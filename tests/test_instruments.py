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
