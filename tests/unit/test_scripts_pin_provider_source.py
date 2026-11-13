"""脚本里构造价格 provider 必须显式钉源。

`add-price-supply-and-caliber` 之后 `AstockMarketDataProvider.source` 成了必填
（联邦视图的 `source_used` 要钉住），于是任何 `AstockMarketDataProvider(dsn=dsn)`
都会在 `__init__` 第一行抛：

    ValueError: astock provider requires an explicit pinned source
                (resolved from the price-source registry); it holds no default source name

2026-11-13 实测：`scripts/` 下有**四个**验收/冒烟脚本停在 `dsn=dsn` 上——
`smoke_astock_provider` / `runner_smoke_astock` / `overlay_acceptance_astock` /
`rotation_fixes_astock`。它们全部启动即崩，也就是说自那次 change 之后，这四个"检查有没有坏"
的脚本从来没有真正跑过。`608e137` 当时只修了两个证据脚本，其余四个没人再动。

这条守卫只做一件事：扫 `scripts/` 下每一次 `AstockMarketDataProvider(...)` 构造，
**必须带 `source=` 关键字**。为什么钉的是这一条而不是"必须用 open_provider()"：
`open_provider()` 是从注册表解析的推荐路径，但如果哪天某个脚本确实要显式钉另一个源，
那是合法写法——它该被允许，只要它**说清楚钉的是谁**。
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
CTOR = "AstockMarketDataProvider"


def _ctor_calls(tree: ast.AST):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None)
        if name == CTOR:
            yield node


def _offenders() -> list[str]:
    out: list[str] = []
    for path in sorted(SCRIPTS.glob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for call in _ctor_calls(tree):
            kwargs = {kw.arg for kw in call.keywords}
            if "source" in kwargs:
                continue
            out.append(f"{path.name}:{call.lineno}")
    return out


def test_every_script_pins_the_provider_source() -> None:
    bad = _offenders()
    assert not bad, (
        "这些脚本构造 provider 时没钉源，启动即 ValueError：\n  "
        + "\n  ".join(bad)
        + "\n修法：走 `_evidence_source.open_provider()`（注册表解析、拒 simulated），"
          "或显式传 `source=<registry db_source>`。"
    )


def test_the_guard_can_go_red(tmp_path, monkeypatch) -> None:
    """守卫要能红：造一个停在 dsn=dsn 上的脚本，扫描必须点名它。

    没有这一条，上面那条只是"现在恰好没人写错"——而不是"写错会被拦住"。
    """
    fake = tmp_path / "scripts"
    fake.mkdir()
    (fake / "bad_script.py").write_text(
        "from somewhere import AstockMarketDataProvider\n"
        "p = AstockMarketDataProvider(dsn='x')\n")
    (fake / "good_script.py").write_text(
        "from somewhere import AstockMarketDataProvider\n"
        "p = AstockMarketDataProvider(dsn='x', source='astock_daily')\n")
    monkeypatch.setattr(__import__(__name__), "SCRIPTS", fake)
    bad = _offenders()
    assert bad == ["bad_script.py:2"], bad
