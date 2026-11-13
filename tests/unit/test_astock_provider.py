"""Unit tests for the astock market-data provider (spec: astock-market-data).

The provider talks to the platform's federated view through an injected
SELECT-only fetcher; tests hand in a fake reader programmed per SQL shape, so
no real store is needed. The real-store smoke lives in the change reports.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from fd_paper_trading.marketdata import AstockMarketDataProvider, build_market_data_provider
from fd_paper_trading.marketdata.astock import normalize_symbol, venue_for_code

STOCK_CONCEPTS = [
    {"id": 233, "code": "price.open", "verified": False},
    {"id": 234, "code": "price.close", "verified": False},
    {"id": 235, "code": "price.high", "verified": False},
    {"id": 236, "code": "price.low", "verified": False},
    {"id": 237, "code": "price.volume", "verified": False},
]


class FakeReader:
    """Programs responses per SQL shape and records every call."""

    def __init__(self, bars=None, calendar=None, raise_calendar=False,
                 entities=None, refs=None, ceiling=None):
        self.calls = []
        self.bars = bars if bars is not None else []
        # calendar probe: weekday date → number of reference-basket entities
        # that traded that day (None = simulate probe failure).
        self.calendar = calendar
        self.raise_calendar = raise_calendar
        self.refs = refs  # None → default basket ids
        self.ceiling = ceiling  # source coverage ceiling (max basket date)
        # code → candidate entity rows (stock/index); empty = unknown symbol.
        self.entities = entities if entities is not None else {
            "600036": [{"id": 3087, "entity_type": "stock"}],
            "600519": [{"id": 58, "entity_type": "stock"}],
        }
        # entity ids that have rows in the pinned family (probe hits).
        self.entities_with_data = {3087, 58}

    async def __call__(self, sql, *args):
        self.calls.append((sql, args))
        s = " ".join(sql.split())
        if s.startswith("select id, code, verified from concepts"):
            return list(STOCK_CONCEPTS) if args[0] == "stock" else []
        if s.startswith("select id, code from entities"):
            # calendar reference basket lookup
            if self.refs is None:
                return [{"id": 3084, "code": "600000"}, {"id": 3087, "code": "600036"}]
            return [{"id": i, "code": c} for c, i in self.refs.items()]
        if s.startswith("select id, entity_type from entities"):
            return list(self.entities.get(args[0], []))
        if s.startswith("select max(date) as d from semantic_observations_all"):
            # 自证水位那条：返回本源最后一根 bar 的日期（夹具里就是 bars 的最大日期）
            dates = sorted(str(r["date"])[:10] for r in self.bars) if self.bars else []
            return [{"d": dates[-1]}] if dates else [{"d": None}]
        if s.startswith("select 1 as x from semantic_observations_all"):
            return [{"x": 1}] if args[0] in self.entities_with_data else []
        if s.startswith("select date, max(case when concept_id"):
            return list(self.bars)
        if s.startswith("select date, count(distinct entity_id) as n"):
            if self.raise_calendar:
                raise RuntimeError("store down")
            if self.calendar is None:
                # default: every probed weekday counts as an open market day
                # (≥ _MIN_OPEN_REFS refs traded)
                return [{"date": date.fromisoformat(d), "n": 4} for d in args[3]]
            return [{"date": date.fromisoformat(d), "n": n}
                    for d, n in self.calendar.items()]
        if s.startswith("select max(date) as m"):
            return [{"m": date.fromisoformat(self.ceiling or "2026-07-10")}]
        if s.startswith("select code, entity_type, coalesce"):
            return [
                {"code": "600036", "entity_type": "stock", "name": "招商银行"},
                {"code": "600030", "entity_type": "stock", "name": "中信证券"},
            ]
        raise AssertionError(f"unexpected SQL in fake: {s[:80]}")

    def sql_for(self, prefix):
        return [c for c in self.calls if " ".join(c[0].split()).startswith(prefix)]


def _bar_row(d, o, h, low, c, vol=1000):
    return {"date": date.fromisoformat(d), "open": str(o), "high": str(h),
            "low": str(low), "close": str(c), "volume": str(vol)}


def _recent(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).isoformat()


def _provider(reader=None, **kw):
    # the provider no longer defaults its pinned source (task 3.3); tests name it
    kw.setdefault("source", "astock_daily")
    return AstockMarketDataProvider(fetcher=reader or FakeReader(), **kw)


# ---- normalization / venue ----

def test_normalize_accepts_bare_and_suffixed():
    assert normalize_symbol("600519") == ("600519", None)
    assert normalize_symbol("600519.SH") == ("600519", "SH")
    assert normalize_symbol("000300.ss") == ("000300", "SS")
    assert normalize_symbol(" AAPL ") is None
    assert normalize_symbol("12345") is None


def test_venue_mapping():
    assert venue_for_code("600519") == "XSHG"
    assert venue_for_code("000001") == "XSHE"
    assert venue_for_code("300750") == "XSHE"
    assert venue_for_code("688981") == "XSHG"


# ---- bars ----

async def test_daily_bars_pivot_with_real_ohlc():
    rows = [_bar_row("2026-07-09", 37.0, 37.8, 36.7, 37.55, 200000),
            _bar_row("2026-07-10", 37.55, 38.1, 37.2, 37.94)]
    reader = FakeReader(bars=rows)
    p = _provider(reader)
    bars = await p.get_daily_bars("600036", "2026-07-01", "2026-07-31")
    assert len(bars) == 2
    assert bars[0].open == Decimal("37.0") and bars[0].high == Decimal("37.8")
    assert bars[1].close == Decimal("37.94")
    assert bars[0].volume == Decimal("200000")
    # The source pin is bound into every pivoting read: one source only.
    pivot = reader.sql_for("select date, max(case")
    assert "source_used = $8" in pivot[0][0]
    assert "astock_daily" in pivot[0][1]


async def test_bars_suffixed_symbol_resolves_same_entity():
    rows = [_bar_row("2026-07-09", 37, 38, 36, 37.5)]
    reader = FakeReader(bars=rows)
    p = _provider(reader)
    a = await p.get_daily_bars("600519", "2026-07-01", "2026-07-31")
    b = await p.get_daily_bars("600519.SH", "2026-07-01", "2026-07-31")
    assert len(a) == len(b) == 1  # dual spelling resolves the same way
    # Same entity id in the pivot args both times.
    ids = [args[5] for _, args in reader.sql_for("select date, max(case")]
    assert ids[0] == ids[1] == 58


async def test_artifact_rows_are_dropped():
    rows = [
        _bar_row("2026-07-09", 37, 36, 35, 35.5),       # high < open → artifact
        _bar_row("2026-07-10", "-1.0", "2", "0.5", "1.8"),  # non-positive → drop
        _bar_row("2026-07-11", 37, 38, 36, 37.5),       # healthy
    ]
    p = _provider(FakeReader(bars=rows))
    bars = await p.get_daily_bars("600036", "2026-07-01", "2026-07-31")
    assert len(bars) == 1
    assert bars[0].ts > 0


async def test_unknown_symbol_is_gap_not_error():
    p = _provider(FakeReader())
    assert await p.get_daily_bars("999999", "2026-01-01", "2026-01-31") == []


async def test_index_symbol_without_rows_is_gap():
    # 000300 index entity exists in the store but carries no pinned rows:
    # reported as a gap (never substituted with a fund or a stock).
    reader = FakeReader(entities={"000300": [{"id": 9676, "entity_type": "index"}]},
                        bars=[])
    p = _provider(reader)
    assert await p.get_daily_bars("000300", "2026-01-01", "2026-06-30") == []


async def test_non_daily_timeframe_rejected():
    p = _provider()
    with pytest.raises(ValueError, match="daily"):
        await p.get_bars("600036", "1m", 50)


async def test_bars_window_cap_and_slice():
    # rows arrive oldest-first (as the store's date-asc SQL returns them)
    rows = [_bar_row(_recent(i), 37, 38, 36, "37.5" if i <= 5 else "36.5")
            for i in range(10, 0, -1)]
    p = _provider(FakeReader(bars=rows))
    bars = await p.get_bars("600036", "1d", 5)
    assert len(bars) == 5
    assert bars[0].ts < bars[-1].ts
    assert all(b.close == Decimal("37.5") for b in bars)


# ---- quotes ----

async def test_quote_derives_from_latest_close():
    rows = [_bar_row(_recent(1), 37, 38, 36, 37.5),
            _bar_row(_recent(0), 37.5, 38, 37, 37.94)]
    p = _provider(FakeReader(bars=rows))
    q = await p.get_quote("600036")
    assert q is not None
    assert q.bid < q.ask and q.bid != q.ask
    # 5 bps half-spread on 37.94: 0.02 floor applies above it
    assert q.ask - q.bid > Decimal("0.02")
    assert q.instrument_id == "600036.XSHG"
    assert q.ts_event > 0


async def test_quote_gap_when_stale():
    # A close older than the 30-day lookback window must not become a quote.
    rows = [_bar_row("2026-07-10", 37, 38, 36, 37.94)]
    reader = FakeReader(bars=[])  # windowed read in provider sees nothing fresh
    p = _provider(reader)
    assert await p.get_quote("600036") is None


async def test_quote_none_on_unknown_symbol():
    p = _provider(FakeReader())
    assert await p.get_quote("999999") is None


def test_get_instrument_syntactic():
    p = _provider()
    inst = p.get_instrument("600036")
    assert inst is not None and inst.symbol_id == "600036.XSHG"
    assert p.get_instrument("000001").symbol_id == "000001.XSHE"
    assert p.get_instrument("ZZZZ") is None
    assert p.get_instrument("600036.SZ").venue == "XSHE"  # explicit suffix wins
    assert p.get_instrument("600036.SH").venue == "XSHG"


async def test_search_returns_stocks_with_names():
    p = _provider()
    hits = await p.search_instruments("6000")
    assert hits and all(h.venue in ("XSHG", "XSHE") for h in hits)
    assert hits[0].symbol == "600036" and hits[0].name == "招商银行"
    assert await p.search_instruments("  ") == []


# ---- coverage (spec: adjusted caliber + calendar from data) ----

async def test_coverage_reports_caliber_and_clean_window():
    rows = [_bar_row("2026-07-08", 37, 38, 36, 37.7),
            _bar_row("2026-07-09", 37, 38, 36, 37.5)]
    reader = FakeReader(bars=rows)
    p = _provider(reader)
    out = await p.get_bars_with_coverage("600036", "2026-07-08", "2026-07-09")
    # 这条 provider 是直接构造的（没有 registry_source_id ⇒ 没有声明），所以
    # 口径**取不到**：按规格报 unknown，而不是拿 provider 侧别名 "forward" 顶替。
    assert out["adjusted"] == "unknown"
    assert out["source"] == "astock_daily"
    assert out["missing"] == [] and out["market_closed"] == []
    assert out["fill_failed"] is False
    assert out["fingerprint"]
    assert [d for d, _ in out["closes"]] == ["2026-07-08", "2026-07-09"]
    # complete series → no calendar probe paid
    assert not reader.sql_for("select date, count(distinct entity_id)")


async def test_coverage_weekend_interval_is_market_closed():
    rows = [_bar_row("2026-07-10", 37, 38, 36, 37.5),
            _bar_row("2026-07-13", 37, 38, 36, 37.7)]  # gap = Sat+Sun
    reader = FakeReader(bars=rows)
    p = _provider(reader)
    out = await p.get_bars_with_coverage("600036", "2026-07-10", "2026-07-13")
    assert out["missing"] == []
    assert out["market_closed"] == [{"start": "2026-07-11", "end": "2026-07-12"}]
    # Weekend-only intervals must not pay for a calendar probe at all.
    assert not reader.sql_for("select date, count(distinct entity_id)")


async def test_coverage_symbol_hole_stays_missing():
    rows = [_bar_row("2026-07-10", 37, 38, 36, 37.5),
            _bar_row("2026-07-14", 37, 38, 36, 37.7)]
    # the basket traded on the weekdays of the interval (calendar=None →
    # every probed weekday counts as open) but this symbol has no row → missing
    p = _provider(FakeReader(bars=rows))
    out = await p.get_bars_with_coverage("600036", "2026-07-10", "2026-07-14")
    assert out["missing"] == [{"start": "2026-07-11", "end": "2026-07-13"}]
    assert out["market_closed"] == []


async def test_coverage_holiday_interval_proven_closed():
    # bars on either side of a week whose weekdays show NO basket trading:
    # proven market closure, not a symbol hole.
    rows = [_bar_row("2024-02-08", 10, 11, 9, 10.5),
            _bar_row("2024-02-19", 10, 11, 9, 10.7)]
    reader = FakeReader(bars=rows, calendar={}, ceiling="2026-07-10")
    p = _provider(reader)
    out = await p.get_bars_with_coverage("600036", "2024-02-08", "2024-02-19")
    assert out["missing"] == []
    assert out["market_closed"] == [{"start": "2024-02-09", "end": "2024-02-18"}]
    assert not reader.raise_calendar


async def test_coverage_beyond_source_ceiling_is_unprovable():
    # A trailing gap after the source's coverage ceiling must stay missing
    # (a missing crawl looks exactly like a holiday — never vouch for it).
    rows = [_bar_row("2026-07-09", 10, 11, 9, 10.5),
            _bar_row("2026-07-10", 10, 11, 9, 10.6)]
    reader = FakeReader(bars=rows, calendar={}, ceiling="2026-07-10")
    p = _provider(reader)
    out = await p.get_bars_with_coverage("600036", "2026-07-10", "2026-07-20")
    assert out["missing"] == [{"start": "2026-07-11", "end": "2026-07-20"}]
    assert out["market_closed"] == []


async def test_coverage_empty_series_never_reads_as_holiday():
    p = _provider(FakeReader(bars=[], entities={"600036": [
        {"id": 3087, "entity_type": "stock"}]}))
    out = await p.get_bars_with_coverage("600036", "2026-01-01", "2026-01-31")
    assert out["missing"] == [{"start": "2026-01-01", "end": "2026-01-31"}]
    assert out["market_closed"] == []


async def test_coverage_calendar_failure_fails_closed():
    rows = [_bar_row("2026-07-10", 37, 38, 36, 37.5),
            _bar_row("2026-07-14", 37, 38, 36, 37.7)]  # gap 11..13: Sat+Sun+Mon
    reader = FakeReader(bars=rows, raise_calendar=True)
    p = _provider(reader)
    out = await p.get_bars_with_coverage("600036", "2026-07-10", "2026-07-14")
    assert out["fill_failed"] is True
    assert out["missing"] == [{"start": "2026-07-11", "end": "2026-07-13"}]
    # intervals containing weekdays are the ones that need the calendar;
    # with the probe broken they must stay missing, never become "closed"
    assert out["market_closed"] == []


async def test_coverage_unknown_symbol_whole_window_missing():
    p = _provider(FakeReader())
    out = await p.get_bars_with_coverage("999999", "2026-01-01", "2026-01-31")
    assert out["missing"] == [{"start": "2026-01-01", "end": "2026-01-31"}]
    assert out["adjusted"] == "unknown"


# ---- provider selection (spec: market-data swappable + startup error) ----

def _settings(source_id: str = "", dsn: str = ""):
    """构造 settings。

    task 6.4 起**只有** ``MARKET_DATA_SOURCE_ID`` 这一个选择器；旧字段
    （market_data_provider / astock_source / astock_calendar_refs）已从 Settings 删除，
    留在这里只会测到不存在的东西（本轮实测：它们全被静默忽略，回落到 simulated）。
    """
    class S:
        market_data_source_id = source_id
        market_data_api_key = ""
        astock_dsn = dsn
    return S()


def test_registry_id_without_dsn_is_startup_error():
    """选了 federated_view 源却没 DSN → 启动失败并点名，不回落 simulated。"""
    with pytest.raises(RuntimeError, match="ASTOCK_DSN"):
        build_market_data_provider(_settings("astock_daily"))


def test_astock_selection_builds_reader_lazily():
    p = build_market_data_provider(_settings("astock_daily", dsn="postgresql://ro@h/db"))
    assert isinstance(p, AstockMarketDataProvider)  # no connection attempted


def test_unset_source_id_selects_simulated():
    p = build_market_data_provider(_settings())
    assert type(p).__name__ == "SimulatedMarketDataProvider"


def test_provider_declares_daily_only():
    p = _provider(FakeReader())
    assert p.supported_timeframes == ("1d",)


# ------------------------------------------------------- 源水位能力（task 6.5）


async def test_source_watermark_is_none_without_registry_source_id():
    """旧路径（MARKET_DATA_PROVIDER=astock，无 MARKET_DATA_SOURCE_ID）不提供水位。

    返回 ``None`` 而不是编一个空水位：调用方据此知道"没能力问"，而不是
    "问过了、没有"。
    """
    p = _provider()
    assert await p.source_watermark() is None


async def test_source_watermark_swallows_registry_failure(monkeypatch):
    """注册表包缺失 / 查询炸掉 → 返回 None，绝不把异常抛进交易路径。"""
    p = _provider(registry_source_id="astock_daily")

    import fd_open_data_mcp.price_sources.registry as reg_mod

    def _boom(_sid, **_kw):
        raise RuntimeError("registry unreachable")

    monkeypatch.setattr(reg_mod, "watermark_for", _boom)
    assert await p.source_watermark() is None


async def test_source_watermark_passes_through_the_registry_answer(monkeypatch):
    """查到什么就报什么，不在 provider 里二次改写水位。"""
    p = _provider(registry_source_id="astock_daily")

    import fd_open_data_mcp.price_sources.registry as reg_mod
    from fd_open_data_mcp.price_sources.watermark import Watermark

    monkeypatch.setattr(reg_mod, "watermark_for", lambda sid, **_kw: Watermark(
        source_id=sid, as_of="2026-07-10", status="ok",
        method="reference_basket", basket=("600036",), lookback_days=400))
    got = await p.source_watermark()
    assert got["source_id"] == "astock_daily"
    assert got["as_of"] == "2026-07-10"
    assert got["status"] == "ok"


# ------------------------------------------------------- 序列出处 ref（task 4.3）


class _Decl:
    """最小声明替身：只带 ref 需要的三个字段。"""

    adjustment = "qfq"
    adjustment_anchor = "2026-07-10"
    db_source = "astock_daily"


@pytest.mark.asyncio
async def test_coverage_attaches_a_series_ref_when_the_source_is_registered():
    """有注册表 id + 声明 → 覆盖结果里带一个能过契约校验的 ref。"""
    from fd_paper_trading._vendor.fd_quant.contracts.models import (
        check_price_series_ref,
    )

    rows = [_bar_row("2026-07-08", 37, 38, 36, 37.7),
            _bar_row("2026-07-09", 37, 38, 36, 37.5)]
    p = _provider(FakeReader(bars=rows), registry_source_id="astock_daily")
    p._decl = _Decl()            # 直接注声明，避免测试依赖注册表文件
    p._decl_loaded = True

    out = await p.get_bars_with_coverage("600036", "2026-07-08", "2026-07-09")

    ref = out["series_ref"]
    assert "series_ref_unavailable" not in out
    assert ref["source_id"] == "astock_daily"
    assert ref["adjustment"] == "qfq"
    assert ref["as_of_max"] == "2026-07-09"      # 窗口内最后一个交易日
    assert ref["adjustment_anchor"] == "2026-07-10"
    assert ref["row_count"] == 2
    assert check_price_series_ref(ref) == []


@pytest.mark.asyncio
async def test_coverage_withholds_the_ref_without_a_registry_id():
    """旧路径（无 MARKET_DATA_SOURCE_ID）→ 不出 ref，但**说明原因**。

    这一条与 6.5 的水位同源：`source_id` 是注册表给的身份，没有它就造不出
    可断言的出处。此时给原因而不是空对象，调用方才能区分"没能力"与"没数据"。
    """
    rows = [_bar_row("2026-07-08", 37, 38, 36, 37.7)]
    p = _provider(FakeReader(bars=rows))     # 无 registry_source_id
    out = await p.get_bars_with_coverage("600036", "2026-07-08", "2026-07-08")
    assert "series_ref" not in out
    assert out["series_ref_unavailable"] == "no_source_id"


@pytest.mark.asyncio
async def test_empty_window_explains_why_there_is_no_ref():
    """空序列没有 as_of_max → 没有可断言的 ref，原因要写出来。"""
    p = _provider(FakeReader(bars=[]), registry_source_id="astock_daily")
    p._decl = _Decl()
    p._decl_loaded = True
    out = await p.get_bars_with_coverage("600036", "2026-07-08", "2026-07-09")
    assert "series_ref" not in out
    assert out["series_ref_unavailable"] == "no_as_of_max"


# ------------------------------------------------- 行情接口的序列引用（需求「每个价格响应都带引用」）


@pytest.mark.asyncio
async def test_series_ref_for_bars_uses_the_returned_window():
    """cheap 版 ref：窗口 = 实际返回的这一段，row_count = 实际行数。"""
    from fd_paper_trading._vendor.fd_quant.contracts.models import (
        check_price_series_ref,
    )

    rows = [_bar_row("2026-07-08", 37, 38, 36, 37.7),
            _bar_row("2026-07-09", 37, 38, 36, 37.5)]
    p = _provider(FakeReader(bars=rows), registry_source_id="astock_daily")
    p._decl = _Decl()
    p._decl_loaded = True
    bars = await p.get_bars("600036", "1d", 2)
    assert bars, "夹具应返回 bar"
    ref = p.series_ref_for_bars("600036", bars)
    assert ref is not None
    assert check_price_series_ref(ref) == []
    assert ref["symbol"] == "600036"
    assert ref["adjustment"] == "qfq"
    assert ref["row_count"] == len(bars)
    # 窗口两端就是首末 bar 的日期（不是"请求了多少天"）
    assert ref["window_start"] <= ref["as_of_max"] == ref["window_end"]


def test_series_ref_for_bars_is_none_without_bars():
    """没有 bar 就没有 as_of_max：返回 None，不编一个空 ref。"""
    p = _provider(FakeReader(), registry_source_id="astock_daily")
    p._decl = _Decl()
    p._decl_loaded = True
    assert p.series_ref_for_bars("600036", []) is None


def test_series_ref_for_bars_is_none_without_registry_identity():
    """旧路径（无注册表 id）→ 不出 ref（与覆盖路径同一判据）。"""
    from fd_paper_trading.marketdata.provider import Bar

    p = _provider(FakeReader())
    bars = [Bar(ts=1751000000, open=1, high=1, low=1, close=1, volume=1)]
    assert p.series_ref_for_bars("600036", bars) is None


@pytest.mark.asyncio
async def test_coverage_caliber_follows_the_declaration_not_the_constant():
    """声明说 hfq，coverage 就得报 hfq——不能被模块常量 "forward" 盖住。

    否则 `run.params["adjusted"]` 与 `price_series_ref.adjustment` 会互相矛盾，
    而下游没有依据判断该信哪个（这正是"三路可比"要防的形态）。
    """
    class _HFQDecl:
        adjustment = "hfq"
        adjustment_anchor = "2026-07-10"
        db_source = "astock_daily"

    rows = [_bar_row("2026-07-08", 37, 38, 36, 37.7)]
    p = _provider(FakeReader(bars=rows), registry_source_id="astock_daily")
    p._decl = _HFQDecl()
    p._decl_loaded = True
    out = await p.get_bars_with_coverage("600036", "2026-07-08", "2026-07-08")
    assert out["adjusted"] == "hfq"
    assert out["series_ref"]["adjustment"] == "hfq"      # 两处一致


@pytest.mark.asyncio
async def test_coverage_caliber_is_unknown_without_a_declaration():
    """没有声明 ⇒ 报 `unknown`，**不**退回模块常量。

    规格 astock-market-data「Indeterminable caliber is stated, not defaulted」：
    「WHEN the provider cannot establish which caliber it read THEN the coverage report
    marks the caliber as **unknown** instead of asserting a value」。

    这条测试原先断言的是反面（`== "forward"`，注释写"退回已知事实，不是编造"）——
    但 `"forward"` 是 provider 侧别名，**不在契约词表** `PRICE_ADJUSTMENTS` 里：
    下游会把它原样记进 `run.params["adjusted"]` 与 `price_caliber`，
    `caliber_for("forward")` 又把它映射成 `qfq`。所以"退回"实际是**两处各自补了一个断言**。
    2026-11-13 按规格翻转：取不到就报 unknown。
    """
    rows = [_bar_row("2026-07-08", 37, 38, 36, 37.7)]
    p = _provider(FakeReader(bars=rows))     # 无注册表 id → 无声明
    out = await p.get_bars_with_coverage("600036", "2026-07-08", "2026-07-08")
    assert out["adjusted"] == "unknown"
    assert out["adjusted"] not in ("forward", "backward")   # 别名不许外泄


@pytest.mark.asyncio
async def test_series_ref_is_withheld_when_the_caliber_is_undeclared():
    """有 source id 但声明拿不到 ⇒ ref **不出**，原因是 `caliber_unknown`。

    这是"取不到就别替声明说话"的第二处：若仍把别名传进 `build_series_ref`，
    `caliber_for()` 会把它映射成 `qfq`，于是 ref 里出现一个没人声明过的口径。
    """
    rows = [_bar_row("2026-07-08", 37, 38, 36, 37.7)]
    p = _provider(FakeReader(bars=rows), registry_source_id="astock_daily")
    p._decl = None            # 显式模拟"声明拿不到"
    p._decl_loaded = True
    out = await p.get_bars_with_coverage("600036", "2026-07-08", "2026-07-08")
    assert out["adjusted"] == "unknown"
    assert "series_ref" not in out
    assert out["series_ref_unavailable"] == "caliber_unknown"


# --------------------------------- 注册表给不出水位时用本源自证（2026-11-10 实测）

@pytest.mark.asyncio
async def test_watermark_falls_back_to_the_sources_own_max_date(monkeypatch):
    """生产实测：`watermark_for` 查的是本体库，federated_view 源的价格行在分片库，
    于是 astock_daily 的水位恒为 no_data（detail「no observations since 2025-10-06」）。
    那时必须用**本源自己的最后一根 bar**自证，否则水位锚点在产永远不启用。
    """
    rows = [_bar_row("2026-07-09", 10, 11, 9, 10.5),
            _bar_row("2026-07-10", 10, 11, 9, 10.6)]
    p = _provider(FakeReader(bars=rows), registry_source_id="astock_daily")
    p._decl = _Decl()
    p._decl_loaded = True

    import fd_open_data_mcp.price_sources.registry as reg_mod
    from fd_open_data_mcp.price_sources.watermark import Watermark
    monkeypatch.setattr(reg_mod, "watermark_for", lambda sid, **_kw: Watermark(
        source_id=sid, as_of=None, status="no_data", method="reference_basket",
        basket=("600036",), lookback_days=400,
        detail="no observations since 2025-10-06"))

    got = await p.source_watermark()
    assert got["status"] == "ok"
    assert got["as_of"] == "2026-07-10"          # 取自本源最后一根 bar
    assert got["method"] == "provider_max_date"  # 来路可分，不冒充注册表水位
    assert "no_data" in got["detail"]


@pytest.mark.asyncio
async def test_watermark_prefers_the_registry_when_it_answers(monkeypatch):
    """注册表能答就用它（三路读同一对象的纪律），不要越位自证。"""
    p = _provider(FakeReader(bars=[]), registry_source_id="astock_daily")
    p._decl = _Decl()
    p._decl_loaded = True

    import fd_open_data_mcp.price_sources.registry as reg_mod
    from fd_open_data_mcp.price_sources.watermark import Watermark
    monkeypatch.setattr(reg_mod, "watermark_for", lambda sid, **_kw: Watermark(
        source_id=sid, as_of="2026-07-10", status="ok", method="reference_basket",
        basket=("600036",), lookback_days=400))

    got = await p.source_watermark()
    assert got["status"] == "ok"
    assert got["method"] == "reference_basket"   # 不是 provider_max_date
