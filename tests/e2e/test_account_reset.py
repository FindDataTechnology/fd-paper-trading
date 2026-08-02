"""E2E: account reset after an active position and a resting order."""

from decimal import Decimal


async def test_reset_after_trade_cancels_and_flattens(
    client, auth_headers, create_account, place_order
):
    h = await auth_headers("reset@example.com")
    aid = (await create_account(h, starting_balance=Decimal("100000"))).json()["id"]

    # Open a position and rest a limit order.
    await place_order(h, aid, symbol="AAPL", side="BUY", order_type="MARKET", quantity="10")
    await place_order(
        h, aid, symbol="MSFT", side="BUY", order_type="LIMIT", quantity="5", limit_price="1.00"
    )

    # Preconditions: one open position, one open order.
    assert len((await client.get(f"/api/v1/accounts/{aid}/positions", headers=h)).json()) == 1
    assert len((await client.get(f"/api/v1/accounts/{aid}/orders?status=OPEN", headers=h)).json()) == 1

    r = await client.post(
        f"/api/v1/accounts/{aid}/reset", json={"starting_balance": "100000"}, headers=h
    )
    assert r.status_code == 200

    # Position flattened, open order cancelled, balance restored to target.
    assert (await client.get(f"/api/v1/accounts/{aid}/positions", headers=h)).json() == []
    assert (await client.get(f"/api/v1/accounts/{aid}/orders?status=OPEN", headers=h)).json() == []
    summary = (await client.get(f"/api/v1/accounts/{aid}", headers=h)).json()
    assert Decimal(str(summary["cash_balance"])) == Decimal("100000")
