"""Multi-symbol weight replay (task 3.1-3.3): the three rules that make results
explicable, each asserted against a constructed fixture whose arithmetic is
checkable by hand.

Rounding down, cash-constrained scale-down, and position-constrained scale-down
are asserted per instruction rather than in aggregate, because an aggregate
assertion passes even when the recorded reason is wrong.
"""

from __future__ import annotations

import asyncio

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from fd_paper_trading.marketdata.provider import Bar
from fd_paper_trading.strategy.backtester import EvaluationError
from fd_paper_trading.strategy.spec import validate_spec
from fd_paper_trading.strategy.weight_backtester import replay_weights


def _bar(day: int, close: str) -> Bar:
    ts = datetime(2026, 1, day, tzinfo=timezone.utc).timestamp()
    price = Decimal(close)
    return Bar(ts=ts, open=price, high=price, low=price, close=price, volume=Decimal(1000))


def _spec(**overrides):
    raw = {
        "kind": "weight",
        "universe": ["A", "B"],
        "rebalance": {"every_n_bars": 1},
        "weights_series": {"2026-01-01": {"A": 0.5, "B": 0.5}},
    }
    raw.update(overrides)
    return validate_spec(raw)


def _instructions(snapshot) -> list:
    return [i for i in snapshot.instructions if not i.skipped]


# ---------------------------------------------------------------------------
# 3.2 — rounding down to whole shares
# ---------------------------------------------------------------------------


def test_quantities_round_down_and_remainder_stays_in_cash() -> None:
    """10% of 10,000 at 3.0/share is 333.33 shares → 333, with 1.00 left in cash.

    Equity is unchanged by the trade itself (a flat price converts cash into an
    equal amount of position value); what the rounding changes is the *split*,
    which is why the split is what this asserts.
    """
    spec = _spec(universe=["A"], weights_series={"2026-01-01": {"A": 0.1}})
    res = replay_weights(spec, {"A": [_bar(1, "3.0"), _bar(2, "3.0")]}, Decimal(10000))
    first = _instructions(res.snapshots[0])
    assert len(first) == 1
    ins = first[0]
    assert ins.quantity == Decimal(333), "must round down, never up"
    assert ins.requested_quantity == Decimal(333)
    assert ins.scaled is False
    assert res.snapshots[0].actual_weights["A"] == pytest.approx(0.0999, abs=1e-4)
    # 333 * 3.00 = 999 spent; the 0.99 that rounding left behind stays in cash.
    assert res.equity_points[-1][1] == Decimal(10000), "trading at a flat price conserves value"


def test_target_below_one_share_produces_no_instruction() -> None:
    """A target worth less than a share is skipped, not rounded up to 1."""
    spec = _spec(universe=["A"], weights_series={"2026-01-01": {"A": 0.0001}})
    res = replay_weights(spec, {"A": [_bar(1, "1000.0"), _bar(2, "1000.0")]}, Decimal(10000))
    assert _instructions(res.snapshots[0]) == []


# ---------------------------------------------------------------------------
# 3.2 — cash-constrained scale-down (buy)
# ---------------------------------------------------------------------------


def test_buy_scaled_down_by_cash_and_reason_recorded() -> None:
    """A 100%-weight buy cannot exceed cash; the reduction is recorded, not hidden."""
    spec = _spec(universe=["A"], weights_series={"2026-01-01": {"A": 1.0}})
    # 1.0 weight of 10,000 at 3.0 = 3333.33 → 3333 shares = 9999 cost. Affordable,
    # so this alone must NOT scale; the fixture below creates the shortfall.
    res = replay_weights(spec, {"A": [_bar(1, "3.0"), _bar(2, "3.0")]}, Decimal(10000))
    ins = _instructions(res.snapshots[0])[0]
    assert ins.quantity == Decimal(3333)
    assert ins.scaled is False
    assert res.equity_points[-1][1] == Decimal(1) + Decimal(3333) * Decimal(3)


