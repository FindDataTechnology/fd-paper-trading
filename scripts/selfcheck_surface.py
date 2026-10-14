"""Surface selfcheck: assert the tool-surface manifest agrees with the
registered MCP tool set, both directions.

Direction 1 (registry drift): every @mcp.tool in fd_paper_trading.mcp.server
must be declared in tool-surface.json, and every declared tool must be
registered. Direction 2 (closure): any tool a consumer may expose must fall
within the manifest's commercial set — for this server every group is
commercial, so the closure assertion pins the full inventory.

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


def registered_tools() -> set[str]:
    """Tool names registered on the FastMCP instance, derived from source.

    The server module requires no credentials at import time (fallback token
    is optional), so importing it directly is safe and keeps this check honest
    — it reflects the actual registry, not a grep of names.
    """
    import asyncio

    import fd_paper_trading.mcp.server as server_mod  # noqa: E402

    tools = asyncio.run(server_mod.mcp._list_tools())
    return {t.name for t in tools}


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

    if problems:
        for p in problems:
            print(f"FAIL: {p}")
        return 1
    print(f"OK: manifest == registry ({len(live)} tools), commercial closure holds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
