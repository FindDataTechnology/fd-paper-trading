"""Headline statistics shared by the backtest and the live-paper track.

## Why one module

A live paper run never wrote ``run.stats`` at all: the six ``run.stats =``
assignments in ``api/routes/strategies.py`` all sit on backtest branches. The
result was a run-history row that showed ``-`` for return/Sharpe/trades — not
"a bad strategy", but "unanswerable".

The fix must not be a second stats implementation. Two copies of the same
arithmetic drift, and the copy nobody re-reads is the one that drifts — the
same reason ``risk_overlay.py`` holds one overlay for two engines. So the pure
arithmetic lives here and both tracks call it; each keeps only what is genuinely
its own (the backtester's round-trip ``win_rate``, the paper run's order-derived
``trade_count``).

## What this module deliberately does not do

- It does not decide *which* equity points to use. The baseline (a backtest's
  measurement boundary, a paper run's first equity point) is the caller's
  fact, not this module's guess.
- It does not fabricate a value when it cannot compute one. Fewer than two
  points yields ``None`` plus a note; a flat curve yields ``None`` Sharpe. Zero
  is a number that means "measured, and it was zero" — never a stand-in for
  "could not measure".
"""

from __future__ import annotations

import math
import statistics
from decimal import Decimal
from typing import Sequence

#: Below two points nothing annualised is defined; below one nothing is at all.
MIN_POINTS_FOR_STATS = 2


def sharpe(equity: Sequence[Decimal]) -> float | None:
    """Daily returns, annualised with sqrt(252); None when undefined."""
    rets = [float(equity[i] / equity[i - 1]) - 1.0 for i in range(1, len(equity))]
    if len(rets) < 2:
        return None
    sd = statistics.stdev(rets)
    if sd == 0:
        return None
    return statistics.mean(rets) / sd * math.sqrt(252)


def max_drawdown(equity: Sequence[Decimal]) -> Decimal:
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


def headline_stats(
    equity: Sequence[Decimal],
    baseline: Decimal,
    trade_count: int,
) -> dict:
    """Total return, max drawdown, Sharpe, final equity, trade count.

    ``baseline`` is the equity the return is measured from — the backtester's
    measurement boundary, or a paper run's first equity point (which is *not*
    the account's opening balance when the account held positions beforehand).
    """
    if not equity:
        return {
            "total_return": None,
            "max_drawdown": None,
            "sharpe": None,
            "final_equity": None,
            "trade_count": trade_count,
            "notes": ["no equity points recorded"],
        }
    final = equity[-1]
    total_return = float(final / baseline) - 1.0 if baseline else None
    notes: list[str] = []
    if len(equity) < MIN_POINTS_FOR_STATS:
        notes.append(
            f"equity curve has {len(equity)} point(s); "
            f"annualised statistics need >= {MIN_POINTS_FOR_STATS}"
        )
    return {
        "total_return": round(total_return, 6) if total_return is not None else None,
        "max_drawdown": round(float(max_drawdown(equity)), 6),
        "sharpe": _round_or_none(sharpe(equity)),
        "final_equity": round(float(final), 4),
        "trade_count": trade_count,
        **({"notes": notes} if notes else {}),
    }


def _round_or_none(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None
