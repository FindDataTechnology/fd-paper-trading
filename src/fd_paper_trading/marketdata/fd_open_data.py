"""fd-open-data market-data provider.

Serves real daily bars and close-derived quotes from the fd-open-data-mcp
ontology (akshare/yfinance/etc. under the hood, with a read-through cache).

Scope: daily bars only. Quotes derive from the latest close with a ±5bps
spread ($0.01 floor), mirroring the Polygon free-tier provider — so the whole
app trades in one price world: the last completed trading day.

The fd-open-data library is synchronous (per-date dispatch + cache); all
library calls are wrapped in ``asyncio.to_thread`` so the event loop is never
blocked by a network fetch.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from fd_open_data_mcp.db import get_database
from fd_open_data_mcp.fetch.cache import read_cache_range
from fd_open_data_mcp.fetch.dispatch import read_range
from fd_open_data_mcp.fetch.gapfill import fingerprint as _fingerprint
from fd_open_data_mcp.models import Concept, Entity

from .provider import Bar, InstrumentSearchResult, MarketDataProvider, Quote
from .simulated import SIM_VENUE, SimulatedMarketDataProvider

logger = logging.getLogger(__name__)

# How far back ``get_latest_close`` looks for a recent bar, and the maximum
# history window served (matches the default group's max_backtest_days).
_QUOTE_LOOKBACK_DAYS = 30
_MAX_WINDOW_DAYS = 730

# Quote derivation: half-spread as a fraction of price (5 bps each side),
# with a $0.01 floor so bid != ask even for very low-priced stocks.
_HALF_SPREAD_BPS = Decimal("0.0005")
_MIN_HALF_SPREAD = Decimal("0.01")
_PRICE_Q = Decimal("0.01")  # equity price precision (cents)


def _normalize_date(value: object) -> str:
    """Coerce date/datetime/'YYYY-MM-DD'/'YYYYMMDD' → 'YYYY-MM-DD'."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")
    s = str(value).strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


def _date_to_ts(d: str) -> int:
    """Convert 'YYYY-MM-DD' to a unix timestamp (midnight UTC)."""
    dt = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


