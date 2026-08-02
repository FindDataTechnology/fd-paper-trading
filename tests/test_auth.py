"""Auth: registration, login, JWT and API tokens, isolation, error format."""

import uuid


async def test_register_success(client, register):
    r = await register("alice@example.com")
    assert r.status_code == 201
    body = r.json()
    assert body["email"] == "alice@example.com"
    assert "id" in body
    assert "password" not in body
    assert "password_hash" not in body


async def test_register_duplicate_rejected(client, register):
    await register("alice@example.com")
    r = await register("alice@example.com")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "email_exists"


async def test_register_validation_error(client, register):
    r = await register("alice@example.com", password="short")
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["fields"]


async def test_login_success(client, register, login):
    await register("alice@example.com")
    r = await login("alice@example.com")
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0
    assert uuid.UUID(body["user_id"])


async def test_login_invalid_credentials(client, register, login):
    await register("alice@example.com")
    r = await login("alice@example.com", password="wrong-password")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_credentials"


async def test_unauthenticated_rejected(client):
    r = await client.get("/api/v1/accounts")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


async def test_api_token_create_list_revoke(client, auth_headers):
    headers = await auth_headers("alice@example.com")

    r = await client.post("/api/v1/tokens", json={"name": "bot"}, headers=headers)
    assert r.status_code == 201
    plaintext = r.json()["token"]
    assert plaintext.startswith("fdt_")
    token_id = r.json()["id"]

    listed = await client.get("/api/v1/tokens", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["token"] is None  # plaintext never returned on list

    # API token authenticates just like a JWT.
    via_token = await client.get(
        "/api/v1/accounts", headers={"Authorization": f"Bearer {plaintext}"}
    )
    assert via_token.status_code == 200

    revoked = await client.delete(f"/api/v1/tokens/{token_id}", headers=headers)
    assert revoked.status_code == 204

    after_revoke = await client.get(
        "/api/v1/accounts", headers={"Authorization": f"Bearer {plaintext}"}
    )
    assert after_revoke.status_code == 401


async def test_revoked_token_leaves_jwt_valid(client, auth_headers):
    headers = await auth_headers("alice@example.com")
    r = await client.post("/api/v1/tokens", json={"name": "bot"}, headers=headers)
    plaintext = r.json()["token"]
    token_id = r.json()["id"]
    await client.delete(f"/api/v1/tokens/{token_id}", headers=headers)

    # The original JWT in `headers` still works.
    r2 = await client.get("/api/v1/accounts", headers=headers)
    assert r2.status_code == 200
    # ...but the revoked API token does not.
    r3 = await client.get("/api/v1/accounts", headers={"Authorization": f"Bearer {plaintext}"})
    assert r3.status_code == 401
