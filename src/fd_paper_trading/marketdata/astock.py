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
  ``qfq``; there is no module-level fallback — an undeclared caliber is reported
  as ``unknown``.
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
from dataclasses import dataclass
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

#: 实体类型 → 价格概念族。注册表为每个族声明**唯一首选源**（D7），
#: 所以"这个实体类型的行该去哪个 ``source_used`` 读"不是 provider 的常量，
#: 而是声明文件里的一行。
#:
#: 这张表只表达**本体事实**（哪个实体类型属于哪个价格族），不表达配置——
#: 换源仍然只改 YAML。
_ENTITY_FAMILY: dict[str, str] = {
    "stock": "price.stock.daily",
    "index": "price.index.daily",
    "fund": "price.fund.daily",
}

#: 本 provider 认的实体类型。``fund`` 是 2026-11-14 加进来的（fund-price-channel）：
#: 场内 ETF/LOF 的日线一直在库里（concept 383-388、``source_used='akshare'``、
#: entity_type='fund'），而此前解析写死 stock/index ⇒ 有价也永远 ``no_quote``。
#: 加它不改变个股读数：个股仍由 pinned 源管辖（见 ``_family_source``）。
_ENTITY_TYPES: tuple[str, ...] = ("stock", "index", "fund")

_SYMBOL_RE = re.compile(r"^(?P<code>\d{6})(?:\.(?P<venue>SH|SS|SZ|SHE))?$", re.I)

#: 声明拿不到时**报什么**。
#:
#: 2026-11-13 改：原先这里退回一个模块常量 ``"forward"``（provider 侧别名），于是
#: 「取不到口径」被写成「口径是 forward」——而 "forward" 根本不是契约词
#: （`PRICE_ADJUSTMENTS` 只有 qfq/hfq/none/not_applicable），下游把它原样记进
#: `run.params["adjusted"]` 与 `price_caliber`，`caliber_for()` 又把它悄悄映射成
#: `qfq`：一次"取不到"被两处各自补成了断言。
#:
#: 规格 astock-market-data「Indeterminable caliber is stated, not defaulted」要求的是
#: 后者：「THEN the coverage report marks the caliber as **unknown** instead of
#: asserting a value」。同文件空序列那条分支（`empty_gap`）本来就用的 ``"unknown"``，
#: 现在两处一致。
_UNKNOWN_ADJUSTMENT = "unknown"

# The pinned ``source_used`` is NOT defaulted here any more: the source arrives
# from the price-source registry (fd-open-data-mcp ``price_sources``) via
# ``marketdata.selection``, so the provider no longer holds a copy of a source
# name. A provider constructed without a source is a programming error, not a
# default (add-price-supply-and-caliber task 3.3).