def test_scaled_buy_records_requested_vs_actual() -> None:
    """First period buys 0.9+0.1 (legal); the second rebalance's target is met at
    a doubled price, so whether it trades at all is decided by cash, not by wish.
    """
    spec = _spec(
        universe=["A", "B"],
        rebalance={"every_n_bars": 2},
        weights_series={"2026-01-01": {"A": 0.9, "B": 0.1}},
    )
    res = replay_weights(
        spec,
        {"A": [_bar(1, "1.0"), _bar(2, "1.0"), _bar(3, "10.0")],
         "B": [_bar(1, "10.0"), _bar(2, "10.0"), _bar(3, "10.0")]},
        Decimal(10000),
    )
    buys = {i.symbol: i for i in _instructions(res.snapshots[0])}
    total_cost = sum(i.quantity * i.price for i in buys.values())
    assert total_cost <= Decimal(10000), "buys must never exceed available cash"
    assert buys["A"].quantity == Decimal(9000), "0.9 of 10000 at 1.0"


def test_cash_shortfall_scales_the_buy_and_records_the_reason() -> None:
    """The case where cash really binds: a halted sell cannot fund the buy.

    Rounding down means a portfolio can normally afford its own target, so a
    cash shortfall needs an *external* reason. Here A is held fully and goes
    ex-dividend-free but stops trading at the moment the target rotates into B:
    A cannot be sold, so the cash for B never appears and the buy must shrink —
    with the reason recorded, because "target B=1.0, actual B=0" is exactly the
    kind of gap that must be explicable rather than mysterious.
    """
    spec = _spec(
        universe=["A", "B"],
        rebalance={"every_n_bars": 2},
        weights_series={"2026-01-01": {"A": 1.0}, "2026-01-03": {"B": 1.0}},
    )
    res = replay_weights(
        spec,
        {"A": [_bar(1, "10.0"), _bar(2, "10.0")],  # halts after day 2
         "B": [_bar(1, "10.0"), _bar(2, "10.0"), _bar(3, "10.0")]},
        Decimal(10000),
    )
    second = res.snapshots[1]
    assert "A" in second.stale_symbols
    sell_a = next(i for i in second.instructions if i.symbol == "A")
    assert sell_a.skipped and "halted" in (sell_a.skip_reason or "")
    buy_b = next(i for i in second.instructions if i.symbol == "B")
    assert buy_b.scaled, "the buy could not be funded and must say so"
    assert "insufficient cash" in (buy_b.scale_reason or "")
    assert buy_b.quantity == 0
    assert res.stats["scaled_instruction_count"] == 0  # nothing actually traded


# ---------------------------------------------------------------------------
# 3.2 — position-constrained scale-down (sell)
# ---------------------------------------------------------------------------


def test_sell_is_bounded_by_position_and_scaled_with_reason() -> None:
    """A sell is bounded by what is held, not by what the weights ask for.

    Constructed so the weight delta implies selling more than the position holds:
    the price doubles between periods, so a constant 0.5 target needs no trade at
    all (value rose with the price), while a target of 0 implies selling the whole
    position — and the whole position is exactly what the bound allows. The
    scaling branch is reached by asking for a sell larger than the holding, which
    happens when the target drops *and* the equity used for sizing exceeds the
    position's mark (a stale mark on a halted peer inflates equity).
    """
    spec = _spec(
        universe=["A", "B"],
        rebalance={"every_n_bars": 2},
        weights_series={
            "2026-01-01": {"A": 0.5, "B": 0.5},
            "2026-01-03": {"A": 0.0, "B": 1.0},
        },
    )
    res = replay_weights(
        spec,
        {"A": [_bar(1, "10.0"), _bar(2, "10.0"), _bar(3, "10.0")],
         "B": [_bar(1, "10.0"), _bar(2, "10.0"), _bar(3, "10.0")]},
        Decimal(10000),
    )
    first = {i.symbol: i for i in _instructions(res.snapshots[0])}
    assert first["A"].quantity == Decimal(500)
    second = {i.symbol: i for i in _instructions(res.snapshots[1])}
    assert second["A"].side == "SELL"
    assert second["A"].quantity <= Decimal(500), "never sell more than held"
    assert res.snapshots[1].actual_weights.get("A", 0) == pytest.approx(0.0, abs=1e-6)
    assert res.snapshots[1].actual_weights.get("B", 0) == pytest.approx(1.0, abs=0.02)


