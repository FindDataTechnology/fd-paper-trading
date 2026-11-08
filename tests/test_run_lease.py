"""One process at a time drives a paper run.

``replicas: 1`` with the default RollingUpdate still starts the second pod before
the first dies. Both ``recover()`` the same runs, both poll the same bars, both
place the orders — the "already processed" guard (``params["last_bar_date"]``) is
written at the END of a tick, after the orders went in, so it cannot prevent the
duplicate. A deploy on a trading day duplicated every signal in flight.

Two supervisors in this test stand in for two pods: separate instances, separate
owner tokens, one database. Account and strategy are created through the API so
this test does not depend on the table shape (it caught a required column the
first time it was written by hand).
"""

import uuid
from datetime import UTC, datetime, timedelta

from fd_paper_trading.persistence.models import (
    Strategy,
    StrategyRun,
    StrategyRunMode,
    StrategyRunStatus,
)
from fd_paper_trading.strategy.runner import PaperRunSupervisor

SPEC = {
    "kind": "template",
    "template": "dca",
    "symbol": "AAPL",
    "params": {"interval_bars": 1, "quantity": 1},
}


async def _make_run(app, client, headers) -> uuid.UUID:
    """A PAPER run row that is RUNNING but has no poll task."""
    acct = await client.post(
        "/api/v1/accounts", json={"name": "lease", "starting_balance": "100000"},
        headers=headers,
    )
    assert acct.status_code == 201, acct.text
    strat = await client.post(
        "/api/v1/strategies", json={"name": "lease", "spec": SPEC}, headers=headers
    )
    assert strat.status_code == 201, strat.text

    async with app.state.db.session_factory() as session:
        strategy = await session.get(Strategy, uuid.UUID(strat.json()["id"]))
        run = StrategyRun(
            strategy_id=strategy.id,
            strategy_version=strategy.version,
            mode=StrategyRunMode.PAPER,
            status=StrategyRunStatus.RUNNING,
            account_id=uuid.UUID(acct.json()["id"]),
            params={"symbol": "AAPL"},
            started_at=datetime.now(UTC),
        )
        session.add(run)
        await session.commit()
        return run.id


async def test_second_supervisor_cannot_take_a_live_run(app, client, auth_headers):
    """Pod B must not tick while pod A holds a valid lease."""
    h = await auth_headers("lease@example.com")
    run_id = await _make_run(app, client, h)

    pod_a = PaperRunSupervisor(app, poll_seconds=0.01)
    pod_b = PaperRunSupervisor(app, poll_seconds=0.01)
    assert pod_a.owner != pod_b.owner

    assert await pod_a._claim_lease(run_id) is True
    assert await pod_b._claim_lease(run_id) is False, (
        "a second process must not be able to claim a live lease"
    )
    # The owner renews its own lease without contention.
    assert await pod_a._claim_lease(run_id) is True


async def test_expired_lease_hands_over(app, client, auth_headers):
    """A crashed owner must not block the run forever: expiry frees it."""
    h = await auth_headers("lease@example.com")
    run_id = await _make_run(app, client, h)

    pod_a = PaperRunSupervisor(app, poll_seconds=0.01)
    pod_b = PaperRunSupervisor(app, poll_seconds=0.01)
    assert await pod_a._claim_lease(run_id) is True

    # Pod A dies without releasing: its lease is now in the past.
    async with app.state.db.session_factory() as session:
        run = await session.get(StrategyRun, run_id)
        run.runner_lease_until = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    assert await pod_b._claim_lease(run_id) is True, (
        "an expired lease must be claimable, otherwise a crash stops the run forever"
    )
    # And the previous owner cannot take it back while B holds it.
    assert await pod_a._claim_lease(run_id) is False


async def test_terminal_state_releases_the_lease(app, client, auth_headers):
    """A stopped run holds no lease, so a restart can pick it up immediately."""
    h = await auth_headers("lease@example.com")
    run_id = await _make_run(app, client, h)
    pod = PaperRunSupervisor(app, poll_seconds=0.01)
    assert await pod._claim_lease(run_id) is True

    await pod._finalize_stop(run_id)

    async with app.state.db.session_factory() as session:
        run = await session.get(StrategyRun, run_id)
        assert run.status == StrategyRunStatus.STOPPED
        assert run.runner_owner is None
        assert run.runner_lease_until is None


async def test_lease_outlives_several_polls(app):
    """The lease must survive a slow tick and still hand over within a poll or two."""
    sup = PaperRunSupervisor(app, poll_seconds=0.01)
    assert sup.lease_seconds >= 60.0
    assert sup.lease_seconds >= 3 * sup.poll_seconds