@dataclass(frozen=True)
class _FamilySource:
    """某个实体类型该读的源：注册表身份、库里的 ``source_used``、以及它的声明。

    三者不能揉成一个字符串：``source_id`` 是数据身份（注册表 key、写进序列引用），
    ``db_source`` 是联邦视图里 ``source_used`` 的物理取值（口径变体或跨族共库时两者
    不同），``declaration`` 是口径/锚点/许可的唯一真源。把它们分开，才可能在
    「基金与个股共用物理 source_used 但口径不同」时各自报对。
    """

    source_id: str | None
    db_source: str
    declaration: object | None

    @property
    def adjustment(self) -> str | None:
        declared = getattr(self.declaration, "adjustment", None)
        return str(declared) if declared else None

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
        # entity_type → _FamilySource | None（None = 该族不可读，fail-closed）。
        self._family_sources: dict[str, _FamilySource | None] = {}
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
        src = self._family_source(etype)
        if src is None:
            return None
        rows = await self._fetch(
            "select max(date) as d from semantic_observations_all "
            "where entity_id = $1 and concept_id = $2 and granularity = 'day' "
            "and source_used = $3",
            eid, close_id, src.db_source,
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

        口径与锚点以 `declarations.yaml` 为唯一真源：本文件
        **不保存**任何口径常量（2026-11-13 删掉了 ``_ADJUSTED``）：取不到就报 unknown。
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

    # ---- 按实体类型解析族与源（fund-price-channel）----

    def _pinned_instrument_types(self) -> tuple[str, ...]:
        """pinned 源在其声明里**声明覆盖**的实体类型。

        取不到声明时按**最窄**的假设走（只认个股）。方向很重要：把"不知道它认什么"
        当成"它什么都认"，会让基金行悄悄落到个股源上去比——那是错的那一侧。
        """
        decl = self._declaration()
        types = getattr(decl, "instrument_types", None) if decl is not None else None
        return tuple(types) if types else ("stock",)

    def _family_source(self, entity_type: str) -> "_FamilySource | None":
        """该实体类型该读哪个源；``None`` = 这个族不可读（fail-closed）。

        规则（design D1/D2/D3）：

        1. pinned 源若在声明里覆盖该实体类型 ⇒ 用 pinned（swap-source 语义不变，
           个股读数与本次改动前逐位一致）。
        2. 否则按 ``entity_type → 价格族 → 注册表首选源`` 解析，用声明的 ``db_source``。
        3. 族**未声明** ⇒ 返回 ``None``：调用方报 data gap，**绝不**退回 pinned 源去
           "试试看"——数据驱动的回落会在"个股在某源没行"时悄悄换源，而换源等于换口径。
        """
        if entity_type in self._family_sources:
            return self._family_sources[entity_type]

        resolved: _FamilySource | None
        if entity_type in self._pinned_instrument_types():
            resolved = _FamilySource(
                source_id=self._registry_source_id,
                db_source=self._source,
                declaration=self._declaration(),
            )
        else:
            resolved = self._registry_family_source(entity_type)
        self._family_sources[entity_type] = resolved
        return resolved

    def _registry_family_source(self, entity_type: str) -> "_FamilySource | None":
        family = _ENTITY_FAMILY.get(entity_type)
        if family is None:
            logger.warning(
                "astock provider: entity_type=%s has no price family mapping",
                entity_type,
            )
            return None
        try:
            from fd_open_data_mcp.price_sources import (  # type: ignore[import-not-found]
                resolve_for_family,
            )

            decl = resolve_for_family(family)
        except Exception as exc:  # noqa: BLE001 - 族未声明/包不在：这是 gap，不是可回落
            logger.warning(
                "astock provider: price family %s unresolved for entity_type=%s (%s); "
                "reporting a gap instead of borrowing another family's source",
                family, entity_type, exc,
            )
            return None
        return _FamilySource(
            source_id=getattr(decl, "source_id", None),
            db_source=getattr(decl, "db_source", None) or getattr(decl, "source_id", ""),
            declaration=decl,
        )

    def _declared_adjustment(self) -> str | None:
        """声明的复权口径；**取不到就是 None**，不拿模块常量顶替。
        返回 None 的两种情形（都属"无法确定"）：没有注册表 id（直接构造），
        或注册表/声明读取失败。调用方据此报 ``unknown``（见 `_UNKNOWN_ADJUSTMENT`），
        而不是断言一个值。
        """
        decl = self._declaration()
        declared = getattr(decl, "adjustment", None) if decl is not None else None
        return str(declared) if declared else None

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

        # 源身份必须与"这串 bar 是从哪读出来的"一致：基金行读了 ``akshare`` 却报
        # ``astock_daily`` 的 ref，就是把口径认错。实体类型从解析缓存取（bar 是刚
        # 从一次取数里出来的，缓存必已填好）；取不到时用 pinned——那是唯一可报的，
        # 且 ``adjusted`` 仍按 pinned 声明，不自造。
        etype = self._entity_type_of(symbol)
        src = self._family_source(etype) if etype else None
        decl = src.declaration if src is not None else self._declaration()
        adjusted = getattr(decl, "adjustment", None) if decl is not None else None
        dates = sorted(datetime.fromtimestamp(b.ts, tz=timezone.utc).date() for b in bars)
        ref, _reason = build_series_ref(
            source_id=(src.source_id if src is not None
                       else self._registry_source_id),
            symbol=symbol,
            adjusted=adjusted,
            as_of_max=dates[-1].isoformat(),
            window_start=dates[0].isoformat(),
            window_end=dates[-1].isoformat(),
            row_count=len(bars),
            adjustment_anchor=(getattr(decl, "adjustment_anchor", None)
                               if decl is not None else None),
            db_source=(src.db_source if src is not None
                       else (getattr(decl, "db_source", None) if decl is not None
                             else self._source)),
        )
        return ref

    def _entity_type_of(self, symbol: str) -> str | None:
        """已解析过的符号的实体类型（未解析/解析不出 ⇒ None）。"""
        norm = normalize_symbol(symbol)
        if norm is None:
            return None
        cached = self._entity_cache.get(norm[0])
        if not cached or cached[0] == -1:
            return None
        return cached[1]

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
        collision is resolved by data existence in the pinned source), then the
        FUND entity (exchange-traded ETFs/LOFs; ``510300`` is a fund and has no
        stock entity, so without this branch it is unresolvable no matter how
        much data the fund family holds). A candidate is taken when it has rows
        **in its own family's source** — probing a fund candidate against the
        stock source would reject every fund in the store.

        A stock that never traded falls through to the index, and an index with
        no rows yet leaves the symbol unresolved (a gap downstream, reported as
        such — never a substitution). Cached per instance: rows only grow, so a
        cached resolution never becomes wrong in the unsafe direction.
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
            "where code = $1 and entity_type = any($2::text[]) "
            "order by case entity_type when 'stock' then 0 "
            "when 'index' then 1 else 2 end, id asc "
            "limit 6",
            code,
            list(_ENTITY_TYPES),
        )
        chosen: tuple[int, str] | None = None
        for r in rows:
            etype = r["entity_type"]
            close_id = (await self._concepts(etype)).get("close")
            if close_id is None:
                # A family we cannot locate in the store cannot be probed;
                # skip the candidate rather than trust it.
                continue
            src = self._family_source(etype)
            if src is None:
                # 族没有可读的源（未声明）：这个候选证明不了自己有数据，
                # 跳过——**不**拿别的族的源去探测它。
                continue
            has = await self._fetch(
                "select 1 as x from semantic_observations_all "
                "where entity_id = $1 and concept_id = $2 "
                "and granularity = 'day' and source_used = $3 limit 1",
                int(r["id"]), close_id, src.db_source,
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
        # 空序列也要报**它本该读哪个源**：基金与个股的源不同，报错一个会让人去查错的源。
        # 实体解析不出来时退回 pinned 源（那是"没解析出来"这一情形下唯一能报的东西），
        # 口径仍然报 unknown（不替声明说话）。
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
        etype = entity[1]
        family_src = self._family_source(etype)
        if family_src is None:
            return empty_gap
        empty_gap["source"] = family_src.db_source
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
            # 口径以**该实体类型所属源**的声明为真源；取不到就报 `unknown`
            # （**不**退回 provider 侧别名）。基金若沿用个股声明，就会把"不复权"
            # 报成 `qfq`——替声明说话，且下游没法判断该信哪个。
            "adjusted": (family_src.adjustment or _UNKNOWN_ADJUSTMENT),
            "source": family_src.db_source,
        }
        out.update(self._series_ref_facts(
            symbol, close_series, start_s, end_s, source=family_src,
        ))
        return out

    def _series_ref_facts(self, symbol: str, close_series, start_s: str,
                          end_s: str, source: "_FamilySource | None" = None) -> dict:
        """附上 `PriceSeriesRef` 或"为什么没有"（change1 task 4.3）。

        口径与锚点取自声明（唯一真源）；``as_of_max`` 取本窗口内最后一个交易日，
        与水位同义——它就是"这批数据右端到哪天"。

        出不来时给**原因**，不给空对象：``series_ref_unavailable`` 与
        ``series_ref`` 恒不同时出现，调用方据此区分"没能力出"和"出了"。
        """
        from .series_ref import build_series_ref

        # 口径/锚点/源身份一律取**该实体类型所属源**的声明；``source=None``
        # （直接构造、未经过族解析）时退回 pinned 声明——那是唯一还能报的东西。
        decl = source.declaration if source is not None else self._declaration()
        # 未声明 ⇒ 传 None：`build_series_ref` 会以 `caliber_unknown` 拒绝出 ref。
        # 传 "forward" 则会被 `caliber_for()` 映射成 `qfq`——那等于替声明说话。
        adjusted = getattr(decl, "adjustment", None) if decl is not None else None
        ref, reason = build_series_ref(
            source_id=(source.source_id if source is not None
                       else self._registry_source_id),
            symbol=symbol,
            adjusted=adjusted,
            # close_series 按日期升序，最后一个就是窗口内的水位
            as_of_max=close_series[-1][0] if close_series else None,
            window_start=start_s,
            window_end=end_s,
            row_count=len(close_series),
            adjustment_anchor=(getattr(decl, "adjustment_anchor", None)
                               if decl is not None else None),
            db_source=(source.db_source if source is not None
                       else (getattr(decl, "db_source", None) if decl is not None
                             else self._source)),
        )
        if ref is not None:
            return {"series_ref": ref}
        return {"series_ref_unavailable": reason}

    async def _query_bars(self, symbol: str, start_s: str, end_s: str, entity=None):
        """One pivot SQL per window: date-asc OHLCV bars from the family's source."""
        entity = entity if entity is not None else await self._resolve_entity(symbol)
        if entity is None:
            return {"bars": [], "close_series": []}
        eid, etype = entity
        concepts = await self._concepts(etype)
        if "close" not in concepts:
            return {"bars": [], "close_series": []}
        src = self._family_source(etype)
        if src is None:
            # 族没有可读的源：空序列（gap）。不拿别的族的源顶上。
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
            eid, ids, src.db_source,
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
        stock_src = self._family_source("stock")
        if stock_src is None:
            raise LookupError("calendar probe: stock family has no readable source")
        rows = await self._fetch(
            "select date, count(distinct entity_id) as n "
            "from semantic_observations_all "
            "where concept_id = $1 and granularity = 'day' and source_used = $2 "
            "and entity_id = any($3::bigint[]) and date = any($4::text[]) "
            "group by date",
            concepts["close"], stock_src.db_source, self._ref_ids, probe_dates,
        )
        counts = {_as_date(r["date"]).isoformat(): int(r["n"]) for r in rows}
        if self._basket_ceiling is None:
            ceil_rows = await self._fetch(
                "select max(date) as m from semantic_observations_all "
                "where concept_id = $1 and granularity = 'day' and source_used = $2 "
                "and entity_id = any($3::bigint[])",
                concepts["close"], stock_src.db_source, self._ref_ids,
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
