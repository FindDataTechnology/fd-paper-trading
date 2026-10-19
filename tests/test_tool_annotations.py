"""Behavior annotation contract for the MCP tool surface.

Three things must hold, or a client will pick the wrong auto-approval policy:

1. **Complete**: every registered tool carries all four hints. A missing hint
   forces the client to guess.
2. **Honest**: a tool that changes state must not be marked read-only. The
   expected side-effect set is written out explicitly below and compared with
   what the runtime registered — if the implementation gains a side effect and
   the declaration does not, this file goes red.
3. **Covered exactly**: the declaration table and the runtime registry are the
   same set, so a newly added tool cannot slip in with a silent default.

Two judgment calls are pinned here rather than left implicit:
``cancel_order`` is *not* idempotent (a repeat hits the API's
``not_cancellable`` branch, so the result differs), while ``stop_strategy`` is
(the API returns an already-terminal run unchanged).
"""
from __future__ import annotations

import asyncio

import pytest

from fd_paper_trading.mcp.annotations import (
    HINTS,
    TOOL_ANNOTATIONS,
    annotations_for,
)
from fd_paper_trading.mcp.server import mcp

#: Tools that write persistent state. This is an explicit expectation, not a
#: value derived from the implementation — that is what lets it detect a
#: declaration that drifted from the code.
WRITES_STATE = {
    "place_order",
    "cancel_order",
    "create_account",
    "create_strategy",
    "update_strategy",
    "run_backtest",
    "start_strategy",
    "stop_strategy",
}

#: Writes that may delete or irreversibly overwrite caller-owned content.
DESTRUCTIVE = {"update_strategy"}

#: Tools whose result is about a mutable, non-enumerable entity set (live
#: market data, or mutable account/order/strategy/run state). Note this is
#: orthogonal to read-vs-write: ``create_account`` and ``create_strategy`` are
#: open-world for the same reason ``list_accounts`` and ``list_strategies`` are
#: — same entity set. Only ``list_strategy_templates`` answers from built-in
#: constants, so it is the sole closed tool.
OPEN_WORLD = {
    "place_order",
    "cancel_order",
    "list_orders",
    "list_accounts",
    "get_account",
    "list_positions",
    "search_instruments",
    "get_bars",
    "list_strategies",
    "get_strategy",
    "run_backtest",
    "get_strategy_run",
    "list_strategy_runs",
    "start_strategy",
    "stop_strategy",
    "create_account",
    "create_strategy",
    "update_strategy",
}


@pytest.fixture(scope="module")
def runtime() -> dict[str, dict]:
    """Every tool's flags as the live registry reports them."""
    out: dict[str, dict] = {}
    for tool in asyncio.run(mcp._list_tools()):
        a = tool.annotations
        assert a is not None, f"{tool.name} declares no behavior annotations"
        out[tool.name] = {k: getattr(a, k) for k in HINTS}
    return out


def test_every_registered_tool_declares_all_four_hints(runtime):
    assert runtime, "runtime registry is empty"
    for name, flags in runtime.items():
        for key in HINTS:
            assert flags[key] is not None, f"{name}.{key} is not declared"


def test_annotation_table_covers_exactly_the_runtime_tool_set(runtime):
    assert set(TOOL_ANNOTATIONS) == set(runtime), (
        "declaration table and runtime registry differ: a new tool must be "
        "added to annotations.TOOL_ANNOTATIONS"
    )


def test_tools_that_write_state_are_not_marked_readonly(runtime):
    for name, flags in runtime.items():
        if name in WRITES_STATE:
            assert flags["readOnlyHint"] is False, (
                f"{name} writes persistent state but is marked read-only — "
                "a client would auto-approve it"
            )


def test_readers_are_readonly_and_idempotent(runtime):
    readers = set(runtime) - WRITES_STATE
    assert readers, "reader set is empty; WRITES_STATE over-covers"
    for name in readers:
        flags = runtime[name]
        assert flags["readOnlyHint"] is True, f"{name} only reads but is not read-only"
        assert flags["idempotentHint"] is True, f"{name} only reads but is not idempotent"


def test_only_in_place_overwrites_are_destructive(runtime):
    for name, flags in runtime.items():
        expected = name in DESTRUCTIVE
        assert flags["destructiveHint"] is expected, (
            f"{name}: destructiveHint={flags['destructiveHint']}, expected {expected}"
        )


def test_open_world_matches_mutable_external_state(runtime):
    for name, flags in runtime.items():
        expected = name in OPEN_WORLD
        assert flags["openWorldHint"] is expected, (
            f"{name}: openWorldHint={flags['openWorldHint']}, expected {expected}"
        )


def test_cancel_order_is_not_idempotent(runtime):
    """A repeat hits not_cancellable and returns an error: the result differs."""
    assert runtime["cancel_order"]["idempotentHint"] is False


def test_stop_strategy_is_idempotent(runtime):
    """The API returns an already-terminal run unchanged."""
    assert runtime["stop_strategy"]["idempotentHint"] is True


def test_append_only_writes_are_not_idempotent(runtime):
    for name in ("place_order", "create_account", "create_strategy",
                 "run_backtest", "start_strategy"):
        assert runtime[name]["idempotentHint"] is False, (
            f"{name} appends a new resource; a repeat is a second one"
        )


def test_annotations_are_copies_not_shared():
    """Mutating one tool's flags must not leak into another's."""
    a = annotations_for("place_order")
    a["readOnlyHint"] = True
    assert annotations_for("get_bars")["readOnlyHint"] is True
    assert annotations_for("place_order")["readOnlyHint"] is False


def test_unknown_tool_raises_instead_of_defaulting():
    with pytest.raises(KeyError):
        annotations_for("no_such_tool")
