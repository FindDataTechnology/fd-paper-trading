"""Unit tests for strategy/backtester.py: fill rules, portfolio math, stats."""

from datetime import date
from decimal import Decimal

import pytest

from fd_trade.marketdata.provider import Bar
from fd_trade.strategy import (
    EvaluationError,
    StrategyContext,
    build_evaluator,
    replay,
    validate_spec,
)
from fd_trade.strategy.backtester import _Portfolio, compute_stats


def make_bars(closes: list[float], lows=None, highs=None) -> list[Bar]:
    bars = []
    for i, c in enumerate(closes):
        lo = lows[i] if lows else c
        hi = highs[i] if highs else c
        bars.append(
            Bar(
                ts=1_700_000_000 + i * 86_400,
                open=Decimal(str(c)),
                high=Decimal(str(hi)),
                low=Decimal(str(lo)),
                close=Decimal(str(c)),
                volume=Decimal(0),
            )
        )
    return bars


class LambdaEvaluator:
    """Test evaluator: fn(ctx) called per bar."""

    def __init__(self, fn):
        self._fn = fn

    async def evaluate(self, ctx: StrategyContext) -> None:
        self._fn(ctx)


async def run(fn, closes, cash="10000", lows=None, highs=None):
    return await replay(
        make_bars(closes, lows=lows, highs=highs),
        LambdaEvaluator(fn),
        Decimal(cash),
    )


# ---- fill rules ----


@pytest.mark.asyncio
async def test_market_buy_fills_at_signal_bar_close():
    result = await run(lambda ctx: ctx.buy(10) if len(ctx.closes()) == 1 else None,
                       closes=[100, 101, 102])
    # filled bar 1 at 100; equity = 10000 - 1000 + 10*close
    assert result.equity_points[0][1] == Decimal(10000)
    assert result.equity_points[-1][1] == Decimal(9000) + 10 * Decimal(102)
    assert result.stats["trade_count"] == 1


@pytest.mark.asyncio
async def test_limit_buy_fills_when_low_crosses_and_expires_otherwise():
    def strategy(ctx: StrategyContext) -> None:
        n = len(ctx.closes())
        if n == 1:
            ctx.buy(10, limit_price=95)  # low 98 > 95: no fill, expires
        elif n == 2:
            ctx.buy(10, limit_price=95)  # low 94 <= 95: fills at 95

    result = await run(strategy, closes=[100, 100], lows=[98, 94], highs=[100, 100])
    assert result.stats["trade_count"] == 1
    # cash spent 10*95; marked at close 100 -> equity 10000 + 50
    assert result.equity_points[-1][1] == Decimal(9050) + 10 * Decimal(100)


@pytest.mark.asyncio
async def test_limit_sell_fills_when_high_crosses():
    def strategy(ctx: StrategyContext) -> None:
        n = len(ctx.closes())
        if n == 1:
            ctx.buy(10)
        elif n == 2:
            ctx.sell(10, limit_price=110)  # high 109: no fill
        elif n == 3:
            ctx.sell(10, limit_price=110)  # high 111: fills at 110

    result = await run(strategy, closes=[100, 105, 105],
                       lows=[100, 105, 105], highs=[100, 109, 111])
    assert result.stats["trade_count"] == 2
    # bought 100, sold 110 -> realized +100, flat at end
    assert result.stats["final_equity"] == 10100.0
    assert result.stats["win_rate"] == 1.0


@pytest.mark.asyncio
async def test_unaffordable_buy_skipped():
    def strategy(ctx: StrategyContext) -> None:
        if len(ctx.closes()) == 1:
            ctx.buy(10)  # 10*900 = 9000 <= 10000: fills
            ctx.buy(10)  # another 9000 > 1000 left: skipped

    result = await run(lambda ctx: strategy(ctx), closes=[900, 900], cash="10000")
    assert result.stats["trade_count"] == 1
    assert result.equity_points[-1][1] == Decimal(10000)


@pytest.mark.asyncio
async def test_sell_when_flat_goes_short_like_live_ledger():
    def strategy(ctx: StrategyContext) -> None:
        if len(ctx.closes()) == 1:
            ctx.sell(5)

    result = await run(strategy, closes=[100, 90])
    # short 5 @100: cash 10500, liability 5*90 -> equity 10050
    assert result.equity_points[-1][1] == Decimal(10050)


# ---- portfolio math (avg cost, round trips) ----


