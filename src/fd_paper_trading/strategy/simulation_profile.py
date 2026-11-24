"""The simulation profile: what rules a run was simulated under.

## Why a profile at all

"Backtest says +12%, the live run says +7%" is a question you cannot even ask
honestly today. The paper path charges no fees and the backtest can; the paper
path has no settlement rule and (soon) the backtest will. Without a recorded
statement of *which rules produced this number*, comparing two runs is measuring
the difference between two unstated assumptions.

So every run records its profile once, at creation, and never rewrites it — the
same reason ``strategy_version`` is pinned: later config changes must not
retroactively claim a run was simulated differently than it was.

## The distinction the shape carries

``cost.mode`` is ``"none"`` when the run modelled zero costs and ``"modeled"``
when it modelled fees. Both are *statements*, and both are comparable — a
zero-cost run is not "missing data". Only an absent profile (a run that predates
this) is genuinely "unmodelled", and the comparison treats it as such.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional


def build_profile(
    *,
    cost_model: Any | None,
    settlement: str = "T+0",
    lot: str = "whole_share",
    price_world: Optional[str] = None,
) -> dict:
    """Assemble the profile dict for one run.

    ``cost_model`` may be a ``CostModel`` or a plain mapping; ``None`` means the
    run did not enable a cost model, which is recorded as ``{"mode": "none"}`` —
    modelled-and-zero, not unmodelled.
    """
    return {
        "cost": _cost_block(cost_model),
        "settlement": settlement,
        "lot": lot,
        "price_world": price_world or "unknown",
    }


def _cost_block(cost_model: Any | None) -> dict:
    if cost_model is None:
        return {"mode": "none"}
    if hasattr(cost_model, "enabled"):
        if not cost_model.enabled:
            return {"mode": "none"}
        return {
            "mode": "modeled",
            "commission_rate": str(cost_model.commission_rate),
            "stamp_tax_rate": str(cost_model.stamp_tax_rate),
            "slippage_bps": str(cost_model.slippage_bps),
        }
    if isinstance(cost_model, Mapping):
        return {"mode": "modeled", **{str(k): str(v) for k, v in cost_model.items()}}
    return {"mode": "none"}
