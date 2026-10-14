"""Group gate + code-track backtest through the sandbox (design D6/D7, task 8.5).

The custom-code track is off by default (403 ``custom_code_not_allowed``).
Promoting a user into a group with ``allow_custom_code=true`` opens it; a code
strategy then backtests through the subprocess sandbox end-to-end.
"""

import asyncio
import uuid
from datetime import date, timedelta

import pytest

from fd_trade.persistence.models import DEFAULT_GROUP_CONFIG, User
from fd_trade.persistence.repositories import UserRepository

CODE_SOURCE = "def on_bar(ctx):\n    ctx.buy(1)"
CODE_SPEC = {"kind": "code", "symbol": "AAPL", "source": CODE_SOURCE}


async def _authed(client, email="coder@x.io"):
    await client.post("/api/v1/auth/register", json={"email": email, "password": "password123"})
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": "password123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_code_strategy_blocked_by_default_group(client, app):
    h = await _authed(client)
    r = await client.post(
        "/api/v1/strategies",
        json={"name": "code", "spec": CODE_SPEC},
        headers=h,
    )
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "custom_code_not_allowed"


@pytest.mark.asyncio
async def test_code_strategy_allowed_and_backtests_when_enabled(client, app):
    h = await _authed(client)

    # Promote to admin (directly) so we can create a group + assign it.
    async with app.state.db.session_factory() as session:
        user = await UserRepository(session).get_by_email("coder@x.io")
        user.is_admin = True
        await session.commit()
        user_id = user.id

    # Create a group with custom code enabled and assign the user to it.
    r = await client.post(
        "/api/v1/groups",
        json={"name": "pro", "config": {**DEFAULT_GROUP_CONFIG, "allow_custom_code": True}},
        headers=h,
    )
    assert r.status_code == 201, r.text
    group_id = r.json()["id"]

    r = await client.post(
        f"/api/v1/users/{user_id}/group",
        json={"group_id": group_id},
        headers=h,
    )
    assert r.status_code == 200, r.text

    # Now the code spec is accepted.
    r = await client.post(
        "/api/v1/strategies",
        json={"name": "code", "spec": CODE_SPEC},
        headers=h,
    )
    assert r.status_code == 201, r.text
    strategy_id = r.json()["id"]

    # Backtest through the sandbox (one subprocess per bar). The simulated
    # provider anchors its series to "now", so the window must be relative to
    # today — a hardcoded past date rots as the calendar advances.
    end = date.today()
    start = end - timedelta(days=20)
    r = await client.post(
        f"/api/v1/strategies/{strategy_id}/backtests",
        json={"start": start.isoformat(), "end": end.isoformat(), "initial_cash": "100000"},
        headers=h,
    )
    assert r.status_code == 202, r.text
    run_id = r.json()["id"]

    # Poll to COMPLETED (sandbox subprocesses are slowish but sub-second total).
    for _ in range(200):
        r = await client.get(f"/api/v1/strategy-runs/{run_id}", headers=h)
        d = r.json()
        if d["status"] in ("COMPLETED", "FAILED"):
            break
        await asyncio.sleep(0.05)
    assert d["status"] == "COMPLETED", d
    assert d["stats"]["trade_count"] >= 1
    assert d["equity_points"]


@pytest.mark.asyncio
async def test_groups_api_requires_admin(client, app):
    h = await _authed(client)  # not an admin
    r = await client.get("/api/v1/groups", headers=h)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "forbidden"
