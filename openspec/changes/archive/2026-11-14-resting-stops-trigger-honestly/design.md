## Context

止损的 live 触发只有一条路径：`PaperExecutionService.evaluate_open_stops`。调用点两处
——挂单后（`api/routes/orders.py`）与策略 run 的轮询（`strategy/runner.py`）。撮合计算器
`SimFillEngine` 无状态，只做「这一张单在这个报价下成交还是不成交」的纯判断。

2026-11-14 排查发现这条路径上有两个问题，一个记错账、一个没有任何东西去调它。

**记错账**：`evaluate_open_stops` 用 `result.status in ("FILLED", "OPEN")` 判断「刚刚
触发了」，而 `OPEN` 在计算器里有两个相反的含义。于是每一张 resting 止损单在挂单后
立刻被盖上 `triggered_at`，下一次评估回灌 `triggered=True`，触发判定被跳过。

实测（bid=269.95，止损 268.95，价格未动）：

```
stop 挂单后:  OPEN   triggered_at=16:34:15Z   stop_price=268.95
同标的另一张 limit 单（自身不成交）
止损单现在:  FILLED @ 268.95    持仓 []
```

**没人调它**：design D1（`2026-11-06-stop-order-execution`）把「新报价到达」枚举为
runner tick / 手动补单 / run 评估——两条是用户或策略的动作，纯手动账户因此没有 wake-up。

## Goals / Non-Goals

**Goals**

- `triggered_at` 只在**触发条件真的成立**时记录；同一份规则同时决定成交与记账。
- 一张从未被触及的 resting 止损单，在任何评估次数下都不成交。
- 纯手动账户的 resting 止损也能在价格穿过止损价后成交，不依赖用户再下单。

**Non-Goals**

- 不做盘中撮合（日线粒度是服务的现实；sweeper 读的是真 provider 的同一个收盘价）。
- 不改撮合计算器的成交语义（成交价、买卖方向规则、STOP_LIMIT 触发后转限价）。
- 不做跨 pod 的单实例 sweeper 选举：`evaluate_open_stops` 对订单行取 `FOR UPDATE`、
  对账户行也加锁，两个副本同时扫也只会有一份成交生效（幂等靠 OPEN 过滤 + 行锁）。

## Decisions

### D1 触发规则抽成 `trigger_condition_met`，成为单一真源

`SimFillEngine._compute_stop_fill` 里的方向判断（买 stop: `ask >= stop`；卖 stop:
`bid <= stop`）被抽成静态方法，`_compute_stop_fill` 与「记录触发时刻」都调它。

**理由**：这次缺陷的根因就是**同一件事被两处各自推断**——计算器按价格判触发，
服务端按「返回状态是不是 OPEN」猜触发。只要允许第二处推断，迟早分叉。抽出来之后
「成交」与「记账」在定义上不可能不一致。

### D2 先记账、再计算成交

顺序改成：条件成立 → 盖 `triggered_at` → 带 `triggered=order.triggered_at is not None`
算成交。这样「这一次评估里刚触发」与「之前已触发」在计算器眼里是同一件事，不需要
第三种状态。

**备选**：给 `FillResult` 加一个 `triggered: bool` 字段。否——那等于把同一件事再表达
一遍，而 D1 的目标正是只有一个表达。

### D3 sweeper 每 60 秒，可用 `STOP_SWEEP_SECONDS` 调，0 关闭

价格本身是日线收盘，60 秒并不带来"更细的行情"，只是把「报价到达」这个时点从
"用户动作"变成"北京时间里的固定节拍"。默认开（这正是缺陷形态：不开启就等于没修）。

### D4 跳过有活跃 run 的账户

runner 自己评估 run 账户的止损，并在触发时写一条 run 的 ORDER 事件（"stop triggered:
…"）。sweeper 抢过去之后 runner 查不到 OPEN 的止损单，那条事件就永远不写——**账本
对、日志缺**，而这正是本仓一再强调的"为什么成交"不可丢失。

### D5 不把触发挂到 `/ws` 的报价 tick 上

`_emit_ticks` 对价格加了 ±0.1% 的确定性抖动，让流看起来在动。用那个价格触发止损，
等于按一个**没有人报过的价**成交。sweeper 读的是 provider 真报价。

### D6 不做数据修复迁移

只读查过生产 `orders`：4 行、全部 MARKET/FILLED、零张止损单 ⇒ 没有脏 `triggered_at`
要清。若将来在别的环境发现有 `order_type=STOP AND status=OPEN AND triggered_at IS NOT NULL`
的行（STOP 触发即成交，这种组合只可能是旧 bug 留下的），那批行的 `triggered_at` 应清空
再上线；STOP_LIMIT 无法从数据上区分「legit 的触发后 resting」与「挂单时误盖」，所以
不写通用迁移——判不准的修复比不修复更危险。

## Risks / Trade-offs

- **sweeper 每 60 秒查一次订单表**：`(status, order_type)` 上没有专门索引；`orders`
  表在当前规模是 4 行，全表扫描的代价可忽略。若订单表变大，加一个部分索引
  （`WHERE status='OPEN' AND order_type IN ('STOP','STOP_LIMIT')`）即可，不在本次范围。
- **sweeper 与挂单路径并发**：两者都对账户行 `FOR UPDATE`、对订单行 `FOR UPDATE`，
  串行化后不会重复成交。代价是极端情况下挂单要等一次扫描的锁。
- **`stop_sweep_seconds` 是新的可配项**，`.env.example` 需要一行（已加）。
