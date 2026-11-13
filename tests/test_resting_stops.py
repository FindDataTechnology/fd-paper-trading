"""Resting stops: the trigger state must be true, and the order must wake up on its own.

这里住着两个缺陷，2026-11-14 相隔几分钟先后发现。

**1. 触发标记是假的（更严重，先修）.** `evaluate_open_stops` 评估完一张 resting
止损单后做的是：

```python
if order.triggered_at is None and result.status in ("FILLED", "OPEN"):
    order.triggered_at = now            # 错：OPEN 有两种含义
```

`SimFillEngine` 用同一个 `"OPEN"` 表示**两件相反的事**——「触发条件不成立，继续
resting」和「已触发但限价没越过，按限价 resting」。于是**每一张 resting 止损单在
挂单后立刻被记成"已触发"**；下一次评估拿到的 `triggered=True` 又让引擎跳过触发判定：
STOP 直接按 `stop_price` 成交，STOP_LIMIT 直接按限价规则成交。

实测（报价 bid=269.95，止损挂 268.95，价格一动没动）：

```
stop 挂单后:  status=OPEN  triggered_at=2026-11-13T16:34:15Z  stop_price=268.95
再挂一张同标的 limit 单（BUY 1 @ 1.00，不会成交）
止损单现在:  status=FILLED  avg_fill_price=268.95
持仓:        []                       ← 仓位没了
```

即：**一张从未被触及的止损单，会在主人下一次在同标的上随手挂单时按止损价把自己卖掉。**
排查前 `evaluate_open_stops` 在**全仓零测试**（grep 只命中本文件）。

**2. 纯手动账户上没有任何东西 tick.** `2026-11-06-stop-order-execution` 的 design D1
把触发点定在「新报价到达」，并枚举为「runner tick / 手动补单 / run 评估」——两条是
用户或策略的动作。于是止损单只在挂单那一刻被评估一次，此后只有主人再下一单才醒。
修法是 `execution/stop_sweeper.py`（每 `stop_sweep_seconds`，默认 60，0=关），走
**同一条** `evaluate_open_stops` 路径。

刻意**不**把触发挂到 `/ws` 的报价 tick 上：`_emit_ticks` 给价格加了抖动让流看起来
在动，用合成涨跌去成交止损单等于按一个没人报过的价格成交。
"""

import asyncio
import time
import uuid
from decimal import Decimal

import pytest

from fd_paper_trading.marketdata.provider import Quote
from fd_paper_trading.marketdata.simulated import SimulatedMarketDataProvider
from fd_paper_trading.persistence.models import (
    Strategy,
    StrategyRun,
    StrategyRunMode,
    StrategyRunStatus,
)

_SPREAD = Decimal("0.05")


class MoveableProvider(SimulatedMarketDataProvider):
    """模拟源，但测试可以改某个标的的基准价（= 新的一天收盘）。"""

    def __init__(self) -> None:
        self.overrides: dict[str, Decimal] = {}

    async def get_quote(self, symbol: str) -> Quote | None:
        base = self.overrides.get(symbol.upper())
        if base is None:
            return await super().get_quote(symbol)
        return Quote(
            instrument_id=f"{symbol.upper()}.SIM",
            bid=base - _SPREAD,
            ask=base + _SPREAD,
        )


@pytest.fixture
def movable(app):
    """把执行路径与行情流都指向一个测试能推价的 provider。

    sweeper 读的是 ``app.state.execution_service``，所以换掉 service 上的 provider
    就够——这也是它读真 provider（而不是 realtime 的抖动价）的证据。
    """
    provider = MoveableProvider()
    app.state.execution_service.provider = provider
    app.state.realtime.provider = provider
    return provider


