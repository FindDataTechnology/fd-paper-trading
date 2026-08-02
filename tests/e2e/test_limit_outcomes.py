"""E2E: limit-order placement outcomes and lifecycle.

v1 computes fills statelessly per order, so a resting limit never fills on its
own - it stays OPEN until cancelled. A crossing limit fills immediately.
"""

from decimal import Decimal


async def test_resting_limit_is_open_then_cancellable(
    client, auth_headers, create_account, place_order
):
    h = await auth_headers("lim@example.com")
    aid = (await create_account(h, starting_balance=Decimal("100000"))).json()["id"]

    placed = (
        await place_order(
            h, aid, symbol="AAPL", side="BUY", order_type="LIMIT", quantity="10", limit_price="1.00"
        )
    ).json()
    assert placed["status"] == "OPEN"
    assert placed["filled_quantity"] == "0"

    # No position opened; the order appears under ?status=OPEN.
    assert (await client.get(f"/api/v1/accounts/{aid}/positions", headers=h)).json() == []
    open_orders = (
        await client.get(f"/api/v1/accounts/{aid}/orders?status=OPEN", headers=h)
    ).json()
    assert any(o["id"] == placed["id"] for o in open_orders)

    # Cancel it.
    cancelled = await client.delete(f"/api/v1/orders/{placed['id']}", headers=h)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELLED"

    # It no longer appears under ?status=OPEN.
    open_orders = (
        await client.get(f"/api/v1/accounts/{aid}/orders?status=OPEN", headers=h)
    ).json()
    assert not any(o["id"] == placed["id"] for o in open_orders)


async def test_crossing_limit_fills_immediately_and_opens_position(
    client, auth_headers, create_account, place_order
):
    h = await auth_headers("lim@example.com")
    aid = (await create_account(h, starting_balance=Decimal("100000"))).json()["id"]

    filled = (
        await place_order(
            h, aid, symbol="AAPL", side="BUY", order_type="LIMIT", quantity="10", limit_price="99999"
        )
    ).json()
    assert filled["status"] == "FILLED"
    assert filled["filled_quantity"] == "10"

    positions = (await client.get(f"/api/v1/accounts/{aid}/positions", headers=h)).json()
    assert any(
        p["symbol"] == "AAPL" and Decimal(str(p["quantity"])) == Decimal("10") for p in positions
    )
