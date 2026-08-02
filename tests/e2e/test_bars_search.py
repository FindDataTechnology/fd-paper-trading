"""E2E: symbol search -> historical bars -> order placement journey."""

from decimal import Decimal


async def test_search_bars_then_trade(client, auth_headers, create_account, place_order):
    h = await auth_headers("sb@example.com")
    aid = (await create_account(h, starting_balance=Decimal("100000"))).json()["id"]

    # Search for a symbol by name fragment.
    search = await client.get("/api/v1/instruments/search?q=micro", headers=h)
    assert search.status_code == 200
    assert "MSFT" in {hit["symbol"] for hit in search.json()}

    # Fetch bars for the chosen symbol across a supported timeframe.
    bars = await client.get("/api/v1/instruments/MSFT/bars?timeframe=5m&limit=30", headers=h)
    assert bars.status_code == 200
    assert len(bars.json()) == 30
    timestamps = [b["ts"] for b in bars.json()]
    assert timestamps == sorted(timestamps)  # oldest-first

    # Trade the symbol found via search.
    placed = await place_order(h, aid, symbol="MSFT", side="BUY", order_type="MARKET", quantity="10")
    assert placed.status_code == 202
    assert placed.json()["status"] == "FILLED"


async def test_unknown_timeframe_rejected(client, auth_headers):
    h = await auth_headers("sb@example.com")
    r = await client.get("/api/v1/instruments/AAPL/bars?timeframe=2m&limit=10", headers=h)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_timeframe"
