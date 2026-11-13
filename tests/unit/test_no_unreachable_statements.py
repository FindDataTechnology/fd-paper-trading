"""不可达语句的闸：`return`/`raise` 之后再跟一条语句。

为什么这条值得单独成闸（2026-11-13 实测）：`marketdata/selection.py` 的
`resolve_selection()` 末尾留着 `return Selection(provider=PROVIDER_SIMULATED)`
——那是 task 6.4 删掉旧变量映射时没删干净的残件。它本身无害，但它**说的是反话**：
这个模块的正文写着「绝不静默退回 simulated」「没配就 simulated 是唯一一处」，
而这条永远到不了的 `return` 读起来像「其他情况退回 simulated」，正好是正文禁止的
那件事。死代码最贵的不是占一行，是它替一个不存在的分支作证。

判据只收一种形状：**同一个语句块里，终结语句（return/raise/continue/break）之后
还有别的语句**（`pass` 除外）。刻意不追求"全程序不可达"——那需要控制流分析，
误报会把闸变成噪声源（本仓 2026-08 撤过一道噪声大的死模块闸）。这一种形状零误报：
全仓 `src/` + `scripts/` 扫下来只有那一处。

刻意不扫 `tests/`：验证"闸能红"的用例本来就要构造坏形状，扫进来是自我指涉。
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("src", "scripts")

_TERMINAL = (ast.Return, ast.Raise, ast.Continue, ast.Break)
_BLOCK_FIELDS = ("body", "orelse", "finalbody")


def _unreachable_lines(source: str, filename: str = "<src>") -> list[int]:
    """行号列表：终结语句之后仍跟有语句（`pass` 不算）的那些后继语句。"""
    tree = ast.parse(source, filename=filename)
    out: list[int] = []
    for node in ast.walk(tree):
        for field in _BLOCK_FIELDS:
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for current, nxt in zip(block, block[1:]):
                if isinstance(current, _TERMINAL) and not isinstance(nxt, ast.Pass):
                    out.append(nxt.lineno)
    return out


def _iter_sources():
    for root in SCAN_ROOTS:
        for path in (REPO / root).rglob("*.py"):
            yield path, path.read_text(encoding="utf-8")


def test_no_unreachable_statements() -> None:
    hits: list[str] = []
    for path, text in _iter_sources():
        for lineno in _unreachable_lines(text, str(path)):
            rel = path.relative_to(REPO)
            hits.append(f"{rel}:{lineno}")
    assert not hits, (
        "这些语句跟在 return/raise/continue/break 之后，永远不会执行："
        f"{hits}。删掉，或者把它们要表达的分支写进真正的控制流里——"
        "留着会让读代码的人以为存在一条并不存在的路径。"
    )


def test_the_scan_can_go_red() -> None:
    """守卫要能红：喂一段照抄来的坏形状，必须点名它的行。"""
    bad = (
        "def f(flag):\n"
        "    if flag:\n"
        "        return 1\n"
        "    return 2\n"
        "    return 3\n"          # 不可达
        "\n"
        "def g():\n"
        "    raise RuntimeError('x')\n"
        "    pass\n"              # pass 不算（占位是合法意图）
    )
    assert _unreachable_lines(bad) == [5]
