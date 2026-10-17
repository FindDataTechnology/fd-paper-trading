"""Weight-track runs through the API (task 3.4, spec: strategy-weight-mode).

Two halves, matching what the API actually owns:

* the backtest record — a weight run's ``universe``, per-rebalance snapshots and
  instruction details are persisted on the run and served by the detail
  endpoint, and editing the strategy afterwards cannot rewrite them;
* the start-time gates — a paper run whose universe exceeds the group's
  ``max_universe_size`` is refused with both numbers named, and the weight
  track's own window limit behaves like the single-symbol one.

The replay engine's arithmetic is asserted in ``test_weight_backtester.py``;
what is asserted here is only that the API records and serves it.
"""

import asyncio
from datetime import date, timedelta

import pytest

from fd_paper_trading.persistence.models import (
    DEFAULT_GROUP_CONFIG,
    DEFAULT_GROUP_ID,
    UserGroup,
)
from fd_paper_trading.persistence.repositories import UserRepository

WINDOW_DAYS = 21

#: A static-series weight spec: three symbols, rebalance every second bar. The
#: series starts long before the window so every rebalance point has a period.
WEIGHT_SERIES_SPEC = {
    "kind": "weight",
    "universe": ["AAPL", "MSFT", "TSLA"],
    "rebalance": {"every_n_bars": 2},
    "weights_series": {"2000-01-01": {"AAPL": 0.4, "MSFT": 0.3, "TSLA": 0.2}},
}


def _window(days: int = WINDOW_DAYS) -> dict:
    end = date.today()
    start = end - timedelta(days=days - 1)
    return {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "initial_cash": "100000",
    }


async def _make_strategy(client, headers, spec=None, name="weight"):
    r = await client.post(
        "/api/v1/strategies",
        json={"name": name, "spec": spec or WEIGHT_SERIES_SPEC},
        headers=headers,
    )
    assert r.status_code == 201, r.json()
    return r.json()["id"]


async def _start_backtest(client, headers, strategy_id, window=None):
    r = await client.post(
        f"/api/v1/strategies/{strategy_id}/backtests",
        json=window or _window(),
        headers=headers,
    )
    assert r.status_code == 202, r.json()
    return r.json()


async def _wait_terminal(client, headers, run_id, tries=400):
    for _ in range(tries):
        r = await client.get(f"/api/v1/strategy-runs/{run_id}", headers=headers)
        assert r.status_code == 200, r.json()
        body = r.json()
        if body["status"] in ("COMPLETED", "FAILED"):
            return body
        await asyncio.sleep(0.02)
    raise AssertionError(f"run {run_id} did not reach a terminal state")


async def _set_group_config(app, email, **overrides):
    """Give the user a group whose config overrides the defaults."""
    async with app.state.db.session_factory() as session:
        user = await UserRepository(session).get_by_email(email)
        group = UserGroup(
            id=DEFAULT_GROUP_ID,
            name="default",
            config={**DEFAULT_GROUP_CONFIG, **overrides},
        )
        session.add(group)
        user.group_id = group.id
        await session.commit()


