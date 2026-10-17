"""Sandbox worker behavior tests (design D6, task 8.5).

Exercises the host-side ``_SandboxBarEvaluator`` against a real subprocess:
signal emission, crash propagation, wall-clock timeout on a CPU spin, and a
memory-growth runaway killed by the timeout backstop.

The weight track's ``target_weights`` entry point (task 5.1) runs in the same
worker and is asserted here too: the same allowlist rejects the same constructs,
a legal source's mapping reaches the caller, and the rebuilt ``WeightContext``
answers per-symbol questions and refuses out-of-universe ones.
"""

from decimal import Decimal

import pytest

import fd_paper_trading.strategy.sandbox_worker as sw
from fd_paper_trading.marketdata.provider import Bar
from fd_paper_trading.strategy.backtester import EvaluationError
from fd_paper_trading.strategy.ctx import StrategyContext, SymbolSeries, WeightContext
from fd_paper_trading.strategy.sandbox_worker import (
    _SandboxBarEvaluator,
    _WeightSandboxEvaluator,
)


def _bar(ts: int, close: float) -> Bar:
    c = Decimal(str(close))
    return Bar(
        ts=ts, open=c, high=c + Decimal("1"), low=c - Decimal("1"),
        close=c, volume=Decimal(1000),
    )


def _ctx(bars, position="0", cash="100000"):
    return StrategyContext(
        [_bar(i, 100 + i) for i in bars],
        position=Decimal(position), cash=Decimal(cash),
    )


@pytest.mark.asyncio
async def test_sandbox_emits_signals():
    ev = _SandboxBarEvaluator(
        "def on_bar(ctx):\n"
        "    if ctx.closes()[-1] > 100:\n"
        "        ctx.buy(1)\n"
    )
    ctx = _ctx(range(1, 4))
    await ev.evaluate(ctx)
    assert len(ctx.signals) == 1
    assert ctx.signals[0].side == "BUY"
    assert ctx.signals[0].quantity == 1
    assert ctx.signals[0].limit_price is None


@pytest.mark.asyncio
async def test_sandbox_limit_signal():
    ev = _SandboxBarEvaluator(
        "def on_bar(ctx):\n"
        "    ctx.buy(2, limit_price=99.5)\n"
    )
    ctx = _ctx([1])
    await ev.evaluate(ctx)
    assert ctx.signals[0].quantity == 2
    assert ctx.signals[0].limit_price == Decimal("99.5")


@pytest.mark.asyncio
async def test_sandbox_crash_propagates_error():
    ev = _SandboxBarEvaluator("def on_bar(ctx):\n    1/0")
    ctx = _ctx([1])
    with pytest.raises(EvaluationError) as exc:
        await ev.evaluate(ctx)
    assert "on_bar raised" in str(exc.value)


@pytest.mark.asyncio
async def test_sandbox_timeout_on_cpu_spin(monkeypatch):
    monkeypatch.setattr(sw, "_WALL_CLOCK_TIMEOUT", 1.0)
    ev = _SandboxBarEvaluator("def on_bar(ctx):\n    while True:\n        pass")
    ctx = _ctx([1])
    with pytest.raises(EvaluationError):
        await ev.evaluate(ctx)


@pytest.mark.asyncio
async def test_sandbox_memory_growth_killed(monkeypatch):
    monkeypatch.setattr(sw, "_WALL_CLOCK_TIMEOUT", 1.5)
    ev = _SandboxBarEvaluator(
        "def on_bar(ctx):\n"
        "    x = []\n"
        "    while True:\n"
        "        x.extend([0] * 100000)\n"
    )
    ctx = _ctx([1])
    with pytest.raises(EvaluationError):
        await ev.evaluate(ctx)


# ---------------------------------------------------------------------------
# Weight track: target_weights(ctx) in the same sandbox (task 5.1)
# ---------------------------------------------------------------------------


def _day(day: int, close: float) -> Bar:
    """A bar on 2026-01-<day>; the sandbox job carries the date separately."""
    import datetime as dt
    ts = int(dt.datetime(2026, 1, day, tzinfo=dt.timezone.utc).timestamp())
    c = Decimal(str(close))
    return Bar(ts=ts, open=c, high=c, low=c, close=c, volume=Decimal(1000))


