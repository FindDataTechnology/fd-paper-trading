"""Instrument search and bars endpoints (task 3.4)."""

from decimal import Decimal


async def test_search_requires_auth(client):
    r = await client.get("/api/v1/instruments/search?q=AAP")
    assert r.status_code == 401


async def test_search_returns_results(client, auth_headers):
    h = await auth_headers()
    r = await client.get("/api/v1/instruments/search?q=AAP", headers=h)
    assert r.status_code == 200
    symbols = {hit["symbol"] for hit in r.json()}
    assert "AAPL" in symbols
    for hit in r.json():
        assert {"symbol", "name", "venue"} <= set(hit)


async def test_search_empty_query_returns_empty(client, auth_headers):
    h = await auth_headers()
    r = await client.get("/api/v1/instruments/search?q=", headers=h)
    assert r.status_code == 200
    assert r.json() == []


async def test_bars_happy_path(client, auth_headers):
    h = await auth_headers()
    r = await client.get(
        "/api/v1/instruments/AAPL/bars?timeframe=1m&limit=50", headers=h
    )
    assert r.status_code == 200
    bars = r.json()
    assert len(bars) == 50
    timestamps = [b["ts"] for b in bars]
    assert timestamps == sorted(timestamps)
    assert {"ts", "open", "high", "low", "close", "volume"} <= set(bars[0])


