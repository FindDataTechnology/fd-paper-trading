"""Behavior annotations for every tool the MCP server registers.

Each tool declares the four protocol hints so a client can decide its
auto-approval policy without reading the implementation:

- ``readOnlyHint``: the call changes no state.
- ``destructiveHint``: the call may delete or irreversibly overwrite something
  the caller cannot recover.
- ``idempotentHint``: repeating the same call leaves the same result and state.
- ``openWorldHint``: the result depends on an external, mutable entity set
  (market data, account/order/run state) rather than closed reference data.

Every name is registered explicitly even when several share a profile: that is
what makes a forgotten tool fail loudly instead of inheriting a default.

Judgment calls worth spelling out:

- ``cancel_order`` is *not* destructive: it flips the order to CANCELLED and
  keeps the row plus its fills, and the caller can place a new order. It is
  also *not* idempotent: a repeat hits the API's ``not_cancellable`` branch and
  returns an error, so the result differs even though the state does not.
- ``stop_strategy`` is idempotent: the API returns an already-terminal run
  unchanged (``if run.status not in ACTIVE_RUN_STATUSES: return run``).
- ``update_strategy`` is the only destructive tool: it replaces ``spec`` in
  place and the previous spec content is not retained anywhere (the version
  counter increments, but there is no spec history).
- Read tools that reflect mutable service/market state (accounts, orders,
  positions, runs) are open-world; ``list_strategy_templates`` reads built-in
  static templates, so it is closed.
- ``openWorldHint`` follows the **entity set the tool's result is about**, not
  whether the tool writes. ``create_account`` answers with an account drawn from
  the same mutable, non-enumerable account set that ``list_accounts`` reads, so
  both are open-world — marking one closed and the other open would contradict
  itself within one service. Same for ``create_strategy`` / ``list_strategies``.
  A write is closed only when its answer depends solely on built-in constants,
  which here means no tool at all: ``list_strategy_templates`` is the only
  closed tool in this service, and it is a pure read.
"""
from __future__ import annotations

from typing import Any

#: The four flags every tool must declare, in manifest order.
HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

#: A read of closed reference data: same input, same answer, no state.
READ_ONLY_CLOSED: dict[str, Any] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

#: A read whose answer reflects mutable market/account/order/run state.
READ_ONLY_OPEN: dict[str, Any] = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

#: An append-only write that creates a new resource: a repeat creates a second
#: one, so it is not idempotent. The new resource is open-world when its effect
#: depends on live market state (an order fills at the current price, a run
#: trades the live paper world).
WRITE_NEW_OPEN: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}

#: An in-place overwrite of caller-owned content. The previous content is not
#: retained, so it is destructive. Repeating the same PATCH converges on the
#: same row (a normalized spec does not bump the version twice) -> idempotent.
#: Open-world: the strategy it overwrites lives in the same mutable strategy
#: set that ``list_strategies`` reports, so the tool's result is about that set.
WRITE_OVERWRITE_OPEN: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
}

#: A status transition on mutable trading state. ``cancel_order`` answers
#: ``not_cancellable`` on a repeat, so the result differs -> non-idempotent.
WRITE_TRANSITION_OPEN: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}

#: A status transition the API makes idempotent by returning an already-terminal
#: run unchanged. Not destructive: stopping cancels resting LIMIT orders but
#: never closes positions, and the run row is kept.
WRITE_TRANSITION_OPEN_IDEMPOTENT: dict[str, Any] = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}

#: Tool name -> flags. Every tool the server registers appears here exactly
#: once; each value is a fresh dict so one entry cannot mutate another.
TOOL_ANNOTATIONS: dict[str, dict[str, Any]] = {
    # ---- trading ----
    "place_order": dict(WRITE_NEW_OPEN),
    "cancel_order": dict(WRITE_TRANSITION_OPEN),
    "list_orders": dict(READ_ONLY_OPEN),
    "list_accounts": dict(READ_ONLY_OPEN),
    "get_account": dict(READ_ONLY_OPEN),
    # Open like list_accounts: the answer is an account from the same mutable set.
    "create_account": dict(WRITE_NEW_OPEN),
    "list_positions": dict(READ_ONLY_OPEN),
    # ---- market ----
    # The instrument universe comes from an external ontology that changes over
    # time; bars come from a live provider.
    "search_instruments": dict(READ_ONLY_OPEN),
    "get_bars": dict(READ_ONLY_OPEN),
    # ---- strategy ----
    "list_strategy_templates": dict(READ_ONLY_CLOSED),
    # Open like list_strategies: same mutable strategy set.
    "create_strategy": dict(WRITE_NEW_OPEN),
    "update_strategy": dict(WRITE_OVERWRITE_OPEN),
    "list_strategies": dict(READ_ONLY_OPEN),
    "get_strategy": dict(READ_ONLY_OPEN),
    "run_backtest": dict(WRITE_NEW_OPEN),
    "get_strategy_run": dict(READ_ONLY_OPEN),
    "list_strategy_runs": dict(READ_ONLY_OPEN),
    "start_strategy": dict(WRITE_NEW_OPEN),
    "stop_strategy": dict(WRITE_TRANSITION_OPEN_IDEMPOTENT),
}


def annotations_for(name: str) -> dict[str, Any]:
    """Return a copy of ``name``'s flags.

    An unknown name raises instead of returning a default: a silent default is
    exactly how a newly added tool would ship without a declaration.
    """
    try:
        return dict(TOOL_ANNOTATIONS[name])
    except KeyError:
        raise KeyError(
            f"tool {name!r} has no declared behavior annotations; add it to "
            "fd_paper_trading.mcp.annotations.TOOL_ANNOTATIONS"
        ) from None