# ---------------------------------------------------------------------------
# Backtest records
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_weight_backtest_records_universe_snapshots_and_stats(
    client, auth_headers
):
    """A 3-symbol weight backtest completes and its detail carries the record."""
    headers = await auth_headers("weight@example.com")
    sid = await _make_strategy(client, headers)
    run = await _start_backtest(client, headers, sid)
    assert run["mode"] == "BACKTEST"
    assert run["universe"] == ["AAPL", "MSFT", "TSLA"]

    done = await _wait_terminal(client, headers, run["id"])
    assert done["status"] == "COMPLETED", done["error"]

    assert done["universe"] == ["AAPL", "MSFT", "TSLA"]
    snapshots = done["weight_snapshots"]
    assert snapshots, "a completed weight run records its rebalance snapshots"
    assert all({"target_weights", "actual_weights", "instructions"} <= set(s)
               for s in snapshots)
    # Each symbol's target is the series' own mapping, so the record shows what
    # the engine was asked for, not a normalized version of it.
    assert snapshots[0]["target_weights"] == {"AAPL": 0.4, "MSFT": 0.3, "TSLA": 0.2}
    # The first rebalance buys into an empty portfolio: real instructions exist.
    first = snapshots[0]["instructions"]
    assert first, "the first rebalance should buy the target portfolio"
    for ins in first:
        assert {"symbol", "side", "quantity", "scaled"} <= set(ins)
        assert ins["side"] in ("BUY", "SELL")

    stats = done["stats"]
    assert stats["rebalance_count"] >= 1
    assert stats["trade_count"] >= 1
    assert "final_equity" in stats
    assert done["equity_points"], "the portfolio curve is recorded"
    assert len(done["equity_points"]) >= 1


@pytest.mark.asyncio
async def test_weight_run_universe_pinned_at_start(client, auth_headers):
    """Editing the strategy's universe after the run leaves the run untouched."""
    headers = await auth_headers("pinned@example.com")
    sid = await _make_strategy(client, headers)
    run = await _start_backtest(client, headers, sid)
    done = await _wait_terminal(client, headers, run["id"])
    assert done["status"] == "COMPLETED", done["error"]
    original_snapshots = done["weight_snapshots"]

    edited = {**WEIGHT_SERIES_SPEC, "universe": ["NVDA", "AMD"]}
    edited["weights_series"] = {"2000-01-01": {"NVDA": 0.5, "AMD": 0.5}}
    r = await client.patch(
        f"/api/v1/strategies/{sid}", json={"spec": edited}, headers=headers
    )
    assert r.status_code == 200, r.json()
    assert r.json()["version"] == 2

    after = await client.get(f"/api/v1/strategy-runs/{run['id']}", headers=headers)
    body = after.json()
    assert body["strategy_version"] == 1, "the run keeps the version it ran"
    assert body["universe"] == ["AAPL", "MSFT", "TSLA"], "universe is pinned"
    assert body["weight_snapshots"] == original_snapshots


