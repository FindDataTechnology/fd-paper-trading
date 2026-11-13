"""四个明写在代码里、却从没被任何测试断言过的错误码。

2026-11-13 的量法：把 `src/` 里所有 `APIError("<code>", ...)` 的字符串码抽出来，
逐个到 `tests/` 全文里找。20 个码里有 5 个从未出现：

```
grid_too_large     strategies.py:733   参数网格 > 50 种组合
invalid_filter     strategies.py:822   /strategy-runs?mode=/&status= 非法值
name_exists        groups.py:48        建同名用户组
universe_too_large strategies.py:896   权重轨 universe 超组配额
user_inactive      auth.py:70          ← 已在 tests/test_auth.py 补上（附四十三）
```

前四个的共同点：**门是真的、状态码是对的、只是没人用那个码断言过**。
这类缺口不会让功能坏掉，但它让"码"变成一种只要改了实现就悄悄漂移的东西：
客户端按 code 分支，测试只按 200/400 分支，于是改码不红。四条的补法都一样——
断言 `error.code` 而不只是 `status_code`。

`universe_too_large` 已有行为测试（`tests/unit/test_weight_api.py`，断言状态码与
消息里点名了两个数字），这里补的是**契约面**：客户端认的是 code。

刻意放在一个文件里：它们是同一类缺口（"码没被断言"），聚在一起下次再扫时能一眼看出
还剩几个。
"""

from decimal import Decimal

import pytest

from fd_paper_trading.persistence.models import DEFAULT_GROUP_CONFIG, UserGroup
from fd_paper_trading.persistence.repositories import UserRepository


async def _make_admin(app, email: str) -> None:
    async with app.state.db.session_factory() as session:
        user = await UserRepository(session).get_by_email(email)
        user.is_admin = True
        await session.commit()


async def _make_strategy(client, headers, *, spec=None, name="s1"):
    payload = {
        "name": name,
        "spec": spec or {"kind": "template", "template": "sma_cross", "symbol": "AAPL",
                         "params": {"fast": 5, "slow": 20, "quantity": 10}},
    }
    r = await client.post("/api/v1/strategies", json=payload, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---- grid_too_large -------------------------------------------------------


async def test_param_grid_over_the_limit_is_grid_too_large(client, auth_headers):
    """参数网格在**请求时**就拒，不让它跑起来再在后台失败。

    规格（strategy-walkforward / design D6）：超限要在请求时给出即时拒绝，
    而不是让用户等一个 FAILED。上限 50 组：这里用 2×2×2×7 = 56 组踩线。
    """
    h = await auth_headers("grid@example.com")
    sid = await _make_strategy(client, h)

    r = await client.post(
        f"/api/v1/strategies/{sid}/backtests",
        json={
            "start": "2026-01-01",
            "end": "2026-06-30",
            "param_grid": {
                "fast": [3, 5],
                "slow": [10, 20],
                "extra": [1, 2],
                "pad": [1, 2, 3, 4, 5, 6, 7],
            },
        },
        headers=h,
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "grid_too_large"
    # 拒绝要能自解释：上限与实际的组合数都在消息里
    message = r.json()["error"]["message"]
    assert "50" in message and "56" in message, message


# ---- invalid_filter ------------------------------------------------------


@pytest.mark.parametrize("query", ["mode=bogus", "status=bogus"])
async def test_bad_run_filter_is_invalid_filter(client, auth_headers, query):
    """列表端点的过滤参数是枚举：非法值要明确拒绝，而不是静默返回空列表。

    "静默返回空"最坏：客户端把拼错的 mode 读成"没有数据"，而不是"我写错了"。
    """
    h = await auth_headers("filter@example.com")
    r = await client.get(f"/api/v1/strategy-runs?{query}", headers=h)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "invalid_filter"


# ---- name_exists --------------------------------------------------------


async def test_duplicate_group_name_is_name_exists(client, auth_headers, app):
    """同名用户组是 409 `name_exists`（规格：组名唯一）。"""
    h = await auth_headers("admin@example.com")
    await _make_admin(app, "admin@example.com")

    payload = {"name": "pro", "config": DEFAULT_GROUP_CONFIG}
    first = await client.post("/api/v1/groups", json=payload, headers=h)
    assert first.status_code == 201, first.text

    again = await client.post("/api/v1/groups", json=payload, headers=h)
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "name_exists"


# ---- universe_too_large -------------------------------------------------


async def test_oversized_universe_is_universe_too_large(
    client, auth_headers, create_account, app
):
    """权重轨的 universe 超组配额时，码是 `universe_too_large`。

    `tests/unit/test_weight_api.py` 已断言状态码与"两个数字都被点名"，
    这里补客户端真正分支的那个东西：`error.code`。
    """
    h = await auth_headers("universe2@example.com")
    async with app.state.db.session_factory() as session:
        group = UserGroup(
            name="tiny", config={**DEFAULT_GROUP_CONFIG, "max_universe_size": 1}
        )
        session.add(group)
        user = await UserRepository(session).get_by_email("universe2@example.com")
        user.group_id = group.id
        await session.commit()

    account = await create_account(h, starting_balance=Decimal("100000"))
    spec = {
        "kind": "weight",
        "universe": ["AAPL", "MSFT"],
        "rebalance": {"every_n_bars": 2},
        "weights_series": {"2000-01-01": {"AAPL": 0.5, "MSFT": 0.5}},
    }
    sid = await _make_strategy(client, h, spec=spec, name="w1")
    r = await client.post(
        f"/api/v1/strategies/{sid}/runs",
        json={"account_id": account.json()["id"]},
        headers=h,
    )
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "universe_too_large"
