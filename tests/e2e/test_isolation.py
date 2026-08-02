"""E2E: per-account and per-user isolation after trades."""

from decimal import Decimal


async def test_two_accounts_keep_independent_positions_and_cash(
    client, auth_headers, create_account, place_order
):
    h = await auth_headers("iso@example.com")
    a1 = (await create_account(h, name="acct1", starting_balance=Decimal("100000"))).json()["id"]
    a2 = (await create_account(h, name="acct2", starting_balance=Decimal("100000"))).json()["id"]

    # Trade different symbols on each account.
    o1 = (
        await place_order(h, a1, symbol="AAPL", side="BUY", order_type="MARKET", quantity="10")
    ).json()
    o2 = (
        await place_order(h, a2, symbol="MSFT", side="BUY", order_type="MARKET", quantity="5")
    ).json()

    # Positions do not cross-contaminate between accounts.
    p1 = (await client.get(f"/api/v1/accounts/{a1}/positions", headers=h)).json()
    p2 = (await client.get(f"/api/v1/accounts/{a2}/positions", headers=h)).json()
    assert {p["symbol"] for p in p1} == {"AAPL"}
    assert {p["symbol"] for p in p2} == {"MSFT"}

    # Each account's cash reflects only its own trade (tolerance for rounding).
    s1 = Decimal(
        str((await client.get(f"/api/v1/accounts/{a1}", headers=h)).json()["cash_balance"])
    )
    s2 = Decimal(
        str((await client.get(f"/api/v1/accounts/{a2}", headers=h)).json()["cash_balance"])
    )
    assert abs(s1 - (Decimal("100000") - Decimal("10") * Decimal(str(o1["avg_fill_price"])))) < Decimal("0.01")
    assert abs(s2 - (Decimal("100000") - Decimal("5") * Decimal(str(o2["avg_fill_price"])))) < Decimal("0.01")


async def test_cross_user_cannot_read_deposit_or_order(
    client, auth_headers, create_account, place_order
):
    ha = await auth_headers("alice@example.com")
    hb = await auth_headers("bob@example.com")
    aid = (await create_account(ha, starting_balance=Decimal("100000"))).json()["id"]

    assert (await client.get(f"/api/v1/accounts/{aid}", headers=hb)).status_code == 404
    assert (
        await client.post(f"/api/v1/accounts/{aid}/deposit", json={"amount": "1"}, headers=hb)
    ).status_code == 404
    r = await place_order(hb, aid, symbol="AAPL", side="BUY", order_type="MARKET", quantity="10")
    assert r.status_code == 404
