"""Sandbox worker behavior tests (design D6, task 8.5).

Exercises the host-side ``_SandboxBarEvaluator`` against a real subprocess:
signal emission, crash propagation, wall-clock timeout on a CPU spin, and a
memory-growth runaway killed by the timeout backstop.
"""

from decimal import Decimal

import pytest

import fd_trade.strategy.sandbox_worker as sw
from fd_trade.marketdata.provider import Bar
from fd_trade.strategy.backtester import EvaluationError
from fd_trade.strategy.ctx import StrategyContext
from fd_trade.strategy.sandbox_worker import _SandboxBarEvaluator


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
