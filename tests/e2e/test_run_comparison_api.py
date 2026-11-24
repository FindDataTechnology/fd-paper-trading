"""Simulation profile + run comparison over HTTP (change add-paper-run-metrics).

Two surfaces:
- every run records its ``simulation_profile`` at creation, and it stays put;
- ``GET /strategy-runs/{id}/comparison/{other}`` refuses to compare runs that
  differ in any decision-relevant dimension, and only diffs when they do not.
"""

import uuid

import pytest

from tests.e2e.test_strategy_backtests import (
    DCA_SPEC,
    _start_backtest,
    _wait_terminal,
    _window,
)
from tests.e2e.test_strategy_paper_runs import (
    _authed,
    _make_run_db,
    _make_strategy,
    _run_detail,
    _wire,
)

pytestmark = pytest.mark.asyncio


async def _new_backtest(client, h, sid, window):
    return await _start_backtest(client, h, sid, window)


async def _make_strategy_row(client, h, spec=DCA_SPEC, name="dca"):
    return await _make_strategy(client, h, spec=spec, name=name)


async def _account_and_strategy(client, h):
    r = await client.post("/api/v1/accounts", json={"name": "cmp"}, headers=h)
    account_id = uuid.UUID(r.json()["id"])
    strat = await _make_strategy_row(client, h)
    return account_id, strat


async def test_every_run_records_a_profile(client, app):
    await _wire(app)
    h = await _authed(client)
    account_id, strat = await _account_and_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)
    await app.state.strategy_runner._tick_once(run_id)

    detail = await _run_detail(client, h, run_id)
    profile = detail["simulation_profile"]
    assert profile is not None
    assert profile["lot"] == "whole_share"
    assert profile["settlement"] in ("T+0", "T+1")
    assert profile["cost"]["mode"] in ("none", "modeled")


async def test_backtest_records_profile_with_cost_mode(client, app):
    h = await _authed(client)
    strat = await _make_strategy_row(client, h)
    body = await _new_backtest(client, h, str(strat["id"]), {
        **_window(), "cost_model": {"enabled": True},
    })
    detail = await _wait_terminal(client, h, str(body["id"]))
    assert detail["simulation_profile"]["cost"]["mode"] == "modeled"


async def test_profile_is_pinned_at_creation(client, app):
    h = await _authed(client)
    strat = await _make_strategy_row(client, h)
    body = await _new_backtest(client, h, str(strat["id"]), {
        **_window(), "cost_model": {"enabled": True, "commission_rate": "0.001"},
    })
    first = (await _wait_terminal(client, h, str(body["id"])))["simulation_profile"]
    again = (await _run_detail(client, h, uuid.UUID(body["id"])))["simulation_profile"]
    assert first == again


async def test_comparison_refuses_different_strategies(client, app):
    h = await _authed(client)
    s1 = await _make_strategy_row(client, h)
    s2 = await _make_strategy_row(client, h, name="dca2")
    b1 = await _new_backtest(client, h, str(s1["id"]),
                             _window())
    b2 = await _new_backtest(client, h, str(s2["id"]),
                             _window())
    await _wait_terminal(client, h, str(b1["id"]))
    await _wait_terminal(client, h, str(b2["id"]))
    r = await client.get(
        f"/api/v1/strategy-runs/{b1['id']}/comparison/{b2['id']}", headers=h
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "not_comparable"


async def test_comparison_of_two_identical_backtests_diffs(client, app):
    h = await _authed(client)
    strat = await _make_strategy_row(client, h)
    body = _window()
    b1 = await _new_backtest(client, h, str(strat["id"]), body)
    b2 = await _new_backtest(client, h, str(strat["id"]), body)
    await _wait_terminal(client, h, str(b1["id"]))
    await _wait_terminal(client, h, str(b2["id"]))
    r = await client.get(
        f"/api/v1/strategy-runs/{b1['id']}/comparison/{b2['id']}", headers=h
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["comparable"] is True
    assert body["reasons"] == []
    assert body["stats"]["diff"]["total_return"] == 0.0
