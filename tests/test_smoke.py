"""Smoke tests: package imports and app boots."""

import fd_paper_trading


def test_package_imports():
    assert fd_paper_trading.__version__


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_api_is_versioned(client):
    r = await client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    assert "/api/v1/auth/register" in paths
    assert "/api/v1/auth/login" in paths
    assert "/api/v1/accounts" in paths
    assert "/api/v1/tokens" in paths
    assert "/health" in paths