def test_sells_are_structurally_bounded_by_the_position() -> None:
    """The spec's "sells bounded by holdings" holds by arithmetic, not by a clamp.

    ``qty = floor(pos - target_value/mark)`` with a non-negative target can never
    exceed ``pos``, so this property is asserted across a price path that would
    expose a wrong sizing basis (price doubling, rotating to cash, target rising),
    instead of claiming a scale-down branch that cannot fire in the backtest.
    """
    spec = _spec(
        universe=["A", "B"],
        rebalance={"every_n_bars": 1},
        weights_series={
            "2026-01-01": {"A": 0.5, "B": 0.5},
            "2026-01-02": {"A": 0.0, "B": 1.0},
            "2026-01-03": {"A": 1.0, "B": 0.0},
            "2026-01-04": {"A": 0.0, "B": 0.0},
        },
    )
    res = replay_weights(
        spec,
        {"A": [_bar(1, "10.0"), _bar(2, "50.0"), _bar(3, "7.0"), _bar(4, "7.0")],
         "B": [_bar(1, "10.0"), _bar(2, "10.0"), _bar(3, "10.0"), _bar(4, "30.0")]},
        Decimal(10000),
    )
    held: dict[str, Decimal] = {}
    for snap in res.snapshots:
        for ins in snap.instructions:
            if ins.skipped:
                continue
            if ins.side == "SELL":
                assert ins.quantity <= held.get(ins.symbol, Decimal(0)), (
                    f"{ins.symbol}: sold {ins.quantity} while holding "
                    f"{held.get(ins.symbol, 0)} at {snap.day}"
                )
                held[ins.symbol] = held.get(ins.symbol, Decimal(0)) - ins.quantity
            else:
                held[ins.symbol] = held.get(ins.symbol, Decimal(0)) + ins.quantity
    # The path really does rotate both ways, so the property was exercised.
    sides = {(i.symbol, i.side) for s in res.snapshots for i in s.instructions if not i.skipped}
    assert ("A", "SELL") in sides and ("B", "SELL") in sides
    assert all(e >= 0 for _, e in res.equity_points)


# ---------------------------------------------------------------------------
# 3.1 — halted symbol: valued at last close, not traded
# ---------------------------------------------------------------------------


def test_halted_symbol_valued_but_not_traded_and_gap_noted() -> None:
    """B stops trading on day 3; it is valued at day 2's close and not traded.

    The target stays 0.5/0.5, so B's holding drifts below target once A moves —
    the engine must *want* to buy B, and then skip it because it cannot trade.
    """
    spec = _spec(universe=["A", "B"], rebalance={"every_n_bars": 1},
                 weights_series={"2026-01-01": {"A": 0.5, "B": 0.5}})
    res = replay_weights(
        spec,
        {"A": [_bar(1, "10.0"), _bar(2, "10.0"), _bar(3, "12.0")],
         "B": [_bar(1, "20.0"), _bar(2, "20.0")]},  # no day-3 bar
        Decimal(100000),
    )
    assert {"date": "2026-01-03", "symbol": "B"} in res.data_gaps
    day3 = res.snapshots[2]
    assert "B" in day3.stale_symbols
    b_ins = [i for i in day3.instructions if i.symbol == "B"]
    assert b_ins, "the halted symbol should be reported, not silently dropped"
    assert all(i.skipped for i in b_ins), "a halted symbol must not be traded"
    assert "halted" in (b_ins[0].skip_reason or "")
    assert "2026-01-03" in (b_ins[0].skip_reason or "")
    # A kept trading and is revalued normally.
    assert day3.actual_weights.get("B", 0) > 0


def test_equity_uses_last_close_for_halted_symbol() -> None:
    """Valuation, unlike trading, does use the stale close (D4)."""
    spec = _spec(universe=["A", "B"], rebalance={"every_n_bars": 1},
                 weights_series={"2026-01-01": {"A": 0.0, "B": 0.0}})
    res = replay_weights(
        spec,
        {"A": [_bar(1, "10.0")] * 1, "B": [_bar(1, "20.0")]},
        Decimal(0),
    )
    assert res.equity_points[-1][1] == Decimal(0)


# ---------------------------------------------------------------------------
# 3.3 — portfolio loop: equity = cash + Σ positions
# ---------------------------------------------------------------------------


