"""Paper accounts: creation, balances, summary, reset, isolation."""

import uuid
from decimal import Decimal


async def test_create_account_default_balance(client, auth_headers, create_account):
    h = await auth_headers("alice@example.com")
    r = await create_account(h)
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "default"
    assert body["venue"].startswith("PAPER-")
    assert Decimal(str(body["cash_balance"])) == Decimal("100000")


async def test_create_account_custom_balance(client, auth_headers, create_account):
    h = await auth_headers("alice@example.com")
    r = await create_account(h, starting_balance=Decimal("50000"))
    assert Decimal(str(r.json()["cash_balance"])) == Decimal("50000")


async def test_multiple_accounts_per_user(client, auth_headers, create_account):
    h = await auth_headers("alice@example.com")
    await create_account(h, name="acct1")
    await create_account(h, name="acct2")
    r = await client.get("/api/v1/accounts", headers=h)
    assert r.status_code == 200
    assert len(r.json()) == 2


async def test_account_summary(client, auth_headers, create_account):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h)).json()
    r = await client.get(f"/api/v1/accounts/{acct['id']}", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["open_positions"] == 0
    assert body["open_orders"] == 0
    assert Decimal(str(body["cash_balance"])) == Decimal("100000")
    assert Decimal(str(body["equity"])) == Decimal("100000")


async def test_deposit_and_withdrawal(client, auth_headers, create_account):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h)).json()
    aid = acct["id"]

    r = await client.post(f"/api/v1/accounts/{aid}/deposit", json={"amount": "5000"}, headers=h)
    assert r.status_code == 200
    assert Decimal(str(r.json()["cash_balance"])) == Decimal("105000")

    r = await client.post(f"/api/v1/accounts/{aid}/deposit", json={"amount": "-3000"}, headers=h)
    assert r.status_code == 200
    assert Decimal(str(r.json()["cash_balance"])) == Decimal("102000")

    r = await client.post(f"/api/v1/accounts/{aid}/deposit", json={"amount": "-999999"}, headers=h)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_amount"


async def test_account_reset(client, auth_headers, create_account):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h, starting_balance=Decimal("100000"))).json()
    aid = acct["id"]
    await client.post(f"/api/v1/accounts/{aid}/deposit", json={"amount": "5000"}, headers=h)

    r = await client.post(
        f"/api/v1/accounts/{aid}/reset", json={"starting_balance": "100000"}, headers=h
    )
    assert r.status_code == 200
    assert Decimal(str(r.json()["cash_balance"])) == Decimal("100000")


async def test_positions_and_orders_empty(client, auth_headers, create_account):
    h = await auth_headers("alice@example.com")
    acct = (await create_account(h)).json()
    aid = acct["id"]

    rp = await client.get(f"/api/v1/accounts/{aid}/positions", headers=h)
    assert rp.status_code == 200
    assert rp.json() == []

    ro = await client.get(f"/api/v1/accounts/{aid}/orders", headers=h)
    assert ro.status_code == 200
    assert ro.json() == []


async def test_account_isolation_between_users(client, auth_headers, create_account):
    ha = await auth_headers("alice@example.com")
    hb = await auth_headers("bob@example.com")
    acct_a = (await create_account(ha)).json()
    aid = acct_a["id"]

    # Bob cannot read Alice's account.
    r = await client.get(f"/api/v1/accounts/{aid}", headers=hb)
    assert r.status_code == 404

    # Bob cannot deposit into Alice's account.
    r = await client.post(f"/api/v1/accounts/{aid}/deposit", json={"amount": "1"}, headers=hb)
    assert r.status_code == 404

    # Bob's own account list is empty.
    r = await client.get("/api/v1/accounts", headers=hb)
    assert r.json() == []


async def test_get_unknown_order_404(client, auth_headers):
    h = await auth_headers("alice@example.com")
    r = await client.get(f"/api/v1/orders/{uuid.uuid4()}", headers=h)
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"


async def test_unknown_route_returns_standardized_404(client):
    r = await client.get("/api/v1/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