class FdOpenDataMarketDataProvider(MarketDataProvider):
    """Daily bars + close-derived quotes from the fd-open-data ontology.

    The concept family it reads — (concept code, entity type, frequency) — is
    configuration, defaulting to the live index-price family. A deprecated
    concept yields a data gap (empty series), never an error.

    Daily only by construction: the ontology stores one point per trading day,
    so there is no sub-daily series to serve and no synthesis that would not be
    invented data. Declaring it here lets the bars endpoint answer 400 for a
    minute-bar request instead of letting ``get_bars`` raise a bare ValueError
    that surfaces as a 500.
    """

    supported_timeframes: tuple[str, ...] = ("1d",)

    def __init__(
        self,
        concept_code: str | None = None,
        entity_type: str | None = None,
        frequency: str | None = None,
    ) -> None:
        from ..config import get_settings

        settings = get_settings()
        self._concept_code = concept_code or settings.fd_open_data_concept
        self._entity_type = entity_type or settings.fd_open_data_entity_type
        self._frequency = frequency or settings.fd_open_data_frequency
        # Instruments and the search fallback share the SIM equity universe,
        # same as the Polygon provider.
        self._sim = SimulatedMarketDataProvider()

    # ---- public interface ----

    async def get_daily_bars(
        self, symbol: str, start: date | str, end: date | str
    ) -> list[Bar]:
        """Daily bars for ``symbol`` over [start, end], oldest-first.

        Returns an empty list when the symbol is unknown to the ontology or no
        data covers the window (a data gap).
        """
        start_s, end_s = _normalize_date(start), _normalize_date(end)
        if start_s > end_s:
            start_s, end_s = end_s, start_s
        closes = await asyncio.to_thread(self._fetch_closes_sync, symbol, start_s, end_s)
        bars: list[Bar] = []
        for d, close in closes:
            bars.append(
                Bar(
                    ts=_date_to_ts(d),
                    # The ontology's daily price concept is close-only; OHL
                    # collapse to the close so limit-crossing logic stays sane.
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=Decimal("0"),
                )
            )
        return bars

    async def get_bars_with_coverage(self, symbol: str, start: date | str,
                                     end: date | str) -> dict:
        """Bars **plus** the window's coverage facts (optional capability).

        This is what the backtest coverage gate consumes. It is deliberately a
        separate method rather than a change to ``get_daily_bars``'s signature:
        coverage is a fact only a real data source can report, and the other
        providers (simulated / polygon-fallback) must not be asked to invent one
        — a synthetic series that answered "complete" would be the gate vouching
        for data nobody checked.

        The ``missing`` vs ``market_closed`` split is produced by the DAAS layer
        (``fd_open_data_mcp.fetch.gapfill``) and consumed here verbatim —
        re-deriving it in this repo would mean a second copy of the
        trading-calendar rules, which is exactly what the upstream capability
        exists to prevent.

        One probe happens as part of this call: missing intervals are asked for
        once upstream so genuine holidays end up in ``market_closed`` instead of
        failing every window that contains one. The probe never writes price
        rows — it only records "asked, empty" — so a coverage check cannot
        mutate the data it is checking.
        """
        start_s, end_s = _normalize_date(start), _normalize_date(end)
        if start_s > end_s:
            start_s, end_s = end_s, start_s
        result = await asyncio.to_thread(
            self._coverage_sync, symbol, start_s, end_s
        )
        bars: list[Bar] = []
        for d, close in result["closes"]:
            bars.append(
                Bar(
                    ts=_date_to_ts(d),
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=Decimal("0"),
                )
            )
        return {**result, "bars": bars}

    def _coverage_sync(self, symbol: str, start: str, end: str) -> dict:
        """Sync half of ``get_bars_with_coverage`` (runs in a worker thread)."""
        session = get_database().get_session()
        try:
            concept = self._resolve_concept(session)
            if concept is None:
                # No live concept means we cannot even judge coverage; report
                # the whole window missing rather than inventing an answer.
                logger.warning(
                    "fd-open-data: concept %s (%s/%s) not available; coverage unknown",
                    self._concept_code, self._entity_type, self._frequency,
                )
                return {
                    "closes": [],
                    "missing": [{"start": start, "end": end}],
                    "market_closed": [],
                    "fill_failed": False,
                    "fingerprint": "",
                }
            entity_id = self._resolve_entity_id(session, symbol)
            if entity_id is None:
                # An unknown symbol has no series at all: everything is missing.
                return {
                    "closes": [],
                    "missing": [{"start": start, "end": end}],
                    "market_closed": [],
                    "fill_failed": False,
                    "fingerprint": "",
                }
            from fd_open_data_mcp.fetch.gapfill import fill_gaps, is_fillable
            from fd_open_data_mcp.fetch.gapfill import series_dates as _series_dates
            from fd_open_data_mcp.fetch.gapfill import (
                known_empty_intervals as _known_empty_intervals,
                probe_interval as _probe_interval,
                record_empty_interval as _record_empty_interval,
            )
            from fd_open_data_mcp.fetch.coverage import coverage as _coverage

            if not is_fillable(concept):
                # Non-fillable concepts (nav.*, macro.*, …) are judged, not
                # filled: fill_gaps deliberately refuses to fetch them, so its
                # whole-window-missing answer there is a refusal notice, not a
                # coverage verdict.
                #
                # But "not filled" must not mean "not probed". Without a probe
                # the coverage judgment has no way to tell a market holiday from
                # a hole, so every window spanning a long holiday reads as
                # missing — and it can never be repaired, because there is
                # genuinely no data there. This branch therefore asks upstream
                # once per gap and records the ones it proves empty; those come
                # back as market_closed, and the rest stay missing (proving
                # nothing means staying unproven, not becoming "complete").
                dates = _series_dates(
                    session, concept.id, self._entity_type, entity_id
                )
                report = _coverage(dates, start, end)
                for gap_start, gap_end in report.fetchable:
                    try:
                        series, used_source = _probe_interval(
                            session, concept, self._entity_type, entity_id,
                            gap_start, gap_end,
                        )
                    except Exception as exc:  # noqa: BLE001 - one bad interval must not kill the rest
                        logger.warning(
                            "fd-open-data: probe failed for [%s..%s]: %s",
                            gap_start, gap_end, exc,
                        )
                        continue
                    if not series and used_source:
                        _record_empty_interval(
                            session, concept.id, self._entity_type, entity_id,
                            gap_start, gap_end,
                        )
                if report.gaps:
                    report = _coverage(
                        dates, start, end,
                        known_empty=_known_empty_intervals(
                            session, concept.id, self._entity_type, entity_id
                        ),
                    )
                rows = read_cache_range(
                    session, concept.id, self._entity_type, entity_id, start, end
                )
                closes = [
                    (r.date, Decimal(str(r.value)))
                    for r in rows
                    if r.value is not None
                ]
                return {
                    "closes": closes,
                    # ``gaps`` keeps known-empty intervals in it by design (it
                    # reports the union); the split into missing vs closed is
                    # the caller's job. Reporting the raw list would put the
                    # holiday back into missing and undo the whole point.
                    "missing": [
                        {"start": g[0], "end": g[1]}
                        for g in report.gaps
                        if g not in set(report.known_empty)
                    ],
                    "market_closed": [
                        {"start": k[0], "end": k[1]} for k in report.known_empty
                    ],
                    "fill_failed": False,
                    "fingerprint": _fingerprint(closes),
                }
            out = fill_gaps(
                session, concept.id, self._entity_type, entity_id, start, end
            )
            closes = [
                (p["date"], Decimal(str(p["value"])))
                for p in out.get("points", [])
                if p.get("value") is not None
            ]
            return {
                "closes": closes,
                "missing": out.get("missing", []),
                "market_closed": out.get("market_closed", []),
                "fill_failed": out.get("fill_failed", False),
                "fingerprint": out.get("fingerprint", ""),
            }
        finally:
            session.close()

    async def get_latest_close(self, symbol: str) -> Decimal | None:
        """Most recent close within the lookback window; None on a data gap."""
        end = date.today()
        start = end - timedelta(days=_QUOTE_LOOKBACK_DAYS)
        closes = await asyncio.to_thread(
            self._fetch_closes_sync, symbol, start.isoformat(), end.isoformat()
        )
        if not closes:
            return None
        return closes[-1][1]

    async def get_quote(self, symbol: str) -> Quote | None:
        """Quote derived from the latest close ±5 bps; None on a data gap."""
        try:
            close = await self.get_latest_close(symbol)
        except Exception:
            logger.exception("get_quote: error fetching close for %s", symbol)
            return None
        if close is None or close <= 0:
            return None
        half = (close * _HALF_SPREAD_BPS).quantize(_PRICE_Q)
        if half < _MIN_HALF_SPREAD:
            half = _MIN_HALF_SPREAD
        return Quote(
            instrument_id=f"{symbol}.{SIM_VENUE}",
            bid=(close - half).quantize(_PRICE_Q),
            ask=(close + half).quantize(_PRICE_Q),
        )

    def get_instrument(self, symbol: str):
        # Same SIM equity universe as the simulated/Polygon providers.
        return self._sim.get_instrument(symbol)

    async def search_instruments(self, q: str) -> list[InstrumentSearchResult]:
        """Search the ontology's stock entities; fall back to the SIM universe."""
        needle = (q or "").strip()
        if not needle:
            return []
        hits = await asyncio.to_thread(self._search_entities_sync, needle)
        if hits:
            return hits
        return await self._sim.search_instruments(q)

    async def get_bars(self, symbol: str, timeframe: str, limit: int) -> list[Bar]:
        """Daily bars only; any other timeframe is rejected."""
        if timeframe != "1d":
            raise ValueError(f"fd-open-data provider supports daily bars only, got {timeframe!r}")
        limit = min(max(1, limit), _MAX_WINDOW_DAYS)
        end = date.today()
        start = end - timedelta(days=limit)
        bars = await self.get_daily_bars(symbol, start, end)
        return bars[-limit:]

    # ---- sync internals (run via asyncio.to_thread) ----

    def _fetch_closes_sync(
        self, symbol: str, start: str, end: str
    ) -> list[tuple[str, Decimal]]:
        """(date, close) series for a symbol via read_range; [] on any gap."""
        session = get_database().get_session()
        try:
            concept = self._resolve_concept(session)
            if concept is None:
                # A deprecated or unseeded concept is a data gap, not an error.
                logger.warning(
                    "fd-open-data: concept %s (%s/%s) not available live",
                    self._concept_code, self._entity_type, self._frequency,
                )
                return []
            entity_id = self._resolve_entity_id(session, symbol)
            if entity_id is None:
                return []
            result = read_range(
                session, [concept.id], self._entity_type, entity_id, start, end
            )
            rows = result.get(concept.id) or []
            out: list[tuple[str, Decimal]] = []
            for row in rows:
                value = row.get("value")
                if value is None:
                    continue
                out.append((row["date"], Decimal(str(value))))
            return out
        finally:
            session.close()

    def _search_entities_sync(self, q: str) -> list[InstrumentSearchResult]:
        session = get_database().get_session()
        try:
            like = f"%{q}%"
            res = session.execute(
                select(Entity)
                .where(
                    Entity.entity_type == self._entity_type,
                    Entity.code.ilike(like) | Entity.name_en.ilike(like),
                )
                .limit(20)
            )
            return [
                InstrumentSearchResult(
                    symbol=e.code, name=e.name_en or e.code, venue=SIM_VENUE
                )
                for e in res.scalars().all()
            ]
        finally:
            session.close()

    def _resolve_concept(self, session) -> Concept | None:
        """The configured family's concept (prefer verified, non-deprecated).

        When several concepts match (e.g. the same code registered across
        sources), exactly one is selected deterministically — verified first,
        then lowest id — and only that one is read.
        """
        res = session.execute(
            select(Concept)
            .where(
                Concept.code == self._concept_code,
                Concept.entity_type == self._entity_type,
                Concept.frequency == self._frequency,
                Concept.deprecated.is_(False),
            )
            .order_by(Concept.verified.desc(), Concept.id.asc())
        )
        return res.scalars().first()

    def _resolve_entity_id(self, session, symbol: str) -> int | None:
        """Map a ticker to the ontology's internal entity id (int PK)."""
        res = session.execute(
            select(Entity).where(
                Entity.entity_type == self._entity_type,
                Entity.code == symbol.upper(),
            )
        )
        entity = res.scalars().first()
        return entity.id if entity is not None else None
