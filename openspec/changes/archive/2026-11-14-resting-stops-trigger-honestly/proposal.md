## Why

`execution/service.py::evaluate_open_stops` 在评估完一张 resting 止损单后这样记账：

```python
if order.triggered_at is None and result.status in ("FILLED", "OPEN"):
    order.triggered_at = now            # 错：OPEN 有两种含义
```

`SimFillEngine` 用同一个 `"OPEN"` 表示**两件相反的事**：「触发条件不成立，继续 resting」
和「已触发、但限价没越过，按限价 resting」。于是**每一张 resting 止损单在挂单后立刻被
记成"已触发"**；而下一次评估把 `triggered_at` 回灌成 `triggered=True`，引擎据此
**跳过触发判定**——STOP 直接按 `stop_price` 成交，STOP_LIMIT 直接按限价规则成交。

2026-11-14 实测（模拟源，报价 bid=269.95，止损挂 268.95，价格一动没动）：

```
stop 挂单后:  status=OPEN  triggered_at=2026-11-13T16:34:15Z  stop_price=268.95
再挂一张同标的 limit 单（BUY 1 @ 1.00，自己不会成交）
止损单现在:  status=FILLED  avg_fill_price=268.95
持仓:        []                       ← 仓位没了
```

即：**一张从未被触及的止损单，会在主人下一次在同标的上随手挂单时，按止损价把自己
卖掉。** 触发标记一旦是假的，"TRIGGERED"这个状态、run 日志、以及任何基于
`triggered_at` 的判断全部跟着失真。

而 `evaluate_open_stops` 在这次排查前**全仓零测试**（`grep` 只命中这次新加的文件），
所以 `tests/unit/test_fill_engine.py` 里十条 STOP 用例全绿也说明不了它——那些测的是
纯计算器，不是记账那一步。

### 顺带：纯手动账户上没有任何东西 tick

同一段代码是**唯一**的 live 触发路径，而它的调用点只有两处：挂单后（`api/routes/
orders.py`）与策略 run 的轮询（`strategy/runner.py`）。`2026-11-06-stop-order-execution`
的 design D1 把触发点定在「新报价到达」，并枚举为「runner tick / 手动补单 / run 评估」
——两条是用户或策略的动作。于是纯手动账户的止损单只在**挂单那一刻**被评估一次，此后
只有主人再下一单才会醒；收盘价跌破止损价，单子照样 resting。

## What Changes

- **修触发记账**：新增 `SimFillEngine.trigger_condition_met(quote, side, stop_price)`
  作为触发规则的**单一真源**，`_compute_stop_fill` 与「记录触发时刻」都调它；服务端
  只在**条件真的成立**时盖 `triggered_at`，并且**先盖再看成交**（同一次评估里刚触发的
  单要按已触发的语义判）。
- **补 sweeper**：`execution/stop_sweeper.py` 每 `stop_sweep_seconds`（默认 60，0=关）
  扫一遍「有 resting 止损、且没有活跃 run 驱动」的账户，走**同一条**
  `evaluate_open_stops` 路径，并把触发后的订单按既有 `order` 事件推给属主。
  活跃 run 的账户跳过：runner 自己评估并写 run 的 ORDER 事件，sweeper 抢过去会让
  run 日志缺掉「为什么成交」。
- 刻意**不**把触发挂到 `/ws` 的报价 tick 上：`_emit_ticks` 给价格加了抖动让流看起来在动，
  用合成涨跌去成交止损单等于按一个没人报过的价格成交。

## Capabilities

### Modified Capabilities

- `order-execution`：止损的触发规则与记账口径（`triggered_at` 只在条件成立时记录；
  未被触及的 resting 止损永不成交）；新增「resting 止损在没有用户动作时也会被重新评估」。

### Impact

- `src/fd_paper_trading/execution/fill_engine.py`、`service.py`、`stop_sweeper.py`（新）、
  `api/app.py`（lifespan）、`config.py`（`stop_sweep_seconds`）。
- 生产数据已在 2026-11-14 实测（fd-prod pod 内只读查询）：`orders` 全表 **4 行，全是
  MARKET/FILLED，零张止损单**。也就是说没有"被错误标记为 triggered"的历史脏行要修——
  否则升级后第一次评估会让那些单子按止损价成交一次。这条测量同时决定了不需要数据修复迁移。
