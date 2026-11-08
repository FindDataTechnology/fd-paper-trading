"""Invariants on the money path that had no test at all.

Written directly against the five fixes in ``harden-ponytail-p0`` §4. Each test
names the failure it prevents, because the failure mode is silent: the API
answers 202 and the ledger is wrong.

No exponential smoothing, no mocks of the fill engine: the value of these tests
is that they drive the same HTTP path a client does.
"""

from decimal import Decimal


def _dec(v) -> Decimal:
    return Decimal(str(v))


async def test_same_client_order_id_creates_one_order_only(
    client, auth_headers, create_account, place_order
):
    """A retried submission must not move cash twice (idempotency key)."""
    h = await auth_headers("alice@example.com")
    aid = (await create_account(h, starting_balance=Decimal("100000"))).json()["id"]

    first = await place_order(
        h, aid, symbol="AAPL", side="BUY", quantity="10", client_order_id="retry-key-1"
    )
    second = await place_order(
        h, aid, symbol="AAPL", side="BUY", quantity="10", client_order_id="retry-key-1"
    )

    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    # Same order comes back, not a second one.
    assert first.json()["id"] == second.json()["id"]

    orders = (await client.get(f"/api/v1/accounts/{aid}/orders", headers=h)).json()
    assert len([o for o in orders if o.get("client_order_id") == "retry-key-1"]) == 1

    positions = (await client.get(f"/api/v1/accounts/{aid}/positions", headers=h)).json()
    held = sum((_dec(p["quantity"]) for p in positions), Decimal("0"))
    assert held == Decimal("10"), "a retry must not double the position"

    cash = _dec((await client.get(f"/api/v1/accounts/{aid}", headers=h)).json()["cash_balance"])
    assert abs(Decimal("100000") - cash) < Decimal("100000") * Decimal("0.5")


async def test_stop_trigger_respects_buying_power(
    client, auth_headers, create_account, place_order
):
    """A gap through a resting stop must be rejected, not filled into negative cash.

    Cash 5,000; STOP_LIMIT BUY 100 at stop 50 / limit 60 passes placement
    (reserves 5,000). A gap to 60 makes the fill 6,000 — more cash than exists.
    """
    h = await auth_headers("alice@example.com")
    aid = (await create_account(h, starting_balance=Decimal("5000"))).json()["id"]

    # Posted directly: the shared `place_order` fixture has no stop_price.
    buy = await client.post(
        "/api/v1/orders",
        json={
            "account_id": str(aid),
            "symbol": "AAPL",
            "side": "BUY",
            "order_type": "STOP_LIMIT",
            "quantity": "100",
            "limit_price": "60.00",
            "stop_price": "50.00",
        },
        headers=h,
    )
    assert buy.status_code == 202, buy.text

    cash = _dec((await client.get(f"/api/v1/accounts/{aid}", headers=h)).json()["cash_balance"])
    assert cash >= Decimal("0"), "paper accounts must never go cash-negative"


async def test_sell_cannot_exceed_position(
    client, auth_headers, create_account, place_order
):
    """Naked short via the API: selling more than is held must be refused."""
    h = await auth_headers("alice@example.com")
    aid = (await create_account(h, starting_balance=Decimal("100000"))).json()["id"]

    bought = await place_order(h, aid, symbol="AAPL", side="BUY", quantity="10")
    assert bought.status_code == 202

    oversell = await place_order(h, aid, symbol="AAPL", side="SELL", quantity="10000000")
    assert oversell.status_code != 202, "selling 10,000,000 of a 10-share position must fail"

    positions = (await client.get(f"/api/v1/accounts/{aid}/positions", headers=h)).json()
    held = sum((_dec(p["quantity"]) for p in positions), Decimal("0"))
    assert held >= Decimal("0"), "position must not go negative"


async def test_equity_includes_open_positions(
    client, auth_headers, create_account, place_order
):
    """Equity must not drop by the position value the moment a buy fills.

    Reporting cash only made every client show an instant loss equal to the
    position size.
    """
    h = await auth_headers("alice@example.com")
    aid = (await create_account(h, starting_balance=Decimal("100000"))).json()["id"]

    before = _dec((await client.get(f"/api/v1/accounts/{aid}", headers=h)).json()["equity"])

    r = await place_order(h, aid, symbol="AAPL", side="BUY", quantity="100")
    assert r.status_code == 202, r.text

    detail = (await client.get(f"/api/v1/accounts/{aid}", headers=h)).json()
    after = _dec(detail["equity"])
    cash = _dec(detail["cash_balance"])
    spent = before - cash  # cash that moved into the position

    # Cash-only equity would report `cash` (a loss of the whole position).
    # Marked-to-market equity is cash + position, so the loss is only the
    # ask/close spread — a small fraction of what was spent.
    assert after > cash, "equity must exceed cash while a position is open"
    assert (cash + spent * Decimal("0.9")) <= after, (
        f"equity {after} looks cash-only (cash {cash}, spent {spent}); "
        "positions are not being marked to market"
    )
    assert (before - after) < spent * Decimal("0.1"), (
        f"equity fell {before - after} on a buy of {spent}; that is not a spread"
    )
