"""规格「No third-party trading framework at runtime」的闸。

规格说：market-data / 成交计算 / 回测器 / 策略沙箱 SHALL NOT 依赖第三方交易框架，
干净安装也不得把这类框架拉进依赖图。2026-11-13 实测这条**成立但没有任何测试**——
按本仓一贯的判据（散文要求不进 CI ＝ 不存在），补上。

两条判据分别对应规格的两个场景：

1. **运行面**：把定价路径 import 起来之后，`sys.modules` 里不得出现任何被禁框架。
   写在**import 之后**而不是扫源码，是因为"没被 import"才是有意义的那个事实；
   源码里出现字符串（注释、错误信息）不算违反。
2. **依赖面**：`pyproject.toml` 声明的依赖名里不得出现被禁框架——它对应
   「clean install omits the framework」。

被禁清单只收**交易框架/交易 SDK**。刻意不收 `pandas`/`numpy`/`ta` 这类通用库：
`ta` 是技术指标库、随处可见，把它列进来会让这道闸长期噪声化——本仓 2026-08
撤过一道"死模块闸"就是因为噪声大（见 MEMORY `commit-tree-not-worktree`）。
自有内核 `fd_quant` 在 `_vendor/` 下，属自建，不在被禁之列。
"""
from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

#: 第三方**交易**框架 / 交易 SDK。只收"跑策略下单调仓"这一类。
FORBIDDEN = (
    "ccxt",
    "backtrader",
    "ib_insync",
    "ibapi",
    "alpaca",
    "alpaca_trade_api",
    "zipline",
    "vectorbt",
    "freqtrade",
    "qlib",
    "nautilus_trader",
    "hftbacktest",
    "lean",
    "quantconnect",
)

#: 规格点名的四条路径：市场数据、成交计算、回测器、策略沙箱。
#: 沙箱的实际模块名是 `sandbox_worker`（规格说的是概念组件，不是 import 路径——
#: 第一版我按字面猜 `strategy.sandbox`，直接 ModuleNotFoundError）。
PRICING_PATH = (
    "fd_paper_trading.marketdata",
    "fd_paper_trading.execution",
    "fd_paper_trading.strategy.backtester",
    "fd_paper_trading.strategy.sandbox_worker",
)


def _imported_forbidden() -> list[str]:
    return sorted({
        name for name in sys.modules
        if any(name == f or name.startswith(f + ".") for f in FORBIDDEN)
    })


def test_pricing_path_imports_no_trading_framework() -> None:
    for mod in PRICING_PATH:
        importlib.import_module(mod)
    hit = _imported_forbidden()
    assert not hit, (
        "定价路径 import 之后出现了第三方交易框架："
        f"{hit}。规格 market-data「No third-party trading framework at runtime」禁止这一条；"
        "把依赖换成自建实现或从该路径里移出去。"
    )


def test_declared_dependencies_omit_trading_frameworks() -> None:
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    project = data.get("project") or {}
    declared: list[str] = list(project.get("dependencies") or [])
    for group in (project.get("optional-dependencies") or {}).values():
        declared.extend(group or [])
    names = {spec.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip().lower()
             for spec in declared}
    hit = sorted(n for n in names if n in FORBIDDEN)
    assert not hit, (
        f"pyproject 声明了第三方交易框架 {hit}——规格要求 clean install 不得把它拉进依赖图。"
    )


def test_the_guard_can_go_red(monkeypatch) -> None:
    """守卫要能红：伪造一个已加载的框架模块，运行面判据必须点名它。"""
    import types

    monkeypatch.setitem(sys.modules, "backtrader", types.ModuleType("backtrader"))
    hit = _imported_forbidden()
    assert "backtrader" in hit, "被禁框架进 sys.modules 后判据没反应，等于没闸"