async def _wait_until(pred, *, timeout: float = 3.0, step: float = 0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(step)
    return pred()


async def _resting_sell_stop(
    client, headers, place_order, movable, account_id, *, quantity="10"
):
    """买 ``quantity`` 股，再挂一张不会在挂单时触发的卖出止损。

    返回 ``(order_id, stop_price)``。止损价取当前 bid 以下 1 元——挂单那一刻的
    评估（`api/routes/orders.py` 挂单后调 `evaluate_open_stops`）不会触发它，
    这正是要复现的 "resting" 状态。
    """
    buy = await place_order(
        headers, account_id, symbol="AAPL", side="BUY", quantity=quantity
    )
    assert buy.status_code == 202, buy.text

    quote = await movable.get_quote("AAPL")
    stop_price = (quote.bid - Decimal("1.00")).quantize(Decimal("0.01"))
    placed = await client.post(
        "/api/v1/orders",
        json={
            "account_id": str(account_id),
            "symbol": "AAPL",
            "side": "SELL",
            "order_type": "STOP",
            "quantity": str(quantity),
            "stop_price": str(stop_price),
        },
        headers=headers,
    )
    assert placed.status_code == 202, placed.text
    body = placed.json()
    assert body["status"] == "OPEN", "止损单在挂单那一刻就触发了（价格没到）"
    assert body["triggered_at"] is None
    return body["id"], stop_price


# ---- 1. 触发状态：resting 不等于 triggered ----

async def test_placing_a_stop_does_not_mark_it_triggered(
    app, client, auth_headers, create_account, place_order, movable
):
    """挂单后紧接着的评估（`api/routes/orders.py`）不该把未触发的单记成已触发。"""
    headers = await auth_headers("trigger1@example.com")
    acct = (await create_account(headers, starting_balance=100000)).json()
    order_id, _ = await _resting_sell_stop(
        client, headers, place_order, movable, acct["id"]
    )
    order = (await client.get(f"/api/v1/orders/{order_id}", headers=headers)).json()
    assert order["status"] == "OPEN"
    assert order["triggered_at"] is None, (
        "价格没触及止损价，却被记成已触发——下一次评估会直接按止损价成交"
    )


async def test_an_untouched_stop_does_not_fill_on_a_later_order_for_that_symbol(
    app, client, auth_headers, create_account, place_order, movable
):
    """**这条是钱**：从未触及的止损单，不能因为同标的来了别的单就成交。

    实测过（修之前）：bid=269.95、止损 268.95、价格没动，用户在同标的挂一张
    BUY 1 @ 1.00 的限价单，止损单立刻 FILLED @ 268.95，仓位清零。
    """
    headers = await auth_headers("trigger2@example.com")
    acct = (await create_account(headers, starting_balance=100000)).json()
    order_id, stop_price = await _resting_sell_stop(
        client, headers, place_order, movable, acct["id"]
    )

    # 同标的另一张单——它的「新报价到达」会评估账户里的 resting 止损。
    other = await place_order(
        headers,
        acct["id"],
        symbol="AAPL",
        side="BUY",
        order_type="LIMIT",
        quantity="1",
        limit_price="1.00",
    )
    assert other.status_code == 202, other.text
    assert other.json()["status"] == "OPEN", "这张限价单本身不该成交（离市价很远）"

    order = (await client.get(f"/api/v1/orders/{order_id}", headers=headers)).json()
    assert order["status"] == "OPEN", (
        f"止损价 {stop_price} 从未被触及，单子却 {order['status']} @ "
        f"{order['avg_fill_price']}"
    )
    assert order["triggered_at"] is None

    position = (
        await client.get(f"/api/v1/accounts/{acct['id']}/positions", headers=headers)
    ).json()
    assert any(p["symbol"] == "AAPL" for p in position), "仓位被一张没触发的止损单卖掉了"


async def test_evaluating_a_resting_stop_twice_changes_nothing(
    app, client, auth_headers, create_account, place_order, movable
):
    """幂等：评估两次与一次同结果（这正是"OPEN 两种含义"造成的分叉）。"""
    headers = await auth_headers("trigger3@example.com")
    acct = (await create_account(headers, starting_balance=100000)).json()
    order_id, _ = await _resting_sell_stop(
        client, headers, place_order, movable, acct["id"]
    )

    service = app.state.execution_service
    for _ in range(3):
        async with app.state.db.session_factory() as session:
            from fd_paper_trading.persistence.models import Account

            account = await session.get(Account, uuid.UUID(acct["id"]), with_for_update=True)
            await service.evaluate_open_stops(session, account)
            await session.commit()

    order = (await client.get(f"/api/v1/orders/{order_id}", headers=headers)).json()
    assert order["status"] == "OPEN"
    assert order["triggered_at"] is None


async def test_a_crossed_stop_still_fills_at_its_stop_price(
    app, client, auth_headers, create_account, place_order, movable
):
    """反向对照：真被触及时仍按止损价成交（修法不能把正常路径弄坏）。"""
    headers = await auth_headers("trigger4@example.com")
    acct = (await create_account(headers, starting_balance=100000)).json()
    order_id, stop_price = await _resting_sell_stop(
        client, headers, place_order, movable, acct["id"]
    )

    movable.overrides["AAPL"] = stop_price - Decimal("2.00")
    async with app.state.db.session_factory() as session:
        from fd_paper_trading.persistence.models import Account

        account = await session.get(Account, uuid.UUID(acct["id"]), with_for_update=True)
        await app.state.execution_service.evaluate_open_stops(session, account)
        await session.commit()

    order = (await client.get(f"/api/v1/orders/{order_id}", headers=headers)).json()
    assert order["status"] == "FILLED"
    assert Decimal(order["avg_fill_price"]) == stop_price
    assert order["triggered_at"] is not None


async def test_a_stop_limit_rests_after_trigger_without_refilling(
    app, client, auth_headers, create_account, place_order, movable
):
    """STOP_LIMIT 触发后转限价 resting：不能因为"已触发"就每评估一次成交一次。"""
    headers = await auth_headers("trigger5@example.com")
    acct = (await create_account(headers, starting_balance=100000)).json()
    assert (
        await place_order(headers, acct["id"], symbol="AAPL", side="BUY", quantity="10")
    ).status_code == 202
    quote = await movable.get_quote("AAPL")
    stop_price = (quote.bid - Decimal("1.00")).quantize(Decimal("0.01"))
    limit_price = (stop_price - Decimal("5.00")).quantize(Decimal("0.01"))

    placed = await client.post(
        "/api/v1/orders",
        json={
            "account_id": acct["id"],
            "symbol": "AAPL",
            "side": "SELL",
            "order_type": "STOP_LIMIT",
            "quantity": "10",
            "stop_price": str(stop_price),
            "limit_price": str(limit_price),
        },
        headers=headers,
    )
    assert placed.status_code == 202, placed.text
    order_id = placed.json()["id"]

    # 触发但限价没越过：价格跌穿止损，但仍低于限价（卖出限价单要等价格回到 limit 之上）
    movable.overrides["AAPL"] = limit_price - Decimal("2.00")
    service = app.state.execution_service
    from fd_paper_trading.persistence.models import Account

    async with app.state.db.session_factory() as session:
        account = await session.get(Account, uuid.UUID(acct["id"]), with_for_update=True)
        await service.evaluate_open_stops(session, account)
        await session.commit()

    after_trigger = (
        await client.get(f"/api/v1/orders/{order_id}", headers=headers)
    ).json()
    assert after_trigger["status"] == "OPEN", "触发后应作为限价单 resting"
    assert after_trigger["triggered_at"] is not None, "触发时刻该被记录"

    # 再评估一次：价格没变（仍在 limit 之上），不该成交
    async with app.state.db.session_factory() as session:
        account = await session.get(Account, uuid.UUID(acct["id"]), with_for_update=True)
        await service.evaluate_open_stops(session, account)
        await session.commit()
    still = (await client.get(f"/api/v1/orders/{order_id}", headers=headers)).json()
    assert still["status"] == "OPEN"
    assert still["filled_quantity"] == "0" or Decimal(still["filled_quantity"]) == 0


# ---- 2. sweeper：纯手动账户也必须自己醒 ----

async def test_a_resting_stop_triggers_once_the_price_crosses(
    app, client, auth_headers, create_account, place_order, movable
):
    """不靠用户再下一单：价格跌破止损价后，sweeper 自己把它唤醒。"""
    headers = await auth_headers("stops@example.com")
    acct = (await create_account(headers, starting_balance=100000)).json()
    order_id, stop_price = await _resting_sell_stop(
        client, headers, place_order, movable, acct["id"]
    )

    # 新的一天：收盘价跌破止损价。用户什么都没做。
    movable.overrides["AAPL"] = stop_price - Decimal("2.00")

    await app.state.stop_sweeper.sweep_once()

    order = (await client.get(f"/api/v1/orders/{order_id}", headers=headers)).json()
    assert order["status"] == "FILLED", (
        f"价格已跌破止损价，单子仍是 {order['status']}——没有任何东西唤醒它"
    )
    assert order["triggered_at"] is not None
    assert Decimal(order["avg_fill_price"]) == stop_price, (
        "止损触发应按止损价成交（不是当时的 bid）"
    )


async def test_the_sweeper_task_wakes_stops_without_being_called(
    app, client, auth_headers, create_account, place_order, movable
):
    """不是只有测试调 sweep_once 才动：后台循环自己会跑。"""
    headers = await auth_headers("stops2@example.com")
    acct = (await create_account(headers, starting_balance=100000)).json()
    order_id, stop_price = await _resting_sell_stop(
        client, headers, place_order, movable, acct["id"]
    )

    app.state.stop_sweeper.interval_seconds = 0.02
    await app.state.stop_sweeper.start()
    try:
        movable.overrides["AAPL"] = stop_price - Decimal("2.00")
        assert app.state.stop_sweeper._task is not None, "任务没起来"
        assert await _wait_until(
            lambda: True
        )  # 让出一次事件循环，循环开始 sleep
        await asyncio.sleep(0.2)  # 给后台循环几个周期
        order = (await client.get(f"/api/v1/orders/{order_id}", headers=headers)).json()
        assert order["status"] == "FILLED", "后台循环没有唤醒止损单"
    finally:
        await app.state.stop_sweeper.stop()


async def test_the_app_lifespan_starts_and_stops_the_sweeper(app):
    """接线判据：删掉 lifespan 里的 start/stop，这条就红。"""
    async with app.router.lifespan_context(app):
        task = app.state.stop_sweeper._task
        assert task is not None, "lifespan 没启动 sweeper（止损单将永不醒来）"
        assert not task.done()
    assert app.state.stop_sweeper._task is None, "lifespan 退出后 sweeper 还在跑"


async def test_zero_interval_disables_the_sweeper(app):
    sweeper = app.state.stop_sweeper
    sweeper.interval_seconds = 0
    await sweeper.start()
    assert sweeper._task is None, "0 应表示关闭"
    await sweeper.stop()


async def test_a_sweep_only_touches_accounts_that_actually_hold_a_stop(
    app, client, auth_headers, create_account, place_order, movable
):
    """空扫描是常态（绝大多数账户没有止损单）：不该每 60 秒碰一遍全库订单表。"""
    headers = await auth_headers("stops4@example.com")
    acct = (await create_account(headers, starting_balance=100000)).json()
    assert (
        await place_order(headers, acct["id"], symbol="AAPL", side="BUY", quantity="10")
    ).status_code == 202

    assert str(acct["id"]) not in [
        str(a) for a in await app.state.stop_sweeper.accounts_with_resting_stops()
    ]

    await _resting_sell_stop(client, headers, place_order, movable, acct["id"])
    assert str(acct["id"]) in [
        str(a) for a in await app.state.stop_sweeper.accounts_with_resting_stops()
    ]


async def test_accounts_driven_by_an_active_run_are_left_to_the_runner(
    app, client, auth_headers, create_account, place_order, movable
):
    """活跃 run 的账户由 runner 评估并写 run 日志；sweeper 抢过去会让日志缺一行。"""
    headers = await auth_headers("stops5@example.com")
    acct = (await create_account(headers, starting_balance=100000)).json()
    await _resting_sell_stop(client, headers, place_order, movable, acct["id"])

    strategy = await client.post(
        "/api/v1/strategies",
        json={
            "name": "run-owned",
            "spec": {
                "kind": "weight",
                "universe": ["AAPL", "MSFT"],
                "rebalance": {"every_n_bars": 3},
                "weights_series": {"2026-01-01": {"AAPL": 0.5, "MSFT": 0.5}},
            },
        },
        headers=headers,
    )
    assert strategy.status_code == 201, strategy.text
    async with app.state.db.session_factory() as session:
        row = await session.get(Strategy, uuid.UUID(strategy.json()["id"]))
        session.add(
            StrategyRun(
                strategy_id=row.id,
                strategy_version=row.version,
                mode=StrategyRunMode.PAPER,
                account_id=uuid.UUID(acct["id"]),
                status=StrategyRunStatus.RUNNING,
                params={},
            )
        )
        await session.commit()

    assert str(acct["id"]) not in [
        str(a) for a in await app.state.stop_sweeper.accounts_with_resting_stops()
    ], "活跃 run 的账户被 sweeper 抢走了（run 日志会缺触发原因）"
