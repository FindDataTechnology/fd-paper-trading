"""规格「One price world for backtest, live, and manual fills」的闸。

规格说：启动选出来的**那一个** provider 实例，要同时喂回测（历史日线）、实时策略
（最新一根）与成交（quote）；同一个进程内不许混用不同 provider 的价格序列。

2026-11-13 实测这条**成立**，但成立方式是结构性的、没有任何判据钉住：
`build_market_data_provider()` 造出一个实例放进 `app.state.market_data_provider`，
之后所有取价方（`api/routes/strategies.py`、`strategy/runner.py`、
`api/routes/accounts.py`、成交服务）都读这一个属性。于是这条规格的全部保证，
压在两件事上：

1. **marketdata 之外没有任何地方再造一个 provider**。多造一个就是第二个价格世界：
   回测在模拟盘的随机游走上跑出漂亮曲线，成交却按真收盘价，两边都"看起来对"。
2. **provider 内部不许把价格方法代理给另一个 provider**。
   `FdOpenDataMarketDataProvider` 确实持有一个 `SimulatedMarketDataProvider`
   （`self._sim`），但它只用于 `get_instrument` / `search_instruments`——即
   合约元数据与搜索，不是价格。这条界线今天靠自觉维持，没有闸。

两条都做成静态判据（AST），不靠人记。

刻意**不**断言"哪些文件读了 app.state"：那会把文件清单钉成契约，
`api/routes/` 拆一次文件就假红。判据只问"有没有第二个来源"和"有没有价格代理"。
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
MARKETDATA = SRC / "fd_paper_trading" / "marketdata"

#: 能独立供水价的四个具体实现。抽象基类 `MarketDataProvider` 不在列：
#: 它是 ABC，实例化即 TypeError，而且作为类型注解出现是正常的。
CONCRETE_PROVIDERS = (
    "SimulatedMarketDataProvider",
    "PolygonMarketDataProvider",
    "AstockMarketDataProvider",
    "FdOpenDataMarketDataProvider",
)

#: provider 的**价格**方法。代理给另一个 provider 就等于混价。
PRICE_METHODS = (
    "get_bars",
    "get_daily_bars",
    "get_quote",
    "get_latest_close",
    "get_bars_with_coverage",
)


def _calls_to(source: str, names: tuple[str, ...]) -> list[tuple[int, str]]:
    """行号 + 名字：`source` 里对 `names` 中任意一个的构造/调用。"""
    out: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        called = (
            func.id if isinstance(func, ast.Name)
            else func.attr if isinstance(func, ast.Attribute)
            else ""
        )
        if called in names:
            out.append((node.lineno, called))
    return out


def _delegated_price_calls(source: str) -> list[tuple[int, str]]:
    """行号 + 方法名：`self._sim.<价格方法>` 这类价格代理。"""
    out: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr in ("_sim", "_delegate", "_fallback")
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "self"
            and node.func.attr in PRICE_METHODS
        ):
            out.append((node.lineno, node.func.attr))
    return out


def _consumers():
    """`marketdata/` 之外、但属于本仓源码的每个模块。"""
    for path in SRC.rglob("*.py"):
        if MARKETDATA in path.parents:
            continue
        yield path, path.read_text(encoding="utf-8")


def _providers():
    for path in MARKETDATA.rglob("*.py"):
        yield path, path.read_text(encoding="utf-8")


def test_no_second_provider_is_constructed_outside_the_marketdata_package() -> None:
    hits = [
        f"{path.relative_to(REPO)}:{lineno} {name}(...)"
        for path, text in _consumers()
        for lineno, name in _calls_to(text, CONCRETE_PROVIDERS)
    ]
    assert not hits, (
        "marketdata 包之外又造了一个 market-data provider："
        f"{hits}。这就是第二个价格世界——回测与成交会各读一套价，"
        "两边都看不出错。从 `app.state.market_data_provider` 取那一个实例。"
    )


def test_providers_never_delegate_price_methods_to_another_provider() -> None:
    hits = [
        f"{path.relative_to(REPO)}:{lineno} self._sim.{name}(...)"
        for path, text in _providers()
        for lineno, name in _delegated_price_calls(text)
    ]
    assert not hits, (
        "provider 把价格方法代理给了内部另一个 provider："
        f"{hits}。合约元数据/搜索可以代理，价格不行——"
        "那会让同一次回测里的 bar 与 fill 来自两套序列。"
    )


def test_the_guards_can_go_red() -> None:
    """两条判据都要能红——否则它们只是注释的另一种写法。"""
    assert _calls_to(
        "def f():\n    return AstockMarketDataProvider(dsn='x', source='y')\n",
        CONCRETE_PROVIDERS,
    ) == [(2, "AstockMarketDataProvider")]

    assert _delegated_price_calls(
        "async def f(self, s):\n    return await self._sim.get_quote(s)\n"
    ) == [(2, "get_quote")]

    # 反面对照：元数据/搜索的代理是**允许**的，不能一并报红。
    assert _delegated_price_calls(
        "def f(self, s):\n"
        "    return self._sim.get_instrument(s)\n"
        "async def g(self, q):\n"
        "    return await self._sim.search_instruments(q)\n"
    ) == []