@pytest.mark.asyncio
async def test_weight_backtest_window_beyond_group_limit_rejected(
    client, auth_headers
):
    """The date-based window gate applies to weight runs unchanged."""
    headers = await auth_headers("window@example.com")
    sid = await _make_strategy(client, headers)
    end = date.today()
    window = {
        "start": (end - timedelta(days=800)).isoformat(),
        "end": end.isoformat(),
        "initial_cash": "100000",
    }
    r = await client.post(
        f"/api/v1/strategies/{sid}/backtests", json=window, headers=headers
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "window_too_large"
    assert "730" in r.json()["error"]["message"]


@pytest.mark.asyncio
async def test_single_symbol_backtest_has_no_weight_columns(client, auth_headers):
    """The single-symbol path records no universe and no snapshots."""
    headers = await auth_headers("single@example.com")
    spec = {
        "kind": "template",
        "template": "dca",
        "symbol": "AAPL",
        "params": {"interval_bars": 2, "quantity": 1},
    }
    sid = await _make_strategy(client, headers, spec=spec, name="dca")
    run = await _start_backtest(client, headers, sid)
    done = await _wait_terminal(client, headers, run["id"])
    assert done["status"] == "COMPLETED"
    assert done["universe"] is None
    assert done["weight_snapshots"] is None
    assert done["data_gaps"] is None
    assert "rebalance_count" not in done["stats"], "single-symbol stats unchanged"


# ---------------------------------------------------------------------------
# Start-time gates
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_paper_run_rejects_universe_over_group_limit(
    client, auth_headers, app, create_account
):
    """An oversized universe is refused naming the limit and the actual size."""
    headers = await auth_headers("universe@example.com")
    await _set_group_config(app, "universe@example.com", max_universe_size=2)
    account = await create_account(headers)
    assert account.status_code == 201, account.json()

    spec = {
        "kind": "weight",
        "universe": ["AAPL", "MSFT", "TSLA"],
        "rebalance": {"every_n_bars": 2},
        "weights_series": {"2000-01-01": {"AAPL": 0.4, "MSFT": 0.3, "TSLA": 0.2}},
    }
    sid = await _make_strategy(client, headers, spec=spec)
    r = await client.post(
        f"/api/v1/strategies/{sid}/runs",
        json={"account_id": account.json()["id"]},
        headers=headers,
    )
    assert r.status_code in (400, 409), r.text
    message = r.json()["error"]["message"]
    assert "2" in message, "the limit must be named"
    assert "3" in message, "the actual size must be named"


@pytest.mark.asyncio
async def test_paper_run_records_universe_within_limit(
    client, auth_headers, app, create_account
):
    """A weight paper run inside the limit starts, storing its universe."""
    headers = await auth_headers("small@example.com")
    await _set_group_config(app, "small@example.com", max_universe_size=5)
    account = await create_account(headers)
    spec = {
        "kind": "weight",
        "universe": ["AAPL", "MSFT"],
        "rebalance": {"every_n_bars": 2},
        "weights_series": {"2000-01-01": {"AAPL": 0.5, "MSFT": 0.4}},
    }
    sid = await _make_strategy(client, headers, spec=spec)
    r = await client.post(
        f"/api/v1/strategies/{sid}/runs",
        json={"account_id": account.json()["id"]},
        headers=headers,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["universe"] == ["AAPL", "MSFT"]
    assert body["params"]["universe"] == ["AAPL", "MSFT"]
    # The run row is created before the runner reads it; stop it so the test
    # does not leave a task polling behind.
    await client.post(f"/api/v1/strategy-runs/{body['id']}/stop", headers=headers)
    await app.state.strategy_runner.shutdown()


@pytest.mark.asyncio
async def test_code_track_weight_source_gated_by_allow_custom_code(
    client, auth_headers
):
    """A weight spec carrying source runs user code, so the sandbox gate applies."""
    headers = await auth_headers("gate@example.com")
    spec = {
        "kind": "weight",
        "universe": ["AAPL", "MSFT"],
        "rebalance": {"every_n_bars": 2},
        "source": "def target_weights(ctx):\n    return {'AAPL': 0.5, 'MSFT': 0.4}",
    }
    r = await client.post(
        "/api/v1/strategies", json={"name": "coded", "spec": spec}, headers=headers
    )
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "custom_code_not_allowed"


@pytest.mark.asyncio
async def test_code_track_weight_backtest_runs_in_sandbox(
    client, auth_headers, app
):
    """A legal ``target_weights`` source backtests end to end through the sandbox."""
    headers = await auth_headers("coded@example.com")
    await _set_group_config(app, "coded@example.com", allow_custom_code=True)
    spec = {
        "kind": "weight",
        "universe": ["AAPL", "MSFT"],
        "rebalance": {"every_n_bars": 5},
        "source": (
            "def target_weights(ctx):\n"
            "    closes = ctx.closes('AAPL')\n"
            "    if len(closes) < 2 or closes[-1] >= closes[-2]:\n"
            "        return {'AAPL': 0.5, 'MSFT': 0.4}\n"
            "    return {'AAPL': 0.2, 'MSFT': 0.2}\n"
        ),
    }
    sid = await _make_strategy(client, headers, spec=spec, name="coded")
    run = await _start_backtest(client, headers, sid)
    done = await _wait_terminal(client, headers, run["id"])
    assert done["status"] == "COMPLETED", done["error"]
    assert done["weight_snapshots"], "the sandbox's mapping reached the engine"
    targets = done["weight_snapshots"][0]["target_weights"]
    assert targets in (
        {"AAPL": 0.5, "MSFT": 0.4},
        {"AAPL": 0.2, "MSFT": 0.2},
    )
