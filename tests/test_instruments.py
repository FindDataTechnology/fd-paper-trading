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
