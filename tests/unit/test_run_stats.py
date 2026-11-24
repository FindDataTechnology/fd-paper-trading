"""Headline stats: the arithmetic both tracks share (change add-paper-run-metrics 1.2).

The point of these tests is not "the numbers are right" (the backtest tests
already pin that) but that the *unmeasurable* cases return None with a reason
instead of a fabricated zero — that is what makes the shared function usable on
a paper run whose curve may be one point long.
"""

from decimal import Decimal

from fd_paper_trading.strategy.run_stats import headline_stats, max_drawdown, sharpe


def test_return_is_measured_against_the_baseline_not_the_last_value():
    # Baseline 87,000 (a run that started with existing positions), ending 95,700.
    stats = headline_stats([Decimal(87000), Decimal(95700)], Decimal(87000), 0)
    assert stats["total_return"] == 0.1
    assert stats["final_equity"] == 95700.0


def test_single_point_has_no_return_stats_but_does_not_fake_zero():
    stats = headline_stats([Decimal(100)], Decimal(100), 0)
    assert stats["total_return"] == 0.0  # one point == baseline, a measured zero
    assert stats["sharpe"] is None
    assert stats["total_return"] is not None
    assert stats["notes"]  # reason present


def test_empty_curve_is_unmeasurable_everywhere():
    stats = headline_stats([], Decimal(100), 3)
    assert stats["total_return"] is None
    assert stats["max_drawdown"] is None
    assert stats["sharpe"] is None
    assert stats["trade_count"] == 3
    assert stats["notes"]


def test_flat_curve_has_no_sharpe_but_zero_drawdown():
    stats = headline_stats([Decimal(100), Decimal(100), Decimal(100)], Decimal(100), 0)
    assert stats["sharpe"] is None
    assert stats["max_drawdown"] == 0.0
    assert stats["total_return"] == 0.0


def test_drawdown_measures_peak_to_trough():
    assert max_drawdown([Decimal(100), Decimal(90), Decimal(95)]) == Decimal("0.1")


def test_sharpe_none_below_two_returns():
    assert sharpe([Decimal(100)]) is None
    assert sharpe([Decimal(100), Decimal(101)]) is None  # 1 return -> stdev undefined
    assert sharpe([Decimal(100), Decimal(101), Decimal(102)]) is not None
