"""前端类型契约 vs 后端 schema：同一份契约的两份手抄，必须双向相等。

`web/src/api/types.ts` 是 SPA 的唯一契约来源，`api/schemas.py` + `auth/schemas.py` 是服务端的。
两份都是手写的，中间没有任何判据——于是 2026-11-13 实测出四处漂移：

```
OrderOut       前端缺 strategy_run_id / stop_price / triggered_at
UserOut        前端缺 is_admin
StrategyRunOut 前端缺 universe / weight_snapshots / data_gaps
```

这些都不是"崩"的缺陷，而是**类型在说谎**：`order.strategy_run_id` 在 TS 里不存在，
于是任何想按 run 归因、或想显示止损价的 UI 只能 `as any` 或静默拿到 `undefined`；
TS 编译器本来该在这里帮忙，却因为类型不全而闭口不言。

反向（前端多声明了服务端没发的字段）同样有害：那个字段永远是 `undefined`，
调用方却以为它一定在。

判据：对**显式声明的配对**双向比较字段集合。配对是显式表而不是按名字猜——
`BarOut` ↔ `Bar`、`InstrumentSearchResultOut` ↔ `InstrumentSearchResult` 这类
名字本来就不一致，猜错会制造假红。

刻意不做的两件事：
* 不比类型（`Decimal` 序列化成 `string`、`datetime` 序列化成 ISO 串）——那要跑一遍
  FastAPI 序列化才准，成本远大于收益；
* 不要求"每个 *Out 都有 TS interface"：`ApiTokenOut` / `UserGroupOut` /
  `StrategyRunDetailOut` 等确实是 API-only（MCP 工具与管理端点消费），
  逼前端为它们写空壳类型只是噪声。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BACKEND_SCHEMAS = (
    REPO / "src" / "fd_paper_trading" / "api" / "schemas.py",
    REPO / "src" / "fd_paper_trading" / "auth" / "schemas.py",
)
TS_TYPES = REPO / "web" / "src" / "api" / "types.ts"

#: (服务端 model, 前端 interface) —— 显式配对，不按名字猜。
PAIRS = (
    ("UserOut", "UserOut"),
    ("OrderOut", "OrderOut"),
    ("FillOut", "FillOut"),
    ("PositionOut", "PositionOut"),
    ("BarOut", "Bar"),
    ("InstrumentSearchResultOut", "InstrumentSearchResult"),
    ("StrategyOut", "StrategyOut"),
    ("StrategyRunOut", "StrategyRunOut"),
)


def _backend_fields() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for path in BACKEND_SCHEMAS:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ClassDef) and node.name.endswith("Out"):
                out[node.name] = {
                    st.target.id
                    for st in node.body
                    if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name)
                }
    return out


def _ts_fields() -> dict[str, set[str]]:
    text = TS_TYPES.read_text(encoding="utf-8")
    return {
        name: set(re.findall(r"^\s{2}(\w+)\??:", body, re.M))
        for name, body in re.findall(r"export interface (\w+)\s*\{(.*?)\n\}", text, re.S)
    }


@pytest.mark.parametrize("backend,frontend", PAIRS, ids=[b for b, _ in PAIRS])
def test_frontend_contract_matches_backend_schema(backend: str, frontend: str):
    be = _backend_fields()
    ts = _ts_fields()
    assert backend in be, f"后端找不到 {backend}（配对表过期了？）"
    assert frontend in ts, f"web/src/api/types.ts 缺少 interface {frontend}"

    missing = sorted(be[backend] - ts[frontend])     # 服务端发、前端不知道
    phantom = sorted(ts[frontend] - be[backend])     # 前端以为有、服务端不发

    assert not missing, (
        f"{frontend} 少了后端字段 {missing}。"
        "类型不全 ⇒ 调用方以为字段不存在，只能 as any 或静默拿 undefined。"
    )
    assert not phantom, (
        f"{frontend} 声明了后端不发的字段 {phantom}。"
        "那字段永远是 undefined，而调用方以为它一定在。"
    )


def test_the_pair_table_points_at_real_declarations():
    """配对表自身不能腐烂：两侧名字都必须真实存在。"""
    be, ts = _backend_fields(), _ts_fields()
    stale = [
        f"{b} ↔ {f}" for b, f in PAIRS if b not in be or f not in ts
    ]
    assert not stale, f"配对表指向不存在的声明：{stale}"


def test_the_guard_can_go_red():
    """纯函数自证：字段集合相等才通过，两个方向都能红。"""
    def diff(be: set[str], ts: set[str]) -> tuple[list[str], list[str]]:
        return sorted(be - ts), sorted(ts - be)

    assert diff({"a", "b"}, {"a", "b"}) == ([], [])
    assert diff({"a", "b"}, {"a"}) == (["b"], [])      # 前端缺
    assert diff({"a"}, {"a", "c"}) == ([], ["c"])      # 前端多
