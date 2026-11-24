"""Comparability judgement (change add-paper-run-metrics, tasks 3.x/4.x).

The spec's core demands are tested at the pure-function level, which is where
"refuse to compare" actually lives:

- a differing cost model / profile / window is a reason, and a comparable pair
  is not;
- ``stats`` is None exactly when not comparable — never a zero or empty diff;
- a reported profile that says ``mode: none`` is *modelled zero cost*, and a
  run missing the profile is *unmodelled*; those are different, and both differ
  from each other in the comparison.
"""

from datetime import date

from fd_paper_trading.strategy.run_comparison import compare_runs
from fd_paper_trading.strategy.simulation_profile import build_profile


class _Run:
    def __init__(self, *, sid="s1", version=1, mode="BACKTEST", params=None,
                 stats=None, rid="r1"):
        self.strategy_id = sid
        self.strategy_version = version
        self.mode = mode
        self.params = params or {}
        self.stats = stats
        self.id = rid


def _profile(cost=None, settlement="T+0", lot="whole_share", world="astock_daily"):
    return build_profile(cost_model=cost, settlement=settlement, lot=lot,
                         price_world=world)


PROFILE = _profile()
WINDOW = (date(2026, 1, 1), date(2026, 3, 1))


def test_identical_profile_and_window_is_comparable_with_diffs():
    a = _Run(params={"start": "2026-01-01", "end": "2026-03-01",
                     "initial_cash": "100000", "simulation_profile": PROFILE},
             stats={"total_return": 0.10, "sharpe": 1.0, "trade_count": 4})
    b = _Run(params={"start": "2026-01-01", "end": "2026-03-01",
                     "initial_cash": "100000", "simulation_profile": PROFILE},
             stats={"total_return": 0.07, "sharpe": 0.8, "trade_count": 4})
    r = compare_runs(run=a, other=b, run_window_=WINDOW, other_window=WINDOW,
                     run_stats=a.stats, other_stats=b.stats)
    assert r["comparable"] is True
    assert r["reasons"] == []
    assert r["stats"]["diff"]["total_return"] == -0.03  # other - self


def test_cost_model_difference_is_refused():
    a = _Run(params={"start": "2026-01-01", "end": "2026-03-01",
                     "initial_cash": "100000", "simulation_profile": PROFILE})
    b = _Run(params={"start": "2026-01-01", "end": "2026-03-01",
                     "initial_cash": "100000", "simulation_profile": PROFILE})
    other = compare_runs(
        run=a, other=b,
        run_window_=WINDOW,
        other_window=WINDOW,
        run_stats={"total_return": 0.1},
        other_stats={"total_return": 0.05},
    )
    assert other["comparable"] is True  # sanity: same profile compares

    # Now make the cost blocks differ.
    from fd_paper_trading.execution.costs import CostModel

    b.params["simulation_profile"] = _profile(cost=CostModel(enabled=True))
    r = compare_runs(run=a, other=b, run_window_=WINDOW, other_window=WINDOW,
                     run_stats={"total_return": 0.1}, other_stats={"total_return": 0.05})
    assert r["comparable"] is False
    assert r["stats"] is None
    dims = {x["dimension"] for x in r["reasons"]}
    assert "simulation_profile.cost" in dims


def test_missing_profile_is_unmodelled_and_refused():
    a = _Run(params={"start": "2026-01-01", "end": "2026-03-01",
                     "initial_cash": "100000", "simulation_profile": PROFILE})
    b = _Run(params={"start": "2026-01-01", "end": "2026-03-01",
                     "initial_cash": "100000"})  # predates the field
    r = compare_runs(run=a, other=b, run_window_=WINDOW, other_window=WINDOW,
                     run_stats={"total_return": 0.1}, other_stats={"total_return": 0.05})
    assert r["comparable"] is False
    assert any(x["dimension"] == "simulation_profile" for x in r["reasons"])


def test_non_overlapping_window_is_refused():
    a = _Run(params={"start": "2026-01-01", "end": "2026-01-31",
                     "initial_cash": "100000", "simulation_profile": PROFILE})
    b = _Run(params={"start": "2026-06-01", "end": "2026-07-01",
                     "initial_cash": "100000", "simulation_profile": PROFILE})
    r = compare_runs(run=a, other=b,
                     run_window_=(date(2026, 1, 1), date(2026, 1, 31)),
                     other_window=(date(2026, 6, 1), date(2026, 7, 1)),
                     run_stats={"total_return": 0.1}, other_stats={"total_return": 0.05})
    assert r["comparable"] is False
    assert any(x["dimension"] == "window" for x in r["reasons"])


def test_different_versions_refused():
    a = _Run(version=1, params={"start": "2026-01-01", "end": "2026-03-01",
                                "initial_cash": "100000", "simulation_profile": PROFILE})
    b = _Run(version=2, params={"start": "2026-01-01", "end": "2026-03-01",
                                "initial_cash": "100000", "simulation_profile": PROFILE})
    r = compare_runs(run=a, other=b, run_window_=WINDOW, other_window=WINDOW,
                     run_stats={}, other_stats={})
    assert any(x["dimension"] == "strategy_version" for x in r["reasons"])