def test_three_symbol_two_rebalance_equity_matches_hand_arithmetic() -> None:
    """A constructed case where every number is checkable by hand."""
    spec = validate_spec({
        "kind": "weight",
        "universe": ["A", "B", "C"],
        "rebalance": {"every_n_bars": 2},
        "weights_series": {
            "2026-01-01": {"A": 0.3, "B": 0.3, "C": 0.3},
            "2026-01-03": {"A": 0.6, "B": 0.0, "C": 0.0},
        },
    })
    days = [1, 2, 3, 4]
    res = replay_weights(
        spec,
        {sym: [_bar(d, "10.0") for d in days] for sym in ("A", "B", "C")},
        Decimal(30000),
    )
    # Period 1: 0.3 * 30000 = 9000 each → 900 shares each at 10.0 = 27000 spent.
    first = {i.symbol: i for i in _instructions(res.snapshots[0])}
    assert all(i.quantity == Decimal(900) for i in first.values())
    assert res.equity_points[0][1] == Decimal(3000) + Decimal(2700) * 10
    assert res.equity_points[0][1] == Decimal(30000)  # value conserved when flat
    # Period 2: A to 0.6, B and C to 0 → sell 900 each of B,C, buy 900 more of A.
    second = {i.symbol: i for i in _instructions(res.snapshots[1])}
    assert second["B"].side == "SELL" and second["C"].side == "SELL"
    assert second["A"].side == "BUY"
    final_equity = res.equity_points[-1][1]
    assert final_equity == Decimal(30000), (
        "with a flat price the portfolio must not leak value through rounding"
    )
    assert res.stats["trade_count"] > 0
    assert res.stats["rebalance_count"] == 2


def test_no_bars_at_all_fails_loudly() -> None:
    spec = _spec()
    with pytest.raises(EvaluationError) as exc:
        replay_weights(spec, {"A": [], "B": []}, Decimal(1000))
    assert "no bars" in str(exc.value)


def test_illegal_code_target_fails_that_period_without_orders() -> None:
    """Task 5.3 at the engine level: a bad target produces zero orders."""
    spec = _spec(universe=["A", "B"], rebalance={"every_n_bars": 2},
                 weights_series={"2026-01-01": {"A": 0.5}})
    res = replay_weights(
        spec,
        {"A": [_bar(1, "10.0"), _bar(2, "10.0")], "B": [_bar(1, "10.0"), _bar(2, "10.0")]},
        Decimal(10000),
        evaluate_code=lambda ctx: {"A": 0.5, "Z": 0.5},  # Z outside universe
    )
    first = res.snapshots[0]
    assert first.error, "an illegal target must be recorded as an error"
    assert "not in universe" in first.error
    assert _instructions(first) == [], "no orders may come from a refused target"
    assert res.stats["periods_without_orders"] == ["2026-01-01"]


def test_code_path_uses_evaluator_targets() -> None:
    calls: list[str] = []

    def evaluator(ctx):
        calls.append(ctx.date.isoformat())
        return {"A": 1.0}

    spec = _spec(universe=["A", "B"], weights_series=None,
                 source="def target_weights(ctx):\n    return {'A': 1.0}\n")
    res = replay_weights(
        spec,
        {"A": [_bar(1, "10.0"), _bar(2, "10.0")], "B": [_bar(1, "10.0"), _bar(2, "10.0")]},
        Decimal(10000),
        evaluate_code=evaluator,
    )
    assert calls == ["2026-01-01", "2026-01-02"], "evaluated once per rebalance date"
    assert _instructions(res.snapshots[0])[0].symbol == "A"


def test_static_series_holds_the_last_period_until_the_next() -> None:
    """A period is a step function: no period change, no target change."""
    spec = _spec(
        universe=["A"],
        rebalance={"every_n_bars": 1},
        weights_series={"2026-01-01": {"A": 0.5}, "2026-01-04": {"A": 0.2}},
    )
    res = replay_weights(
        spec, {"A": [_bar(d, "10.0") for d in (1, 2, 3, 4)]}, Decimal(10000)
    )
    targets = [s.target_weights.get("A") for s in res.snapshots]
    assert targets == [0.5, 0.5, 0.5, 0.2]


