"""E2E: an API token can place orders; a revoked token cannot."""

from decimal import Decimal


async def test_api_token_places_order_then_revoked_rejects(
    client, auth_headers, create_account, place_order
):
    h = await auth_headers("tok@example.com")
    aid = (await create_account(h, starting_balance=Decimal("100000"))).json()["id"]

    # Create an API token.
    r = await client.post("/api/v1/tokens", json={"name": "bot"}, headers=h)
    assert r.status_code == 201
    plaintext = r.json()["token"]
    token_id = r.json()["id"]
    token_headers = {"Authorization": f"Bearer {plaintext}"}

    # Use the API token to place an order - it authenticates like a JWT.
    placed = await place_order(
        token_headers, aid, symbol="AAPL", side="BUY", order_type="MARKET", quantity="10"
    )
    assert placed.status_code == 202
    assert placed.json()["status"] == "FILLED"

    # The fill landed on the account.
    positions = (await client.get(f"/api/v1/accounts/{aid}/positions", headers=h)).json()
    assert any(p["symbol"] == "AAPL" for p in positions)

    # Revoke the token.
    revoked = await client.delete(f"/api/v1/tokens/{token_id}", headers=h)
    assert revoked.status_code == 204

    # A subsequent order placement with the revoked token is rejected.
    again = await place_order(
        token_headers, aid, symbol="MSFT", side="BUY", order_type="MARKET", quantity="5"
    )
    assert again.status_code == 401
