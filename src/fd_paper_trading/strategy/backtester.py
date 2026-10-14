"""Fill-at-close daily-bar backtester (design D5, spec: strategy-backtest).

Replays daily bars oldest-first through the shared ``on_bar(ctx)`` contract
against an in-memory portfolio. Fill rules:

- MARKET signals fill at the signal bar's close.
- LIMIT buys fill at the limit iff the bar's low is at or below it; LIMIT
  sells fill at the limit iff the bar's high is at or above it. Unfilled
  limits expire at bar end (no queue modeling — documented optimistic
  assumption).
- A buy whose cost exceeds available cash is skipped; the run continues.

The portfolio mirrors the live ledger semantics (signed position, average
cost, realized PnL per round trip) so backtest stats are comparable to live
paper behavior. Stats: ``total_return``, ``max_drawdown``, ``sharpe`` (daily
returns, √252-annualized), ``win_rate`` (per closed round trip), and
``trade_count`` (filled signals), plus one equity point per bar where
equity = cash + position marked at the bar's close.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Protocol

from ..marketdata.provider import Bar
from .ctx import Signal, StrategyContext
from .spec import CodeSpec, StrategySpec, TemplateSpec
from .templates import make_evaluator


class EvaluationError(Exception):
    """A bar evaluation failed; the run must transition to FAILED."""


class BarEvaluator(Protocol):
    """One strategy instance over one replay; holds cross-bar state."""

    async def evaluate(self, ctx: StrategyContext) -> None:
        """Run ``on_bar(ctx)`` for the bar at the end of ``ctx``'s window."""
        ...


class _TemplateBarEvaluator:
    def __init__(self, impl) -> None:
        self._impl = impl

    async def evaluate(self, ctx: StrategyContext) -> None:
        self._impl.on_bar(ctx)


def build_evaluator(spec: StrategySpec) -> BarEvaluator:
    """Build the per-run evaluator for a validated spec."""
    if isinstance(spec, TemplateSpec):
        return _TemplateBarEvaluator(make_evaluator(spec.template, spec.params))
    if isinstance(spec, CodeSpec):
        # Code-track strategies run in an isolated subprocess sandbox (D6).
        from .sandbox_worker import _SandboxBarEvaluator

        return _SandboxBarEvaluator(spec.source)
    raise EvaluationError(f"unsupported spec kind: {type(spec).__name__}")


# ---------------------------------------------------------------------------
# Portfolio (mirrors execution/service.py ledger semantics)
# ---------------------------------------------------------------------------


@dataclass
class _Portfolio:
    cash: Decimal
    position: Decimal = Decimal(0)
    avg_cost: Decimal | None = None
    realized_pnl: Decimal = Decimal(0)
    trade_count: int = 0
    closed_trips: int = 0
    winning_trips: int = 0
    _trip_realized: Decimal = field(default=Decimal(0))

    def apply(self, signed_qty: Decimal, price: Decimal) -> None:
        old = self.position
        new = old + signed_qty
        realized = Decimal(0)
        if old == 0 or (old > 0) == (signed_qty > 0):
            # Opening a trip, or adding in the trip's direction.
            if old == 0:
                self.avg_cost = price
                self._trip_realized = Decimal(0)
            else:
                self.avg_cost = (
                    abs(old) * self.avg_cost + abs(signed_qty) * price
                ) / abs(new)
        else:
            closing = min(abs(old), abs(signed_qty))
            direction = Decimal(1) if old > 0 else Decimal(-1)
            realized = closing * (price - self.avg_cost) * direction
            self._trip_realized += realized
            if new == 0 or (new > 0) != (old > 0):
                # Trip closed (flat or flipped); a flip opens a fresh trip.
                self.avg_cost = price if new != 0 else None
                self.closed_trips += 1
                if self._trip_realized > 0:
                    self.winning_trips += 1
                self._trip_realized = Decimal(0)
            # Partial close: avg_cost unchanged.
        self.position = new
        self.realized_pnl += realized

    def equity(self, mark: Decimal) -> Decimal:
        return self.cash + self.position * mark


def _fill_price(signal: Signal, bar: Bar) -> Decimal | None:
    if signal.limit_price is None:
        return bar.close
    if signal.side == "BUY":
        return signal.limit_price if bar.low <= signal.limit_price else None
    return signal.limit_price if bar.high >= signal.limit_price else None


def _apply_signal(portfolio: _Portfolio, signal: Signal, bar: Bar) -> None:
    price = _fill_price(signal, bar)
    if price is None:
        return  # limit not crossed — expires at bar end
    if signal.side == "BUY":
        if signal.quantity * price > portfolio.cash:
            return  # unaffordable buy skipped
        signed = signal.quantity
    else:
        signed = -signal.quantity
    portfolio.trade_count += 1
    portfolio.cash -= signed * price
    portfolio.apply(signed, price)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


def _sharpe(equity: list[Decimal]) -> float | None:
    rets = [float(equity[i] / equity[i - 1]) - 1.0 for i in range(1, len(equity))]
    if len(rets) < 2:
        return None
    sd = statistics.stdev(rets)
    if sd == 0:
        return None
    return statistics.mean(rets) / sd * math.sqrt(252)


def _max_drawdown(equity: list[Decimal]) -> Decimal:
    peak = equity[0]
    mdd = Decimal(0)
    for eq in equity:
        if eq > peak:
            peak = eq
        if peak > 0:
            dd = (peak - eq) / peak
            if dd > mdd:
                mdd = dd
    return mdd


def compute_stats(
    equity: list[Decimal], initial_cash: Decimal, portfolio: _Portfolio
) -> dict:
    final = equity[-1]
    total_return = float(final / initial_cash) - 1.0 if initial_cash else 0.0
    sharpe = _sharpe(equity)
    win_rate = (
        portfolio.winning_trips / portfolio.closed_trips
        if portfolio.closed_trips
        else None
    )
    return {
        "total_return": round(total_return, 6),
        "max_drawdown": round(float(_max_drawdown(equity)), 6),
        "sharpe": round(sharpe, 6) if sharpe is not None else None,
        "win_rate": round(win_rate, 6) if win_rate is not None else None,
        "trade_count": portfolio.trade_count,
        "final_equity": round(float(final), 4),
    }


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------


@dataclass
class BacktestResult:
    stats: dict
    equity_points: list[tuple[date, Decimal]]  # one per replayed bar


def _bar_date(bar: Bar) -> date:
    return datetime.fromtimestamp(bar.ts, tz=timezone.utc).date()


async def replay(
    bars: list[Bar], evaluator: BarEvaluator, initial_cash: Decimal
) -> BacktestResult:
    """Replay bars through the evaluator; return stats + per-bar equity."""
    if not bars:
        raise EvaluationError("no bars to replay")
    portfolio = _Portfolio(cash=initial_cash)
    curve: list[tuple[date, Decimal]] = []
    equity: list[Decimal] = []
    for i, bar in enumerate(bars):
        ctx = StrategyContext(
            bars[: i + 1], position=portfolio.position, cash=portfolio.cash
        )
        try:
            await evaluator.evaluate(ctx)
        except Exception as exc:
            raise EvaluationError(
                f"evaluation failed on bar {i} ({_bar_date(bar)}): {exc}"
            ) from exc
        for signal in ctx.signals:
            _apply_signal(portfolio, signal, bar)
        eq = portfolio.equity(bar.close)
        equity.append(eq)
        curve.append((_bar_date(bar), eq))
    return BacktestResult(
        stats=compute_stats(equity, initial_cash, portfolio), equity_points=curve
    )
