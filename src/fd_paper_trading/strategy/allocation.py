"""Score-proportional allocation: how a cash budget is split by a score vector.

This is **allocation**, not validation. The contract owns "non-negative, Σ ≤ 1,
symbols inside the universe" (see ``weights.py`` for why the engine keeps no
second copy of those rules); this module owns the orthogonal question of *how
much of the budget each name gets*, and it hands its answer back through the
contract before returning. That ordering is deliberate: an allocation rule that
returned weights without asking the contract could produce a mapping the engine
would reject three layers later, and the rejection would name the weights rather
than the rule that built them.

Why a rule at all: the factor suite documents exactly two weight sources —
equal weight (``1/N``) and score weight (share ∝ score) — and forbids
optimizers (no risk parity, no mean-variance, no Black-Litterman). Equal weight
needs no module; score weight needs one place to live so that the evidence
scripts, the skills and any future endpoint cannot each invent a slightly
different rounding.

Rounding is by largest remainder in integer units of ``10**-decimals``, so the
weights sum to the budget **exactly**, not approximately. Approximate summing is
the failure mode that matters here: 0.9 split nine ways in floats leaves a
residue that the engine silently keeps as cash, and the resulting drift looks
like a strategy decision instead of arithmetic.

Scores must be finite and non-negative-able: negative scores mean the caller has
not decided what a short is, and this module refuses to guess (shorting is not
representable in ``TargetWeights``). Zero scores are simply excluded — a name
that scores zero gets no budget rather than a token position.
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Mapping, Sequence

from .weights import validate_weights

__all__ = ["score_proportional_weights"]


def score_proportional_weights(
    scores: Mapping[str, float],
    *,
    budget: float = 0.9,
    decimals: int = 4,
    universe: Sequence[str] | None = None,
) -> dict[str, float]:
    """Split ``budget`` across names in proportion to their scores.

    Args:
        scores: ``{symbol: score}``. Only positive scores receive budget;
            negative or non-finite scores raise rather than being clipped,
            because clipping would turn "this caller has not decided about
            shorts" into a silent long-only decision.
        budget: Total weight to allocate, in ``(0, 1]``. Defaults to ``0.9``,
            the factor suite's convention of holding 10% cash against slippage
            and rounding residue.
        decimals: Weight precision. The allocation can never be finer than this
            (a whole-share purchase needs far less), and fixing it makes the
            output byte-stable across runs.
        universe: Optional universe to check membership against. When given, the
            contract check runs with the full universe so a stray symbol is
            reported as out-of-universe instead of being validated on its own.

    Returns:
        Weights sorted by descending weight then symbol, summing to ``budget``
        exactly. Names whose share rounds to zero are omitted (not emitted as
        ``0.0``) so that "in the universe" and "targeted" stay distinguishable.

    Raises:
        ValueError: no positive score, out-of-range budget, negative/non-finite
            score, stray symbol, or any contract violation in the result.
    """
    positive: dict[str, float] = {}
    for name, raw in scores.items():
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError(f"score for {name!r} is not finite: {raw!r}")
        if value < 0:
            raise ValueError(
                f"score for {name!r} is negative ({value!r}); drop the short side "
                "before allocating instead of relying on a clip"
            )
        if value > 0:
            positive[name] = value
    if not positive:
        raise ValueError("no positive score: score-weighted allocation needs at least one")
    if not 0 < budget <= 1:
        raise ValueError(f"budget must be in (0, 1], got {budget!r}")
    if decimals < 0:
        raise ValueError(f"decimals must be >= 0, got {decimals!r}")

    units = 10**decimals
    # budget 是调用方给的十进制数（0.9），用 str 转 Decimal 才不会把 0.9 变成
    # 0.9000000000000000222…；总量单位数必须整数，否则 Σ 永远差一个末位。
    total_units = int(Decimal(str(budget)) * units)
    score_sum = sum(positive.values())

    exact = {
        name: Decimal(str(score)) / Decimal(str(score_sum)) * total_units
        for name, score in positive.items()
    }
    allocated = {name: int(value) for name, value in exact.items()}

    # 最大余数法：小数部分大的先补，补到 Σ 恰好等于预算。余数并列时按分数、
    # 再按符号名定序，否则同一输入在不同 dict 顺序下会给出不同分配。
    remainder = total_units - sum(allocated.values())
    order = sorted(
        exact,
        key=lambda name: (exact[name] - allocated[name], positive[name], name),
        reverse=True,
    )
    for name in order[:remainder]:
        allocated[name] += 1

    weights = {name: unit / units for name, unit in allocated.items() if unit > 0}
    if not weights:
        raise ValueError(
            f"budget {budget!r} is below the smallest representable share at "
            f"{decimals} decimals; raise budget or decimals"
        )

    if universe is not None:
        stray = sorted(set(weights) - set(universe))
        if stray:
            raise ValueError(f"weights outside universe: {stray}")

    violations = validate_weights(
        list(universe) if universe is not None else sorted(weights), weights
    )
    if violations:
        raise ValueError("allocated weights violate the contract: " + "; ".join(violations))
    return dict(sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])))