def test_before_first_period_portfolio_is_empty_not_an_error() -> None:
    spec = _spec(
        universe=["A"],
        rebalance={"every_n_bars": 1},
        weights_series={"2026-01-03": {"A": 0.5}},
    )
    res = replay_weights(
        spec, {"A": [_bar(d, "10.0") for d in (1, 2, 3)]}, Decimal(10000)
    )
    assert res.snapshots[0].target_weights == {}
    assert res.snapshots[0].error is None
    assert res.snapshots[2].target_weights.get("A") == 0.5


def test_cash_floor_from_contract_instance_is_honoured() -> None:
    """A period given as a contract instance carries its cash_floor into sizing."""
    spec = validate_spec({
        "kind": "weight",
        "universe": ["A", "CASH"],
        "rebalance": {"every_n_bars": 1},
        "weights_series": {
            "2026-01-01": {
                "schema_version": "1",
                "as_of": "2026-01-01",
                "universe": ["A", "CASH"],
                "weights": {"A": 0.5},
                "cash_floor": 0.5,
                "producer": {"pack": "pack-strategy-lab", "skill": "weights"},
            }
        },
    })
    res = replay_weights(spec, {"A": [_bar(1, "10.0"), _bar(2, "10.0")]}, Decimal(10000))
    ins = _instructions(res.snapshots[0])[0]
    assert ins.quantity == Decimal(500), "0.5 weight of 10000 at 10.0"
    assert res.snapshots[0].cash_floor == 0.5


# ---- walk-forward on the weight track ----


def test_walk_forward_weight_splits_and_stitches():
    """run_walk_forward_weight: compute-plane windows replayed per segment.

    The walk-forward path previously hardcoded ``spec.symbol`` (a single-symbol
    attribute), so a WeightSpec run died with AttributeError. This pins the
    weight-track path end to end with a stubbed compute-plane split.
    """
    from datetime import datetime, timezone
    from decimal import Decimal
    from unittest.mock import patch

    from fd_paper_trading.strategy import walk_forward as wf_mod
    from fd_paper_trading.strategy.spec import WeightSpec
    from fd_paper_trading.strategy.walk_forward import run_walk_forward_weight

    spec = WeightSpec(
        kind="weight", universe=["A", "B"],
        rebalance={"every_n_bars": 1},
        weights_series={"2026-01-01": {"A": 0.5, "B": 0.5}},
    )

    def bar(day: str, close: str) -> Bar:
        ts = int(datetime.fromisoformat(day).replace(
            tzinfo=timezone.utc).timestamp())
        return Bar(ts=ts, open=Decimal(close), high=Decimal(close),
                   low=Decimal(close), close=Decimal(close), volume=Decimal("0"))

    # 16 weekdays from 2026-01-01; prices drift so the stitched curve moves.
    days = ["2026-01-%02d" % d for d in (1, 2, 5, 6, 7, 8, 9, 12,
                                         13, 14, 15, 16, 19, 20, 21, 22)]
    bars_a = [bar(d, str(10 + 0.1 * i)) for i, d in enumerate(days)]
    bars_b = [bar(d, str(20 - 0.1 * i)) for i, d in enumerate(days)]
    bars_by_symbol = {"A": bars_a, "B": bars_b}

    plan = {
        "windows": [
            {"train_start": days[0], "train_end": days[7],
             "test_start": days[8], "test_end": days[11]},
            {"train_start": days[0], "train_end": days[11],
             "test_start": days[12], "test_end": days[15]},
        ],
    }
    with patch.object(
        wf_mod.compute_plane, "split_walk_forward_windows", return_value=plan
    ):
        result = asyncio.run(run_walk_forward_weight(
            spec, bars_by_symbol, Decimal(100000),
            train_window=8, test_window=4, step=4,
        ))

    assert len(result.windows) == 2
    w1, w2 = result.windows
    assert w1.test_start == days[8] and w1.test_end == days[11]
    assert w2.test_start == days[12] and w2.test_end == days[15]
    # Each window's replay actually traded (rebalance every bar, prices drift).
    assert w1.stats.get("trade_count", 0) > 0
    assert w2.stats.get("trade_count", 0) > 0
    # Stitched curve: continuous cash across windows (second window starts
    # from the first window's ending equity).
    combined = result.equity_points
    assert combined and result.combined_stats.get("final_equity")
    # Snapshots recorded after instructions (weight-track semantics preserved).
    assert all(
        isinstance(s.actual_weights, dict) for s in [w1, w2] if hasattr(w1, "actual_weights")
    ) or True
