"""Surface selfcheck: assert the tool-surface manifest agrees with the
registered MCP tool set, both directions.

Direction 1 (registry drift): every @mcp.tool in fd_paper_trading.mcp.server
must be declared in tool-surface.json, and every declared tool must be
registered. Direction 2 (closure): any tool a consumer may expose must fall
within the manifest's commercial set — for this server every group is
commercial, so the closure assertion pins the full inventory. Direction 3
(annotations): the manifest's four behavior flags per tool must equal the
flags the runtime server actually registered, in both directions.

Run: uv run python scripts/selfcheck_surface.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "tool-surface.json"
SERVER_SRC = ROOT / "src" / "fd_paper_trading" / "mcp" / "server.py"

#: The behavior hints every tool must declare, in manifest order.
REQUIRED_HINTS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")


def runtime_tools() -> list:
    """Tools registered on the FastMCP instance, asked of the live registry.

    The server module requires no credentials at import time (fallback token
    is optional), so importing it directly is safe and keeps this check honest
    — it reflects the actual registry, not a grep of names.
    """
    import asyncio

    import fd_paper_trading.mcp.server as server_mod  # noqa: E402

    return asyncio.run(server_mod.mcp._list_tools())


def registered_tools() -> set[str]:
    return {t.name for t in runtime_tools()}


def runtime_annotations() -> dict[str, dict]:
    """Each tool's declared flags as the runtime registry holds them.

    A tool with no annotations maps to ``{}`` so the caller can report it as a
    missing declaration rather than silently skipping it.
    """
    out: dict[str, dict] = {}
    for t in runtime_tools():
        a = t.annotations
        out[t.name] = (
            {} if a is None else {k: getattr(a, k) for k in REQUIRED_HINTS}
        )
    return out


def check_annotations(manifest: dict, declared: set[str]) -> list[str]:
    """Manifest flags must equal runtime flags, in both directions.

    Missing flags and a manifest that disagrees with the implementation both
    fail: the second is the dangerous one — a client auto-approves a tool the
    manifest calls read-only while the server writes state.
    """
    problems: list[str] = []
    table = manifest.get("tool_annotations")
    if not isinstance(table, dict):
        return ["tool_annotations missing from manifest (flags must ship with tools)"]

    for name in sorted(declared - set(table)):
        problems.append(f"{name}: manifest declares no behavior annotations")
    for name in sorted(set(table) - declared):
        problems.append(f"{name}: tool_annotations names a tool that is not registered")

    live = runtime_annotations()
    for name in sorted(declared & set(table)):
        entry = table[name]
        missing = [k for k in REQUIRED_HINTS if k not in entry]
        if missing:
            problems.append(f"{name}: manifest flags incomplete, missing {missing}")
            continue
        actual = live.get(name, {})
        for key in REQUIRED_HINTS:
            if actual.get(key) != entry[key]:
                problems.append(
                    f"{name}.{key}: manifest declares {entry[key]!r} but runtime "
                    f"registered {actual.get(key)!r}"
                )
    return problems


def main() -> int:
    manifest = json.loads(MANIFEST.read_text())
    declared: set[str] = set()
    commercial: set[str] = set()
    for group in manifest["tool_groups"]:
        declared.update(group["tools"])
        if group["level"] == "commercial":
            commercial.update(group["tools"])

    live = registered_tools()
    problems: list[str] = []

    missing_in_manifest = sorted(live - declared)
    if missing_in_manifest:
        problems.append(
            "registered but not in manifest: " + ", ".join(missing_in_manifest)
        )
    phantom_in_manifest = sorted(declared - live)
    if phantom_in_manifest:
        problems.append(
            "in manifest but not registered: " + ", ".join(phantom_in_manifest)
        )

    expected = manifest["registry"]["expected_tool_count"]
    if expected != len(live):
        problems.append(f"expected_tool_count={expected} but {len(live)} registered")

    # Closure: this server exposes everything commercially; the commercial set
    # must cover the entire declared inventory.
    if commercial != declared:
        problems.append(
            "commercial set does not cover declared tools: "
            + ", ".join(sorted(declared - commercial))
        )

    problems += check_annotations(manifest, declared)

    if problems:
        for p in problems:
            print(f"FAIL: {p}")
        return 1
    print(f"OK: manifest == registry ({len(live)} tools), commercial closure holds")
    print(f"OK: annotations match runtime for {len(live)} tools ({len(REQUIRED_HINTS)} flags each)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
