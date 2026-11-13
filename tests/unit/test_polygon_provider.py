"""Polygon provider：搜索/元数据走模拟盘，**价格一律走 Polygon 自己的端点**。

这份测试原先钉的是另一件事：`get_bars` 代理给 `SimulatedMarketDataProvider`
（2026-07-17 修 500 时留下的），理由是"免费版没有 bars 端点"。2026-11-13 实测
那个理由不成立——`/v2/aggs/ticker/{t}/range/1/day` 在免费版上是通的
（AAPL 取回 7 根真 bar，`status=OK`）。于是那次"修复"造出了规格
「One price world for backtest, live, and manual fills」明令禁止的形状：
同一个 provider 实例的 quote 来自 Polygon、bars 来自合成随机游走，
回测的信号与成交价分属两个世界，两边都看不出异常。

现在判据分三类：

1. **解析**：真聚合行 → `Bar`，毫秒转秒，desc 转 oldest-first。
2. **不顶替**：403/429/没 key → 空序列（数据缺口），模拟盘一次都不被问到
   （`_SimBoom` 一被调用就炸，所以"没顶替"是可观测的事实，不是注释里的承诺）。
3. **不服务**：非日线抛 ValueError（`supported_timeframes` 也让路由答 400 而非 500）。
"""

import pytest

from fd_paper_trading.marketdata import PolygonMarketDataProvider


class _Resp:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _Client:
    """最小 httpx 替身：记下请求，返回准备好的响应。"""

    def __init__(self, resp: _Resp) -> None:
        self.resp = resp
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url: str, params: dict | None = None) -> _Resp:
        self.calls.append((url, params or {}))
        return self.resp

    async def aclose(self) -> None:  # pragma: no cover - 只为对齐接口
        return None


class _SimBoom:
    """价格方法一旦被代理到模拟盘就炸。"""

    def get_bars(self, *a, **k):  # pragma: no cover - 调用即失败
        raise AssertionError("价格方法被代理给了模拟盘")

    def get_quote(self, *a, **k):  # pragma: no cover
        raise AssertionError("价格方法被代理给了模拟盘")

    def get_instrument(self, symbol):
        raise AssertionError("价格方法被代理给了模拟盘")

    async def search_instruments(self, q):  # pragma: no cover
        return []


_AGG_ROWS = [
    # 本仓请求 sort=desc：最新在前
    {"o": 302.1, "h": 305.0, "l": 300.5, "c": 304.2, "v": 4210, "t": 1783483200000},
    {"o": 299.0, "h": 303.0, "l": 298.2, "c": 302.1, "v": 3987, "t": 1783396800000},
    {"o": 295.5, "h": 299.9, "l": 294.0, "c": 299.0, "v": 5120, "t": 1783310400000},
]


@pytest.fixture
async def provider():
    p = PolygonMarketDataProvider(api_key="fake-key")
    yield p
    await p.aclose()


async def test_search_uses_the_simulated_universe(provider):
    results = await provider.search_instruments("AAP")
    assert any(r.symbol == "AAPL" for r in results)
    assert all(r.venue == "SIM" for r in results)


async def test_search_empty_query_returns_empty(provider):
    assert await provider.search_instruments("") == []


async def test_get_instrument_returns_equity(provider):
    assert provider.get_instrument("AAPL") is not None


async def test_get_instrument_returns_none_for_a_non_symbol(provider):
    # 这条原先断言的是**反面**（"test-kit always returns one"）——那正是让
    # 下单路径的 unknown_instrument 门在默认配置下永远打不开的那条行为。
    assert provider.get_instrument("UNKNOWN-SYMBOL-XYZ") is None


async def test_get_bars_parses_polygon_aggregates_oldest_first(provider):
    provider._client = _Client(_Resp(200, {"status": "OK", "results": _AGG_ROWS}))
    provider._sim = _SimBoom()  # 价格若被代理到模拟盘，这条测试立刻炸

    bars = await provider.get_bars("AAPL", "1d", 3)

    assert [str(b.close) for b in bars] == ["299.0", "302.1", "304.2"]
    assert [b.ts for b in bars] == sorted(b.ts for b in bars)      # oldest-first
    assert bars[-1].ts == 1783483200                               # 毫秒 → 秒
    assert bars[-1].high == 305.0 and bars[-1].low == 300.5
    assert bars[-1].volume == 4210
    url, params = provider._client.calls[0]
    assert "/range/1/day/" in url
    assert params["adjusted"] == "true" and params["limit"] == 3


async def test_get_bars_is_a_gap_not_a_substitution_on_http_error(provider):
    provider._client = _Client(_Resp(403, {"status": "NOT_AUTHORIZED"}))
    provider._sim = _SimBoom()

    assert await provider.get_bars("AAPL", "1d", 30) == []


async def test_get_bars_is_a_gap_without_an_api_key():
    p = PolygonMarketDataProvider(api_key="")
    p._client = _Client(_Resp(200, {"results": _AGG_ROWS}))
    p._sim = _SimBoom()
    try:
        assert await p.get_bars("AAPL", "1d", 30) == []
        assert p._client.calls == []  # 没 key 就不去要
    finally:
        await p.aclose()


async def test_get_bars_refuses_a_non_daily_timeframe(provider):
    with pytest.raises(ValueError, match="1d"):
        await provider.get_bars("AAPL", "1m", 20)


def test_supported_timeframes_are_declared(provider):
    # 声明了，路由才能把"这个 provider 不服务该粒度"答成 400 而不是 500。
    assert provider.supported_timeframes == ("1d",)