def test_portfolio_avg_cost_and_realized():
    p = _Portfolio(cash=Decimal(10000))
    p.apply(Decimal(10), Decimal(100))
    p.apply(Decimal(10), Decimal(120))
    assert p.position == 20
    assert p.avg_cost == Decimal(110)
    p.apply(Decimal(-10), Decimal(150))  # partial close
    assert p.realized_pnl == 10 * (150 - 110)
    assert p.closed_trips == 0  # still long 10
    p.apply(Decimal(-10), Decimal(90))  # close out at a loss on remainder
    assert p.position == 0
    assert p.closed_trips == 1
    assert p.winning_trips == 1  # trip net: +400 - 200 > 0
    assert p.realized_pnl == Decimal(200)


def test_portfolio_flip_closes_trip_and_opens_new():
    p = _Portfolio(cash=Decimal(10000))
    p.apply(Decimal(10), Decimal(100))
    p.apply(Decimal(-20), Decimal(110))  # close long 10, open short 10
    assert p.position == -10
    assert p.avg_cost == Decimal(110)
    assert p.closed_trips == 1
    assert p.winning_trips == 1
    assert p.realized_pnl == Decimal(100)


# ---- stats ----


def test_stats_on_known_equity_curve():
    equity = [Decimal(10000), Decimal(10100), Decimal(10000), Decimal(10300)]
    p = _Portfolio(cash=Decimal(10300))
    p.trade_count = 3
    p.closed_trips = 2
    p.winning_trips = 1
    stats = compute_stats(equity, Decimal(10000), p)
    assert stats["total_return"] == 0.03
    assert stats["max_drawdown"] == round(100 / 10100, 6)
    assert stats["trade_count"] == 3
    assert stats["win_rate"] == 0.5
    assert stats["final_equity"] == 10300.0
    # returns: +0.01, -1/101, +0.03 -> mean 0.010033, sample sd 0.0199505
    assert stats["sharpe"] == pytest.approx(0.010033 / 0.0199505 * 252**0.5, rel=1e-3)


def test_stats_sharpe_none_when_flat_or_single_bar():
    p = _Portfolio(cash=Decimal(100))
    assert compute_stats([Decimal(100), Decimal(100), Decimal(100)], Decimal(100), p)["sharpe"] is None
    assert compute_stats([Decimal(100)], Decimal(100), p)["sharpe"] is None


def test_stats_win_rate_none_without_closed_trips():
    p = _Portfolio(cash=Decimal(100))
    stats = compute_stats([Decimal(100), Decimal(110)], Decimal(100), p)
    assert stats["win_rate"] is None


# ---- replay contract ----


@pytest.mark.asyncio
async def test_replay_emits_one_equity_point_per_bar():
    result = await run(lambda ctx: None, closes=[10, 11, 12, 13])
    assert len(result.equity_points) == 4
    assert all(isinstance(d, date) for d, _ in result.equity_points)
    assert [float(e) for _, e in result.equity_points] == [10000.0] * 4


@pytest.mark.asyncio
async def test_replay_empty_bars_raises():
    with pytest.raises(EvaluationError, match="no bars"):
        await replay([], LambdaEvaluator(lambda ctx: None), Decimal(1000))


@pytest.mark.asyncio
async def test_evaluation_failure_identifies_bar():
    def boom(ctx: StrategyContext) -> None:
        if len(ctx.closes()) == 3:
            raise RuntimeError("bang")

    with pytest.raises(EvaluationError, match=r"bar 2 .* bang"):
        await run(boom, closes=[1, 2, 3, 4])


@pytest.mark.asyncio
async def test_build_evaluator_template_track_drives_replay():
    spec = validate_spec(
        {"kind": "template", "template": "sma_cross", "symbol": "TEST",
         "params": {"fast": 2, "slow": 3, "quantity": 10}}
    )
    result = await replay(
        make_bars([5, 5, 5, 4, 4, 6, 7, 6, 5]), build_evaluator(spec), Decimal(10000)
    )
    # buy 10 @6 (bar 5), sell 10 @5 (bar 8): one losing round trip
    assert result.stats["trade_count"] == 2
    assert result.stats["win_rate"] == 0.0
    assert result.stats["final_equity"] == 9990.0


@pytest.mark.asyncio
async def test_build_evaluator_code_track_uses_sandbox():
    # Task 8.4: a code-track spec builds a sandbox-backed evaluator (one
    # subprocess per evaluate) rather than raising. The subprocess path
    # itself is exercised in test_sandbox_worker.py; here we only assert the
    # evaluator is constructed and exposes the BarEvaluator contract.
    spec = validate_spec({"kind": "code", "symbol": "TEST", "source": "def on_bar(ctx): pass"})
    evaluator = build_evaluator(spec)
    assert hasattr(evaluator, "evaluate")
    assert callable(evaluator.evaluate)
