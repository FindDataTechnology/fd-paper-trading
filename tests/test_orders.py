"""Order placement/cancellation with simulated fills."""

from decimal import Decimal


def _dec(v) -> Decimal:
    return Decimal(str(v))


async def test_place_market_buy_fills_at_ask(client, auth_headers, create_account, place_order):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    aid = acct["id"]

    r = await place_order(h, aid, symbol="AAPL", side="BUY", order_type="MARKET", quantity="10")
    assert r.status_code == 202
    order = r.json()
    assert order["status"] == "FILLED"
    assert _dec(order["filled_quantity"]) == Decimal("10")
    assert _dec(order["avg_fill_price"]) > 0
    assert len(order["fills"]) == 1
    avg = _dec(order["avg_fill_price"])

    # Position opened long 10 at the fill price.
    rp = await client.get(f"/api/v1/accounts/{aid}/positions", headers=h)
    assert rp.status_code == 200
    positions = rp.json()
    assert len(positions) == 1
    assert _dec(positions[0]["quantity"]) == Decimal("10")
    assert _dec(positions[0]["avg_price"]) == avg

    # Cash reduced by 10 * avg.
    rs = await client.get(f"/api/v1/accounts/{aid}", headers=h)
    cash = _dec(rs.json()["cash_balance"])
    assert abs(cash - (Decimal("100000") - Decimal("10") * avg)) < Decimal("0.01")


async def test_market_sell_after_buy_reduces_position(client, auth_headers, create_account, place_order):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    aid = acct["id"]

    buy = (await place_order(h, aid, symbol="AAPL", side="BUY", order_type="MARKET", quantity="10")).json()
    sell = await place_order(h, aid, symbol="AAPL", side="SELL", order_type="MARKET", quantity="4")
    assert sell.status_code == 202
    assert sell.json()["status"] == "FILLED"

    rp = await client.get(f"/api/v1/accounts/{aid}/positions", headers=h)
    assert _dec(rp.json()[0]["quantity"]) == Decimal("6")  # 10 - 4


async def test_limit_order_rests_when_not_crossing(client, auth_headers, create_account, place_order):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    aid = acct["id"]

    # A tiny limit price (1.00) will not cross the ask -> order rests OPEN.
    r = await place_order(h, aid, symbol="AAPL", side="BUY", order_type="LIMIT", quantity="10", limit_price="1.00")
    assert r.status_code == 202
    order = r.json()
    assert order["status"] == "OPEN"
    assert _dec(order["filled_quantity"]) == Decimal("0")

    # No position, one open order.
    rp = await client.get(f"/api/v1/accounts/{aid}/positions", headers=h)
    assert rp.json() == []
    ro = await client.get(f"/api/v1/accounts/{aid}/orders?status=OPEN", headers=h)
    assert len(ro.json()) == 1


async def test_limit_order_fills_when_crossing(client, auth_headers, create_account, place_order):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    aid = acct["id"]

    # A very high limit (99999) crosses the ask -> fills immediately.
    r = await place_order(h, aid, symbol="AAPL", side="BUY", order_type="LIMIT", quantity="10", limit_price="99999")
    assert r.status_code == 202
    assert r.json()["status"] == "FILLED"


async def test_insufficient_buying_power_rejected(client, auth_headers, create_account, place_order):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("1000"))).json()
    aid = acct["id"]

    r = await place_order(h, aid, symbol="AAPL", side="BUY", order_type="MARKET", quantity="100000")
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "insufficient_buying_power"


async def test_cancel_open_limit_order(client, auth_headers, create_account, place_order):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    aid = acct["id"]

    placed = (await place_order(h, aid, symbol="AAPL", side="BUY", order_type="LIMIT", quantity="10", limit_price="1.00")).json()
    r = await client.delete(f"/api/v1/orders/{placed['id']}", headers=h)
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"


async def test_cancel_filled_order_rejected(client, auth_headers, create_account, place_order):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    aid = acct["id"]

    placed = (await place_order(h, aid, symbol="AAPL", side="BUY", order_type="MARKET", quantity="10")).json()
    assert placed["status"] == "FILLED"
    r = await client.delete(f"/api/v1/orders/{placed['id']}", headers=h)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "not_cancellable"


async def test_cross_account_order_denied(client, auth_headers, create_account, place_order):
    ha = await auth_headers("alice@example.com")
    hb = await auth_headers("bob@example.com")
    acct_a = (await create_account(ha, starting_balance=Decimal("100000"))).json()

    r = await place_order(hb, acct_a["id"], symbol="AAPL", side="BUY", order_type="MARKET", quantity="10")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_order_requires_auth(client, create_account, auth_headers):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    r = await client.post(
        "/api/v1/orders",
        json={"account_id": acct["id"], "symbol": "AAPL", "side": "BUY", "order_type": "MARKET", "quantity": "10"},
    )
    assert r.status_code == 401


async def test_limit_requires_limit_price(client, auth_headers, create_account):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    r = await client.post(
        "/api/v1/orders",
        json={"account_id": acct["id"], "symbol": "AAPL", "side": "BUY", "order_type": "LIMIT", "quantity": "10"},
        headers=h,
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "validation_error"
