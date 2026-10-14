"""API tests for strategy CRUD, versioning, isolation, and the template catalog."""

import pytest

TEMPLATE_SPEC = {
    "kind": "template",
    "template": "sma_cross",
    "symbol": "AAPL",
    "params": {"fast": 2, "slow": 3, "quantity": 10},
}
CODE_SPEC = {"kind": "code", "symbol": "AAPL", "source": "def on_bar(ctx): pass"}


async def make_strategy(client, headers, spec=None, name="s1"):
    r = await client.post(
        "/api/v1/strategies",
        json={"name": name, "description": "d", "spec": spec or TEMPLATE_SPEC},
        headers=headers,
    )
    assert r.status_code == 201, r.json()
    return r.json()


# ---- CRUD + versioning ----


@pytest.mark.asyncio
async def test_create_normalizes_spec_and_summarizes(client, auth_headers):
    headers = await auth_headers()
    body = await make_strategy(client, headers)
    assert body["version"] == 1
    assert body["spec"]["symbol"] == "AAPL"
    assert body["spec"]["params"] == {"fast": 2, "slow": 3, "quantity": 10}
    assert body["spec_summary"] == "sma_cross(fast=2, quantity=10, slow=3) on AAPL"


@pytest.mark.asyncio
async def test_create_invalid_spec_returns_field_errors(client, auth_headers):
    headers = await auth_headers()
    r = await client.post(
        "/api/v1/strategies",
        json={"name": "bad", "spec": {"kind": "template", "template": "nope",
                                      "symbol": "AAPL", "params": {}}},
        headers=headers,
    )
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "invalid_spec"
    assert any("unknown template" in f["message"] for f in err["fields"])


@pytest.mark.asyncio
async def test_code_spec_rejected_by_default_group(client, auth_headers):
    headers = await auth_headers()
    r = await client.post(
        "/api/v1/strategies",
        json={"name": "code", "spec": CODE_SPEC},
        headers=headers,
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "custom_code_not_allowed"


@pytest.mark.asyncio
async def test_spec_edit_bumps_version_metadata_edit_does_not(client, auth_headers):
    headers = await auth_headers()
    body = await make_strategy(client, headers)
    sid = body["id"]

    r = await client.patch(
        f"/api/v1/strategies/{sid}",
        json={"name": "renamed", "description": "new"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["version"] == 1
    assert r.json()["name"] == "renamed"

    new_spec = {**TEMPLATE_SPEC, "params": {"fast": 3, "slow": 5, "quantity": 10}}
    r = await client.patch(
        f"/api/v1/strategies/{sid}", json={"spec": new_spec}, headers=headers
    )
    assert r.status_code == 200
    assert r.json()["version"] == 2
    assert r.json()["spec"]["params"]["fast"] == 3

    # Re-submitting the identical spec is a no-op (normalized comparison).
    r = await client.patch(
        f"/api/v1/strategies/{sid}", json={"spec": new_spec}, headers=headers
    )
    assert r.json()["version"] == 2


@pytest.mark.asyncio
async def test_delete_strategy(client, auth_headers):
    headers = await auth_headers()
    body = await make_strategy(client, headers)
    r = await client.delete(f"/api/v1/strategies/{body['id']}", headers=headers)
    assert r.status_code == 204
    r = await client.get(f"/api/v1/strategies/{body['id']}", headers=headers)
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_list_strategies(client, auth_headers):
    headers = await auth_headers()
    await make_strategy(client, headers, name="a")
    await make_strategy(client, headers, name="b")
    r = await client.get("/api/v1/strategies", headers=headers)
    assert r.status_code == 200
    assert {s["name"] for s in r.json()} == {"a", "b"}


# ---- isolation ----


@pytest.mark.asyncio
async def test_user_isolation(client, auth_headers):
    h1 = await auth_headers("one@example.com")
    h2 = await auth_headers("two@example.com")
    body = await make_strategy(client, h1)

    r = await client.get("/api/v1/strategies", headers=h2)
    assert r.json() == []
    for method in ("GET", "PATCH", "DELETE"):
        r = await client.request(
            method, f"/api/v1/strategies/{body['id']}", headers=h2, json={}
        )
        assert r.status_code == 404, method


@pytest.mark.asyncio
async def test_unauthenticated_rejected(client):
    r = await client.get("/api/v1/strategies")
    assert r.status_code == 401


# ---- template catalog ----


@pytest.mark.asyncio
async def test_template_catalog(client, auth_headers):
    headers = await auth_headers()
    r = await client.get("/api/v1/strategy-templates", headers=headers)
    assert r.status_code == 200
    by_id = {t["id"]: t for t in r.json()}
    assert set(by_id) == {
        "sma_cross",
        "rsi_reversion",
        "bollinger_breakout",
        "macd_cross",
        "dca",
    }
    assert by_id["sma_cross"]["params_schema"]["properties"]["fast"]["default"] == 10
