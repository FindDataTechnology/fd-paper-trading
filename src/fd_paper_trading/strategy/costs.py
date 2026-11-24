"""Re-export shim. The cost arithmetic lives in ``execution/costs.py``.

## Why it moved

The paper ledger (``execution/service.py``) must charge the same fees the
backtester models, so it imports the cost model. But ``strategy/`` is a heavier
package: importing anything under it runs ``strategy/__init__``, which pulls in
the runner, which imports the execution service — an import cycle. The module
itself is pure arithmetic with no strategy dependencies, so living under
``execution/`` costs nothing and removes the cycle.

This file stays so every existing import site (``from .costs import ...`` in the
backtester, ``from fd_paper_trading.strategy import costs`` in tests and
scripts) keeps working. There is still exactly one implementation.
"""

from ..execution.costs import (  # noqa: F401
    BPS,
    DEFAULT_COMMISSION_RATE,
    DEFAULT_SLIPPAGE_BPS,
    DEFAULT_STAMP_TAX_RATE,
    CostModel,
    CostTotals,
    FillCost,
    accumulate,
    apply_costs,
)

__all__ = [
    "BPS",
    "DEFAULT_COMMISSION_RATE",
    "DEFAULT_SLIPPAGE_BPS",
    "DEFAULT_STAMP_TAX_RATE",
    "CostModel",
    "CostTotals",
    "FillCost",
    "accumulate",
    "apply_costs",
]