async def test_bars_invalid_timeframe_returns_400(client, auth_headers):
    h = await auth_headers()
    r = await client.get(
        "/api/v1/instruments/AAPL/bars?timeframe=2m&limit=10", headers=h
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_timeframe"


async def test_bars_default_limit_applied(client, auth_headers):
    h = await auth_headers()
    r = await client.get("/api/v1/instruments/AAPL/bars", headers=h)
    assert r.status_code == 200
    assert len(r.json()) == 200


async def test_bars_limit_out_of_range_rejected(client, auth_headers):
    h = await auth_headers()
    r = await client.get(
        "/api/v1/instruments/AAPL/bars?limit=99999", headers=h
    )
    assert r.status_code == 422  # validation error (ge/le)


# --- provider capability vs service-wide timeframe set ----------------------


async def test_capabilities_reports_the_active_provider_set(client, auth_headers):
    """图表按 provider 真实能力渲染档位，而不是按服务级全集。"""
    h = await auth_headers()
    r = await client.get("/api/v1/instruments/capabilities", headers=h)
    assert r.status_code == 200
    assert isinstance(r.json()["timeframes"], list) and r.json()["timeframes"]


async def test_capabilities_requires_auth(client):
    r = await client.get("/api/v1/instruments/capabilities")
    assert r.status_code == 401


async def test_unserved_timeframe_is_400_not_500(app, auth_headers, monkeypatch):
    """provider 不支持的档位答 4xx，而不是 500。

    真实故障形态：fd-open-data provider 只服务 ``1d``，而服务级集合声称 5 档。
    请求 ``5m`` 时 provider 抛裸 ``ValueError``，路由不捕获 → 图表拿到 500。
    这里把 provider 的 ``supported_timeframes`` 收窄到 ``("1d",)`` 并复现该请求。
    """
    from httpx import ASGITransport, AsyncClient

    provider = app.state.market_data_provider
    monkeypatch.setattr(provider, "supported_timeframes", ("1d",), raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        h = await auth_headers()
        r = await ac.get("/api/v1/instruments/AAPL/bars?timeframe=5m&limit=10", headers=h)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
    assert r.json()["error"]["code"] == "timeframe_not_served"


async def test_served_timeframe_still_works_when_narrowed(app, auth_headers, monkeypatch):
    """收窄到日线后，日线请求仍然正常返回。"""
    from httpx import ASGITransport, AsyncClient

    provider = app.state.market_data_provider
    monkeypatch.setattr(provider, "supported_timeframes", ("1d",), raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        h = await auth_headers()
        r = await ac.get("/api/v1/instruments/AAPL/bars?timeframe=1d&limit=5", headers=h)
        assert r.status_code == 200
        caps = await ac.get("/api/v1/instruments/capabilities", headers=h)
    assert caps.json()["timeframes"] == ["1d"]


async def test_provider_valueerror_becomes_400(app, auth_headers, monkeypatch):
    """provider 内部对档位抛裸 ValueError 时，路由必须译成 400 而非 500。

    这是原始故障的精确防护：fd-open-data provider 对非 1d 请求 ``raise
    ValueError(...)``，而 FastAPI 会把未捕获异常渲染成 500。``supported_timeframes``
    的声明可以挡住大多数情况，但 provider 仍可能在自己的实现里抛——路由必须兜住。
    """
    from httpx import ASGITransport, AsyncClient

    provider = app.state.market_data_provider

    async def boom(self, symbol, timeframe, limit):
        raise ValueError("fd-open-data provider supports daily bars only")

    # 让能力声明仍然声称支持（模拟「声明与实际不一致」的 provider），
    # 强制请求走到 provider 内部，从而验证 capture 分支。
    monkeypatch.setattr(provider, "supported_timeframes", ("5m",), raising=False)
    monkeypatch.setattr(type(provider), "get_bars", boom, raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        h = await auth_headers()
        r = await ac.get("/api/v1/instruments/AAPL/bars?timeframe=5m&limit=5", headers=h)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
    assert "daily bars only" in r.text


# ---------------------------------------------------------------------------
# 行情响应携带序列引用（需求：每个价格响应都带引用）
# ---------------------------------------------------------------------------


async def test_bars_body_unchanged_but_ref_rides_in_a_header(client, auth_headers, app):
    """响应体仍是裸数组（不破坏图表），引用放头里。

    这是"零破坏"与"每个价格响应都带出处"两条约束的交点：体不动、头增量。
    """
    import json

    from fd_paper_trading._vendor.fd_quant.contracts.models import (
        check_price_series_ref,
    )

    class _RefProvider:
        async def get_bars(self, symbol, timeframe, limit):
            from fd_paper_trading.marketdata.provider import Bar

            return [Bar(ts=1751000000 + i, open=1, high=1, low=1, close=1,
                        volume=1) for i in range(3)]

        def series_ref_for_bars(self, symbol, bars):
            from fd_paper_trading.marketdata.series_ref import build_series_ref

            ref, _ = build_series_ref(
                source_id="astock_daily", symbol=symbol, adjusted="forward",
                as_of_max="2026-07-10", window_start="2026-07-08",
                window_end="2026-07-10", row_count=len(bars),
                adjustment_anchor="2026-07-10", db_source="astock_daily",
            )
            return ref

    h = await auth_headers()
    app.state.market_data_provider = _RefProvider()
    r = await client.get("/api/v1/instruments/600036/bars?timeframe=1d&limit=3",
                         headers=h)
    assert r.status_code == 200
    assert isinstance(r.json(), list) and len(r.json()) == 3   # 体没变
    raw = r.headers["x-price-series-ref"]
    ref = json.loads(raw)
    assert check_price_series_ref(ref) == []
    assert ref["source_id"] == "astock_daily"
    assert ref["symbol"] == "600036"


async def test_no_ref_header_when_the_provider_cannot_say(client, auth_headers, app):
    """simulated 等 provider 说不出处 → 不挂头，而不是挂一个假引用。"""
    h = await auth_headers()
    r = await client.get("/api/v1/instruments/AAPL/bars?timeframe=1m&limit=5",
                         headers=h)
    assert r.status_code == 200
    assert "x-price-series-ref" not in r.headers


async def test_a_broken_ref_builder_never_breaks_the_bars_endpoint(client, auth_headers, app):
    """出处是尽力而为：provider 的 ref 构造抛异常，行情照常返回。"""
    class _Broken:
        async def get_bars(self, symbol, timeframe, limit):
            from fd_paper_trading.marketdata.provider import Bar

            return [Bar(ts=1751000000, open=1, high=1, low=1, close=1, volume=1)]

        def series_ref_for_bars(self, symbol, bars):
            raise RuntimeError("boom")

    h = await auth_headers()
    app.state.market_data_provider = _Broken()
    r = await client.get("/api/v1/instruments/600036/bars?timeframe=1d&limit=1",
                         headers=h)
    assert r.status_code == 200
    assert "x-price-series-ref" not in r.headers
