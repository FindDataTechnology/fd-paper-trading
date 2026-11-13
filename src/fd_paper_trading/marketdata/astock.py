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
- D4: coverage carries the **declared** ``adjusted`` caliber — ``declarations.yaml``
  is the single source of truth (`8ada860`); for ``astock_daily`` it declares
  ``qfq``, and the module constant ``_ADJUSTED`` is only the no-registry fallback.
  Alongside it: the standard
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

# The pinned ``source_used`` is NOT defaulted here any more: the source arrives
# from the price-source registry (fd-open-data-mcp ``price_sources``) via
# ``marketdata.selection``, so the provider no longer holds a copy of a source
# name. A provider constructed without a source is a programming error, not a
# default (add-price-supply-and-caliber task 3.3).

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
        source: str | None = None,
        calendar_refs: str = DEFAULT_CALENDAR_REFS,
        registry_source_id: str | None = None,
    ):
        if not source:
            raise ValueError(
                "astock provider requires an explicit pinned source (resolved "
                "from the price-source registry); it holds no default source "
                "name"
            )
        if fetcher is None:
            if not dsn:
                raise ValueError(
                    "astock provider needs a read-only DSN (ASTOCK_DSN); "
                    "no implicit fallback to another provider"
                )
            fetcher = _AsyncpgReader(dsn)
        self._fetch = fetcher
        self._source = source
        # 注册表 id（与 ``source`` 的 db_source 不是同一个东西：前者是数据身份，
        # 后者是联邦视图里钉住的 ``source_used``）。只有它能让本 provider 问到
        # 「这个源的水位到哪一天」——见 ``source_watermark``。
        self._registry_source_id = registry_source_id
        # 源声明缓存：口径与锚点只该有一个真源（declarations.yaml），
        # 不该在 provider 里另写一份常量。
        self._decl_loaded = False
        self._decl: object | None = None
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

    # ---- source watermark（能力方法，非必需接口）----

    async def source_watermark(self) -> dict | None:
        """本 provider 所钉源的实时水位（change1 task 6.5）。
        形状是 ``Watermark.to_dict()``：``{source_id, as_of, status, method,
        basket, lookback_days, detail}``。``as_of`` 为 ``None`` 时 ``status``
        说明原因（``no_data`` / ``unreachable``）。

        为什么要有它：判据探针原先用**固定 400 天盲窗**去躲开上游数据滞后
        （astock_daily 只到 2026-07-10，同日探针取不到真实 bar）。水位把这个
        偏移变成了查得到的事实——拿水位当窗口右端，窗口就能收回到正常宽度。

        **问不到就返回 None**，不返回 ``as_of=None`` 的假水位：调用方据此退回
        固定窗口（卸载前的行为），而不是把"注册表包没装"当成"这个源没数据"。
        """
        if not self._registry_source_id:
            return None
        try:
            mark = await asyncio.to_thread(self._registry_watermark)
        except Exception:  # noqa: BLE001 - 水位是尽力而为，绝不影响交易路径
            logger.warning(
                "source_watermark(%s) unavailable; falling back to the provider's own max date",
                self._registry_source_id,
                exc_info=True,
            )
            mark = None

        if mark and mark.get("status") == "ok" and mark.get("as_of"):
            return mark

        # ── 注册表给不出 → 用本 provider 自己的数据自证（2026-11-10 实测的必要性）──
        #
        # `watermark_for` 查的是**本体库**（get_database() → fd_open_data），而
        # federated_view 源的价格行在**分片库**里。实测生产：`astock_daily` 的水位恒为
        # `status=no_data`、detail「no observations since 2025-10-06」——本体库自那时起
        # 没有价格观测，于是水位锚点在产永远不会启用（只剩 400 天兜底）。
        #
        # 这里退回"问自己的库最后一根 bar 是哪天"：同一个源、同一批行，只是换了条通路。
        # 结果带 `method`/`via` 标明来路，消费方（以及事后复盘）能分清"注册表水位"
        # 与"provider 自证水位"——两者不该长得一样。
        try:
            as_of = await self._max_bar_date()
        except Exception:  # noqa: BLE001
            logger.warning("source_watermark(%s): provider fallback failed",
                           self._registry_source_id, exc_info=True)
            return mark  # 保留注册表那份（可能是 no_data/unreachable，语义仍正确）
        if not as_of:
            return mark
        reason = (mark or {}).get("status") or "registry_unavailable"
        return {
            "source_id": self._registry_source_id,
            "as_of": as_of,
            "status": "ok",
            "method": "provider_max_date",
            "basket": [],
            "lookback_days": 0,
            "detail": f"registry watermark said {reason}; derived from the pinned source itself",
        }

    _REFERENCE_FOR_MAX_DATE = "600036"   # 与声明里的 reference_basket 主标的同码

    async def _max_bar_date(self) -> str | None:
        entity = await self._resolve_entity(self._REFERENCE_FOR_MAX_DATE)
        if entity is None:
            return None
        eid, etype = entity
        concepts = await self._concepts(etype)
        close_id = concepts.get("close")
        if close_id is None:
            return None
        rows = await self._fetch(
            "select max(date) as d from semantic_observations_all "
            "where entity_id = $1 and concept_id = $2 and granularity = 'day' "
            "and source_used = $3",
            eid, close_id, self._source,
        )
        if not rows:
            return None
        d = rows[0].get("d")
        return _date_str(d) if d is not None else None

    def _registry_watermark(self) -> dict:
        """注册表水位（阻塞调用：SQLAlchemy 同步会话，故由 `asyncio.to_thread` 包起来）。"""
        from fd_open_data_mcp.price_sources.registry import (  # type: ignore[import-not-found]
            watermark_for,
        )

        return watermark_for(self._registry_source_id).to_dict()

    def _declaration(self):
        """本 provider 对应源的声明（缓存；拿不到返回 None）。

        口径与锚点以 `declarations.yaml` 为唯一真源：本文件的 ``_ADJUSTED``
        只是"没注册表时的已知事实"，不是可以与声明并存的第二份口径。
        """
        if self._decl_loaded:
            return self._decl
        self._decl_loaded = True
        if not self._registry_source_id:
            return None
        try:
            from fd_open_data_mcp.price_sources.registry import (  # type: ignore[import-not-found]
                load_registry,
            )

            self._decl = load_registry().get(self._registry_source_id)
        except Exception:  # noqa: BLE001 - 声明拿不到就不出 ref，不猜
            logger.warning(
                "declaration(%s) unavailable; series refs will be withheld",
                self._registry_source_id,
                exc_info=True,
            )
            self._decl = None
        return self._decl

    def _declared_adjustment(self) -> str:
        """本源的复权口径：有声明用声明，没有才退回已知常量。"""
        decl = self._declaration()
        declared = getattr(decl, "adjustment", None) if decl is not None else None
        return declared or _ADJUSTED

    def series_ref_for_bars(self, symbol: str, bars) -> dict | None:
        """行情接口的序列引用（cheap 版，change1 需求「每个价格响应都带引用」）。

        与覆盖路径的 ref 同一套字段、同一个指纹函数，但**不做补洞探测**：
        行情端点只回答"这串 bar 是哪来的"，不需要证明窗口完整。窗口就是"实际
        返回的这一段"（首末 bar 的日期），``row_count`` 是实际行数——不假装是
        请求区间。

        没有 bar 就没有 as_of_max，也就没有 ref；返回 ``None`` 而不是空对象。
        """
        if not bars:
            return None
        from .series_ref import build_series_ref

        decl = self._declaration()
        adjusted = getattr(decl, "adjustment", None) if decl is not None else _ADJUSTED
        dates = sorted(datetime.fromtimestamp(b.ts, tz=timezone.utc).date() for b in bars)
        ref, _reason = build_series_ref(
            source_id=self._registry_source_id,
            symbol=symbol,
            adjusted=adjusted,
            as_of_max=dates[-1].isoformat(),
            window_start=dates[0].isoformat(),
            window_end=dates[-1].isoformat(),
            row_count=len(bars),
            adjustment_anchor=(getattr(decl, "adjustment_anchor", None)
                               if decl is not None else None),
            db_source=(getattr(decl, "db_source", None) if decl is not None
                       else self._source),
        )
        return ref

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
            # 空序列没有 as_of_max，也就没有可断言的 ref：说明原因，不给空对象。
            "series_ref_unavailable": "no_as_of_max",
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

        out = {
            "bars": bars,
            "closes": close_series,
            "missing": missing,
            "market_closed": market_closed,
            "fill_failed": fill_failed,
            "fingerprint": _fingerprint(close_series),
            # 口径以声明为真源：`_ADJUSTED` 只是"没有注册表时已知的事实"。
            # 两者若各说各话，run.params["adjusted"] 与 price_series_ref.adjustment
            # 会互相矛盾，而下游没法判断该信哪个。
            "adjusted": self._declared_adjustment(),
            "source": self._source,
        }
        out.update(self._series_ref_facts(symbol, close_series, start_s, end_s))
        return out

    def _series_ref_facts(self, symbol: str, close_series, start_s: str,
                          end_s: str) -> dict:
        """附上 `PriceSeriesRef` 或"为什么没有"（change1 task 4.3）。

        口径与锚点取自声明（唯一真源）；``as_of_max`` 取本窗口内最后一个交易日，
        与水位同义——它就是"这批数据右端到哪天"。

        出不来时给**原因**，不给空对象：``series_ref_unavailable`` 与
        ``series_ref`` 恒不同时出现，调用方据此区分"没能力出"和"出了"。
        """
        from .series_ref import build_series_ref

        decl = self._declaration()
        adjusted = getattr(decl, "adjustment", None) if decl is not None else _ADJUSTED
        ref, reason = build_series_ref(
            source_id=self._registry_source_id,
            symbol=symbol,
            adjusted=adjusted,
            # close_series 按日期升序，最后一个就是窗口内的水位
            as_of_max=close_series[-1][0] if close_series else None,
            window_start=start_s,
            window_end=end_s,
            row_count=len(close_series),
            adjustment_anchor=(getattr(decl, "adjustment_anchor", None)
                               if decl is not None else None),
            db_source=(getattr(decl, "db_source", None) if decl is not None
                       else self._source),
        )
        if ref is not None:
            return {"series_ref": ref}
        return {"series_ref_unavailable": reason}

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
