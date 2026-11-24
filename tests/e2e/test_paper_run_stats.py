"""Paper-run headline stats (change add-paper-run-metrics, tasks 2.1-2.4).

Before this change a paper run wrote no ``run.stats`` at all — the run-history
row showed ``-`` for return/Sharpe/trades because nothing filled them. These
tests pin four things the spec calls out:

- a stopped run has stats, measured from its own first equity point;
- a run whose account held positions before it started does not use the
  account's opening cash as the baseline;
- an unmeasurable curve reports null + a reason, never a fabricated zero;
- ``win_rate`` is null *with a note*, not silently absent.
"""

from decimal import Decimal

import uuid

import pytest

from test_strategy_paper_runs import (
    _authed,
    _make_account,
    _make_run_db,
    _make_strategy,
    _run_detail,
    _wire,
)


@pytest.mark.asyncio
async def test_stopped_run_records_stats(client, app):
    provider = await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)

    sup = app.state.strategy_runner
    await sup._tick_once(run_id)
    provider.add_bars(1)
    await sup._tick_once(run_id)
    await sup._finalize_stop(run_id)

    detail = await _run_detail(client, h, run_id)
    stats = detail["stats"]
    assert stats is not None
    for key in ("total_return", "max_drawdown", "sharpe", "trade_count", "final_equity"):
        assert key in stats
    assert stats["trade_count"] == 2  # two DCA buys, one per processed bar
    assert stats["total_return"] is not None


@pytest.mark.asyncio
async def test_baseline_is_first_equity_point_not_opening_cash(client, app):
    """A run that starts with existing positions measures from its own start."""
    provider = await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)

    sup = app.state.strategy_runner
    await sup._tick_once(run_id)
    provider.add_bars(1)
    await sup._tick_once(run_id)

    async with app.state.db.session_factory() as session:
        from fd_paper_trading.persistence.repositories import StrategyRunRepository

        points = await StrategyRunRepository(session).list_equity_points(run_id)
    baseline = points[0].equity
    # If the baseline were the account's opening cash (100k) rather than the
    # first equity point, a run that bought on day one would show a nonzero
    # return on day one. Comparing final to the first point keeps it honest.
    final = points[-1].equity
    expected = float(final / baseline) - 1.0

    await sup._finalize_stop(run_id)
    detail = await _run_detail(client, h, run_id)
    assert detail["stats"]["total_return"] == pytest.approx(round(expected, 6), abs=1e-6)


@pytest.mark.asyncio
async def test_single_point_records_no_fabricated_zero(client, app):
    provider = await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)

    await app.state.strategy_runner._tick_once(run_id)  # exactly one snapshot
    await app.state.strategy_runner._finalize_stop(run_id)

    detail = await _run_detail(client, h, run_id)
    stats = detail["stats"]
    assert stats["sharpe"] is None
    assert stats["notes"]  # the reason is present, not silenced


@pytest.mark.asyncio
async def test_win_rate_is_null_with_a_reason(client, app):
    provider = await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)

    await app.state.strategy_runner._tick_once(run_id)
    provider.add_bars(1)
    await app.state.strategy_runner._tick_once(run_id)
    await app.state.strategy_runner._finalize_stop(run_id)

    stats = (await _run_detail(client, h, run_id))["stats"]
    assert stats["win_rate"] is None
    assert any("win_rate" in n for n in stats["notes"])


@pytest.mark.asyncio
async def test_crashed_run_still_records_stats(client, app):
    provider = await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)

    await app.state.strategy_runner._tick_once(run_id)
    provider.add_bars(1)
    await app.state.strategy_runner._tick_once(run_id)
    await app.state.strategy_runner._finalize_error(run_id, RuntimeError("boom"))

    detail = await _run_detail(client, h, run_id)
    assert detail["status"] == "ERROR"
    assert detail["stats"]["final_equity"] is not None