def _wctx(series: dict[str, list[Bar]], positions: dict[str, str] | None = None,
          cash: str = "100000", day: int = 1) -> WeightContext:
    import datetime as dt
    return WeightContext(
        day=dt.date(2026, 1, day),
        universe=list(series),
        series={sym: SymbolSeries(sym, bars) for sym, bars in series.items()},
        cash=Decimal(cash),
        positions={k: Decimal(v) for k, v in (positions or {}).items()},
    )


def test_weight_source_with_import_rejected_naming_the_line():
    """The weight entry point is checked by the same allowlist; the line is named."""
    src = "import os\ndef target_weights(ctx):\n    return {'A': 0.5}"
    ev = _WeightSandboxEvaluator(src)
    with pytest.raises(EvaluationError) as exc:
        ev.evaluate(_wctx({"A": [_day(1, 10.0)], "B": [_day(1, 10.0)]}))
    assert "import" in str(exc.value)
    assert "line 1" in str(exc.value)


def test_weight_source_with_underscore_identifier_rejected():
    """A ``_``-prefixed name is rejected on the weight track too."""
    src = "def _helper(ctx):\n    return 1\ndef target_weights(ctx):\n    return {'A': 0.5}"
    ev = _WeightSandboxEvaluator(src)
    with pytest.raises(EvaluationError) as exc:
        ev.evaluate(_wctx({"A": [_day(1, 10.0)]}))
    assert "_helper" in str(exc.value)


def test_weight_source_mapping_reaches_the_caller():
    """A legal target_weights source executes and its floats come back."""
    src = (
        "def target_weights(ctx):\n"
        "    if ctx.current_weights().get('CASH', 1.0) > 0.5:\n"
        "        return {'A': 0.4, 'B': 0.4}\n"
        "    return {'A': 0.5}\n"
    )
    ctx = _wctx({"A": [_day(1, 10.0)], "B": [_day(1, 20.0)]})
    mapping = _WeightSandboxEvaluator(src).evaluate(ctx)
    assert mapping == {"A": 0.4, "B": 0.4}


def test_weight_context_closes_per_symbol_and_rejects_out_of_universe():
    """``ctx.closes(sym)`` is the symbol's own history; an outsider raises."""
    src = (
        "def target_weights(ctx):\n"
        "    if ctx.closes('A')[-1] > 0:\n"
        "        return {'A': 0.5}\n"
        "    return {}\n"
    )
    ctx = _wctx({"A": [_day(1, 10.0), _day(2, 12.0)], "B": [_day(2, 20.0)]}, day=2)
    assert _WeightSandboxEvaluator(src).evaluate(ctx) == {"A": 0.5}

    # The same context refuses a symbol outside the universe: the worker's
    # KeyError surfaces as an evaluation failure, not as an empty series.
    bad = (
        "def target_weights(ctx):\n"
        "    return {'A': 0.5} if ctx.closes('ZZZ')[-1] else {}\n"
    )
    with pytest.raises(EvaluationError) as exc:
        _WeightSandboxEvaluator(bad).evaluate(ctx)
    assert "ZZZ" in str(exc.value)
    assert "not in the universe" in str(exc.value)


def test_weight_source_crash_propagates_as_evaluation_error():
    """A raising target_weights fails the evaluation, like on_bar does."""
    src = "def target_weights(ctx):\n    1/0"
    with pytest.raises(EvaluationError) as exc:
        _WeightSandboxEvaluator(src).evaluate(_wctx({"A": [_day(1, 10.0)]}))
    assert "target_weights raised" in str(exc.value)


def test_build_weight_evaluator_is_none_for_static_and_wired_for_source():
    """The spec-to-evaluator builder: static series needs no sandbox at all."""
    from fd_paper_trading.strategy import build_weight_evaluator, validate_spec

    static = validate_spec({
        "kind": "weight",
        "universe": ["A", "B"],
        "rebalance": {"every_n_bars": 1},
        "weights_series": {"2026-01-01": {"A": 0.5}},
    })
    assert build_weight_evaluator(static) is None

    coded = validate_spec({
        "kind": "weight",
        "universe": ["A", "B"],
        "rebalance": {"every_n_bars": 1},
        "source": "def target_weights(ctx):\n    return {'A': 0.5}",
    })
    evaluate = build_weight_evaluator(coded)
    assert callable(evaluate)
    assert evaluate(_wctx({"A": [_day(1, 10.0)], "B": [_day(1, 10.0)]})) == {"A": 0.5}
