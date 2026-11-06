"""A-share daily market-data provider (spec: astock-market-data).

Reads the platform's sharded daily store through the
``semantic_observations_all`` federated view, with the source pinned to
``astock_daily`` — the curated 前复权 (forward-adjusted) A-share daily family.
The pin is unconditional: the view also carries rows under other
``source_used`` values (e.g. ``akshare``) whose adjustment caliber differs,
and mixing calibers inside one series is exactly the defect that produces
"close above the day's high" artifacts (dashboards line, same store). One
series, one source. The provider only ever issues SELECTs.

Design (openspec/changes/astock-price-channel/design.md):

- D1: in-process read-only SQL over the federated view. The public ontology's
  ``price.*`` family stays deprecated by policy and is NOT re-registered or
  re-opened here — this reads stored rows for the service's own consumption.
- D2: symbol normalization accepts ``600036`` and ``600036.SH`` alike; venue
  maps 6/9/5-prefixed codes to XSHG, the rest to XSHE.
- D4: coverage carries ``adjusted="forward"`` alongside the standard
  missing/market_closed/fill_failed/fingerprint contract. The trading
  calendar is derived from the data itself: a date with no ``astock_daily``
  rows market-wide is a day the market produced nothing (weekend/holiday); a
  date WITH market-wide rows but without this symbol's row is a symbol-level
  hole (halt or gap) and stays ``missing``. A calendar that cannot be queried
  proves nothing — gaps fail closed and ``fill_failed`` is set.

Benchmark index codes resolve to the store's ``index`` entities when they
carry rows in the pinned source (the concept family is looked up per entity
type, falling back to the stock family); until such rows exist the provider
reports the window as a gap — never a substitution.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from .provider import Bar, Instrument, InstrumentSearchResult, MarketDataProvider, Quote

logger = logging.getLogger(__name__)

# Same windows as the fd-open-data provider: quotes look a recent close back
# this far, and history requests cap at the default group's max_backtest_days.
_QUOTE_LOOKBACK_DAYS = 30
_MAX_WINDOW_DAYS = 730

# Quote derivation: half-spread 5 bps each side with a $0.01 floor — the same
# derivation as the Polygon / fd-open-data providers, so all providers trade
# in one price world.
_HALF_SPREAD_BPS = Decimal("0.0005")
_MIN_HALF_SPREAD = Decimal("0.01")
_PRICE_Q = Decimal("0.01")

# The concept family this provider reads, by concept code → Bar field.
_CONCEPT_SPECS: tuple[tuple[str, str], ...] = (
    ("price.open", "open"),
    ("price.high", "high"),
    ("price.low", "low"),
    ("price.close", "close"),
    ("price.volume", "volume"),
)

# 6/9/5-prefixed codes trade on the Shanghai exchange; the rest on Shenzhen.
_XSHG_PREFIXES = ("6", "9", "5")

_SYMBOL_RE = re.compile(r"^(?P<code>\d{6})(?:\.(?P<venue>SH|SS|SZ|SHE))?$", re.I)

_ADJUSTED = "forward"

DEFAULT_ASTOCK_SOURCE = "astock_daily"

# Trading-calendar probe: a small basket of dense, full-history blue chips in
# the SAME pinned source. A market-wide calendar scan over the federated view
# costs seconds per day (measured: the 730-day group-by times out), while a
# basket probe rides the entity index — a year of weekday checks answers in
# ~5 s. A weekday where >= _MIN_OPEN_REFS of the basket traded is a proven
# market day; a weekday where (at or below the source's coverage ceiling) none
# of the basket traded is a proven market-closed day. No blue-chip-of-this-
# liquidity halts together, so the false-"closed" risk is the basket's own
# crawl failure — which the coverage ceiling (max date in the basket) bounds:
# beyond it nothing is provable and gaps fail closed.
DEFAULT_CALENDAR_REFS = "600000,600036,600585,600900,601318"
_MIN_OPEN_REFS = 2


def normalize_symbol(symbol: str) -> tuple[str, str | None] | None:
    """Split ``600519`` / ``600519.SH`` / ``000300.SS`` into (code, suffix).

    Returns ``None`` for anything that is not a 6-digit A-share-style code
    with an optional recognized venue suffix (SH/SS = Shanghai, SZ/SHE =
    Shenzhen). The suffix only carries the requested venue flavor; binding to
    a real entity happens through the store.
    """
    m = _SYMBOL_RE.match((symbol or "").strip())
    if not m:
        return None
    return m.group("code"), (m.group("venue") or "").upper() or None


def venue_for_code(code: str) -> str:
    return "XSHG" if code.startswith(_XSHG_PREFIXES) else "XSHE"


class AstockMarketDataProvider(MarketDataProvider):
    """Real A-share daily bars + close-derived quotes from the sharded store.

    ``fetcher`` is an injectable ``async fetch(sql, *args) -> list[dict]``
    (SELECT-only by convention). Production builds an asyncpg-backed reader
    from the configured DSN; tests hand in a fake. A missing DSN is a
    construction error, never a silent fallback to another provider.
    """

    supported_timeframes: tuple[str, ...] = ("1d",)

    def __init__(
        self,
        dsn: str = "",
        fetcher=None,
        source: str = DEFAULT_ASTOCK_SOURCE,
        calendar_refs: str = DEFAULT_CALENDAR_REFS,
    ):
        if fetcher is None:
            if not dsn:
                raise ValueError(
                    "astock provider needs a read-only DSN (ASTOCK_DSN); "
                    "no implicit fallback to another provider"
                )
            fetcher = _AsyncpgReader(dsn)
        self._fetch = fetcher
        self._source = source
        self._calendar_ref_codes = [
            c.strip() for c in (calendar_refs or "").split(",") if c.strip()
        ]
        # entity_type → {field: concept_id}; a family without price.close
        # resolves to {} (callers then report a gap, they never substitute).
        self._concepts_by_type: dict[str, dict[str, int]] = {}
        # symbol code → (entity_id, entity_type) | -1 sentinel (unresolved).
        self._entity_cache: dict[str, tuple[int, str]] = {}
        self._ref_ids: list[int] | None = None
        self._basket_ceiling: str | None = None
        self._lock = asyncio.Lock()

    # ---- concepts / entity resolution ----

    async def _concepts(self, entity_type: str) -> dict[str, int]:
        """Resolve the concept family (code → id), deterministically.

        Several ids can carry the same (code, entity_type, frequency) — the
        store grew them across eras. One is picked the way the ontology picks
        one: verified first, then lowest id. Deprecated concepts are NOT
        filtered out: ``price.*`` is deprecated *for the public read path* by
        the 2026-10-07 policy, and this provider's purpose is the service-
        internal read of the frozen curated rows — reading stored data is
        not re-opening a concept.
        """
        if entity_type in self._concepts_by_type:
            return self._concepts_by_type[entity_type]
        async with self._lock:
            if entity_type not in self._concepts_by_type:
                rows = await self._fetch(
                    "select id, code, verified from concepts "
                    "where entity_type = $1 and frequency = 'daily' "
                    "and code = any($2::text[]) "
                    "order by verified desc nulls last, id asc",
                    entity_type,
                    [code for code, _ in _CONCEPT_SPECS],
                )
                ids: dict[str, int] = {}
                for r in rows:  # already sorted: first row per code wins
                    for code, name in _CONCEPT_SPECS:
                        if r["code"] == code and name not in ids:
                            ids[name] = int(r["id"])
                            break
                if "close" not in ids:
                    logger.warning(
                        "astock provider: no price.close concept for entity_type=%s",
                        entity_type,
                    )
                    ids = {}
                self._concepts_by_type[entity_type] = ids
        return self._concepts_by_type[entity_type]

    async def _resolve_entity(self, symbol: str) -> tuple[int, str] | None:
        """Map a symbol to (entity_id, entity_type) through the store.

        Preference: the STOCK entity for this code, then the INDEX entity
        (benchmark codes like 000300; 000905 collides with a stock, and the
        collision is resolved by data existence in the pinned source). A
        candidate is taken when it has rows in the pinned family; a stock
        that never traded there falls through to the index, and an index with
        no rows yet leaves the symbol unresolved (a gap downstream, reported
        as such — never a substitution). Cached per instance: rows only grow,
        so a cached resolution never becomes wrong in the unsafe direction.
        """
        norm = normalize_symbol(symbol)
        if norm is None:
            return None
        code = norm[0]
        cached = self._entity_cache.get(code)
        if cached is not None:
            return None if cached[0] == -1 else cached

        rows = await self._fetch(
            "select id, entity_type from entities "
            "where code = $1 and entity_type in ('stock', 'index') "
            "order by case when entity_type = 'stock' then 0 else 1 end, id asc "
            "limit 4",
            code,
        )
        chosen: tuple[int, str] | None = None
        for r in rows:
            etype = r["entity_type"]
            close_id = (await self._concepts(etype)).get("close")
            if close_id is None:
                # A family we cannot locate in the store cannot be probed;
                # skip the candidate rather than trust it.
                continue
            has = await self._fetch(
                "select 1 as x from semantic_observations_all "
                "where entity_id = $1 and concept_id = $2 "
                "and granularity = 'day' and source_used = $3 limit 1",
                int(r["id"]), close_id, self._source,
            )
            if has:
                chosen = (int(r["id"]), etype)
                break
        if chosen is None:
            self._entity_cache[code] = (-1, "")
            return None
        self._entity_cache[code] = chosen
        return chosen

    # ---- bars ----

    async def get_daily_bars(self, symbol: str, start, end) -> list[Bar]:
        start_s, end_s = _date_str(start), _date_str(end)
        if start_s > end_s:
            start_s, end_s = end_s, start_s
        return (await self._query_bars(symbol, start_s, end_s))["bars"]

    async def get_bars_with_coverage(self, symbol: str, start, end) -> dict:
        """Bars plus the window's coverage facts, with the adjustment caliber.

        Consumed by the backtest coverage gate; the dict carries the standard
        keys (closes/missing/market_closed/fill_failed/fingerprint) and
        ``adjusted``/``source`` (spec: coverage reports the caliber; an
        indeterminable caliber is stated as ``unknown``, not defaulted).
        """
        start_s, end_s = _date_str(start), _date_str(end)
        if start_s > end_s:
            start_s, end_s = end_s, start_s
        empty_gap = {
            "bars": [], "closes": [],
            "missing": [{"start": start_s, "end": end_s}],
            "market_closed": [], "fill_failed": False,
            "fingerprint": "", "adjusted": "unknown",
            "source": self._source,
        }
        entity = await self._resolve_entity(symbol)
        if entity is None:
            return empty_gap
        fetched = await self._query_bars(symbol, start_s, end_s, entity=entity)
        bars, close_series = fetched["bars"], fetched["close_series"]
        observed = [d for d, _ in close_series]
        if not observed:
            # An empty series is not "all holidays" — it is not covered.
            # Same rule as the fd-open-data gate: prove nothing, claim nothing.
            return empty_gap

        intervals = self._gap_intervals(observed, start_s, end_s)
        missing: list[dict] = []
        market_closed: list[dict] = []
        # Weekend-only intervals are provable without the store at all.
        need_calendar: list[tuple[str, str]] = []
        for a, b in intervals:
            weekdays = [d for d in _iter_dates(a, b) if _is_weekday(d)]
            if not weekdays:
                market_closed.append({"start": a, "end": b})
            else:
                need_calendar.append((a, b))

        fill_failed = False
        open_counts: dict[str, int] | None = {}
        ceiling: str | None = None
        if need_calendar:
            probe_dates = sorted({
                d for a, b in need_calendar for d in _iter_dates(a, b)
                if _is_weekday(d)
            })
            try:
                open_counts, ceiling = await self._basket_calendar(probe_dates)
            except Exception as exc:  # noqa: BLE001 - a broken probe must fail closed
                logger.warning("astock coverage: calendar probe failed: %s", exc)
                open_counts, ceiling = None, None
                fill_failed = True

        for a, b in need_calendar:
            if open_counts is None:
                # Calendar unreadable: unproven stays missing (gate semantics:
                # "证明不了完整就不当完整").
                missing.append({"start": a, "end": b})
                continue
            provably_closed = True
            for d in _iter_dates(a, b):
                if not _is_weekday(d):
                    continue
                if open_counts.get(d, 0) >= _MIN_OPEN_REFS:
                    provably_closed = False  # market traded; this symbol didn't
                    break
                if ceiling is not None and d > ceiling:
                    provably_closed = False  # beyond source coverage: unprovable
                    break
            if provably_closed:
                market_closed.append({"start": a, "end": b})
            else:
                missing.append({"start": a, "end": b})

        from fd_open_data_mcp.fetch.gapfill import fingerprint as _fingerprint

        return {
            "bars": bars,
            "closes": close_series,
            "missing": missing,
            "market_closed": market_closed,
            "fill_failed": fill_failed,
            "fingerprint": _fingerprint(close_series),
            "adjusted": _ADJUSTED,
            "source": self._source,
        }

    async def _query_bars(self, symbol: str, start_s: str, end_s: str, entity=None):
        """One pivot SQL per window: date-asc OHLCV bars from the pinned source."""
        entity = entity if entity is not None else await self._resolve_entity(symbol)
        if entity is None:
            return {"bars": [], "close_series": []}
        eid, etype = entity
        concepts = await self._concepts(etype)
        if "close" not in concepts:
            return {"bars": [], "close_series": []}
        close_id = concepts["close"]
        open_id = concepts.get("open", close_id)
        high_id = concepts.get("high", close_id)
        low_id = concepts.get("low", close_id)
        vol_id = concepts.get("volume")
        ids = [open_id, high_id, low_id, close_id] + ([vol_id] if vol_id else [])
        rows = await self._fetch(
            "select date, "
            "max(case when concept_id = $1 then value end) as open, "
            "max(case when concept_id = $2 then value end) as high, "
            "max(case when concept_id = $3 then value end) as low, "
            "max(case when concept_id = $4 then value end) as close, "
            "max(case when concept_id = $5 then value end) as volume "
            "from semantic_observations_all "
            "where entity_id = $6 and concept_id = any($7::bigint[]) "
            "and granularity = 'day' and source_used = $8 "
            "and date >= $9 and date <= $10 "
            "group by date order by date asc",
            open_id, high_id, low_id, close_id, vol_id or -1,
            eid, ids, self._source,
            start_s, end_s,
        )
        bars: list[Bar] = []
        close_series: list[tuple[str, Decimal]] = []
        for r in rows:
            o, h = _to_decimal(r.get("open")), _to_decimal(r.get("high"))
            low, c = _to_decimal(r.get("low")), _to_decimal(r.get("close"))
            if None in (o, h, low, c):
                continue  # incomplete row → treated as not traded (a gap, honestly)
            if h < max(o, c, low) or low > min(o, c, h):
                continue  # internally inconsistent → adjustment artifact, excluded
            d = _as_date(r["date"]).isoformat()
            v = _to_decimal(r.get("volume"))
            bars.append(Bar(
                ts=_date_to_ts(d), open=o, high=h, low=low, close=c,
                volume=v if v is not None and v > 0 else Decimal("0"),
            ))
            close_series.append((d, c))
        return {"bars": bars, "close_series": close_series}

    async def _basket_calendar(self, probe_dates: list[str]):
        """Reference-basket trading-day evidence for the given weekdays.

        Returns (date→basket-row-count, coverage ceiling date). The ceiling is
        the latest pinned-source date present for the basket at all — days
        beyond it cannot be proven closed (a missing crawl looks exactly like
        a holiday), so callers must treat them as unproven.
        """
        if self._ref_ids is None:
            if not self._calendar_ref_codes:
                raise LookupError("no calendar reference codes configured")
            rows = await self._fetch(
                "select id, code from entities "
                "where code = any($1::text[]) and entity_type = 'stock'",
                self._calendar_ref_codes,
            )
            if not rows:
                raise LookupError(
                    f"calendar references not in store: {self._calendar_ref_codes}"
                )
            self._ref_ids = sorted(int(r["id"]) for r in rows)
        concepts = await self._concepts("stock")
        if "close" not in concepts:
            raise LookupError("calendar probe: stock price.close concept absent")
        rows = await self._fetch(
            "select date, count(distinct entity_id) as n "
            "from semantic_observations_all "
            "where concept_id = $1 and granularity = 'day' and source_used = $2 "
            "and entity_id = any($3::bigint[]) and date = any($4::text[]) "
            "group by date",
            concepts["close"], self._source, self._ref_ids, probe_dates,
        )
        counts = {_as_date(r["date"]).isoformat(): int(r["n"]) for r in rows}
        if self._basket_ceiling is None:
            ceil_rows = await self._fetch(
                "select max(date) as m from semantic_observations_all "
                "where concept_id = $1 and granularity = 'day' and source_used = $2 "
                "and entity_id = any($3::bigint[])",
                concepts["close"], self._source, self._ref_ids,
            )
            self._basket_ceiling = (
                _as_date(ceil_rows[0]["m"]).isoformat()
                if ceil_rows and ceil_rows[0].get("m") is not None else None
            )
        return counts, self._basket_ceiling

    @staticmethod
    def _gap_intervals(observed: list[str], start_s: str, end_s: str):
        """Maximal unobserved intervals inside [start, end] (inclusive ends)."""
        if not observed:
            return [(start_s, end_s)] if start_s <= end_s else []
        lo, hi = observed[0], observed[-1]
        out: list[tuple[str, str]] = []
        if start_s < lo:
            out.append((start_s, _shift(lo, -1)))
        prev = lo
        for d in observed[1:]:
            if _shift(prev, 1) < d:
                out.append((_shift(prev, 1), _shift(d, -1)))
            prev = d
        if hi < end_s:
            out.append((_shift(hi, 1), end_s))
        return [(a, b) for a, b in out if a <= b]

    # ---- quotes / instruments / search ----

    async def get_latest_close(self, symbol: str) -> Decimal | None:
        end = date.today()
        start = end - timedelta(days=_QUOTE_LOOKBACK_DAYS)
        out = await self._query_bars(symbol, start.isoformat(), end.isoformat())
        return out["bars"][-1].close if out["bars"] else None

    async def get_quote(self, symbol: str) -> Quote | None:
        """Quote derived from the latest close ±5 bps; None on a data gap.

        A store error is surfaced as a gap here (the same handling the
        fd-open-data provider gives its quotes): the order path then rejects
        with the standard missing-quote reason rather than a 500.
        """
        norm = normalize_symbol(symbol)
        if norm is None:
            return None
        code = norm[0]
        end = date.today()
        start = end - timedelta(days=_QUOTE_LOOKBACK_DAYS)
        try:
            out = await self._query_bars(code, start.isoformat(), end.isoformat())
        except Exception:  # noqa: BLE001
            logger.exception("get_quote: store read failed for %s", symbol)
            return None
        if not out["bars"]:
            return None
        last = out["bars"][-1]
        half = (last.close * _HALF_SPREAD_BPS).quantize(_PRICE_Q)
        if half < _MIN_HALF_SPREAD:
            half = _MIN_HALF_SPREAD
        return Quote(
            instrument_id=f"{code}.{venue_for_code(code)}",
            bid=(last.close - half).quantize(_PRICE_Q),
            ask=(last.close + half).quantize(_PRICE_Q),
            ts_event=last.ts,
        )

    def get_instrument(self, symbol: str) -> Instrument | None:
        """Syntactic resolution only.

        The interface is sync and the store is async; data existence is
        enforced downstream the same way the existing providers enforce it —
        ``get_quote`` returns None on a gap and the missing-quote rejection
        refuses the order. A syntactically valid A-share code gets the
        service's own Instrument value (symbol, venue, precision).
        """
        norm = normalize_symbol(symbol)
        if norm is None:
            return None
        code, hint = norm
        venue = {
            "SH": "XSHG", "SS": "XSHG",
            "SZ": "XSHE", "SHE": "XSHE",
        }.get(hint or "", venue_for_code(code))
        return Instrument(
            symbol=code,
            symbol_id=f"{code}.{venue}",
            venue=venue,
            name=code,
            asset_class="equity",
            price_tick=Decimal("0.01"),
            size_increment=Decimal("1"),
        )

    async def search_instruments(self, q: str) -> list[InstrumentSearchResult]:
        needle = (q or "").strip()
        if not needle:
            return []
        norm = normalize_symbol(needle)
        like = f"%{needle}%"
        rows = await self._fetch(
            "select code, entity_type, coalesce(nullif(name_zh, ''), name_en) as name "
            "from entities "
            "where entity_type in ('stock', 'index') "
            "and (code = $1 or code ilike $2 or name_zh ilike $2 or name_en ilike $2) "
            "order by case when code = $1 then 0 else 1 end, code asc "
            "limit 20",
            (norm[0] if norm else needle), like,
        )
        out: list[InstrumentSearchResult] = []
        seen: set[str] = set()
        for r in rows:
            if r["code"] in seen:
                continue
            seen.add(r["code"])
            out.append(InstrumentSearchResult(
                symbol=r["code"],
                name=r.get("name") or r["code"],
                venue=venue_for_code(r["code"]),
            ))
        return out

    async def get_bars(self, symbol: str, timeframe: str, limit: int) -> list[Bar]:
        if timeframe != "1d":
            raise ValueError(f"astock provider serves daily bars only, got {timeframe!r}")
        limit = min(max(1, limit), _MAX_WINDOW_DAYS)
        end = date.today()
        # Trading days ≈ 5/7 of calendar days; pad so `limit` is satisfiable.
        start = end - timedelta(days=math.ceil(limit * 7 / 5) + 30)
        bars = await self.get_daily_bars(symbol, start, end)
        return bars[-limit:]


# ---- value / date helpers ----

def _to_decimal(value: object) -> Decimal | None:
    """Coerce a stored observation value; non-finite or non-positive → None.

    A non-positive "price" in a forward-adjusted family is the signature of
    the additive-adjustment artifact the DAAS line hit (negative closes);
    dropping such rows keeps the series honest (宁缺勿错).
    """
    if value is None:
        return None
    try:
        d = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    if not d.is_finite() or d <= 0:
        return None
    return d


def _as_date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _date_str(value: object) -> str:
    return _as_date(value).isoformat() if value is not None else ""


def _shift(d: str, days: int) -> str:
    return (_as_date(d) + timedelta(days=days)).isoformat()


def _iter_dates(a: str, b: str):
    d = _as_date(a)
    end = _as_date(b)
    while d <= end:
        yield d.isoformat()
        d += timedelta(days=1)


def _is_weekday(d: str) -> bool:
    """True for Mon-Fri. Weekends need no store evidence to be market-closed."""
    return _as_date(d).weekday() < 5


def _date_to_ts(d: str) -> int:
    """Convert 'YYYY-MM-DD' to a unix timestamp (midnight UTC)."""
    dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


class _AsyncpgReader:
    """SELECT-only asyncpg-backed fetcher (lazy pool, created on first read)."""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool = None

    async def __call__(self, sql: str, *args) -> list[dict]:
        if not sql.strip().lower().startswith("select"):
            raise ValueError("astock provider is read-only by contract")
        import asyncpg  # lazy: keeps the sandbox import path cheap

        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        async with self._pool.acquire() as conn:
            return [dict(r) for r in await conn.fetch(sql, *args)]
