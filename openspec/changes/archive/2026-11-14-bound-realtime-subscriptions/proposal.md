## Why

`ws.py` 的订阅是一条**没有任何上限**的路径：

```python
if mtype == "subscribe":
    conn.subscribed.add(symbol)      # ws.py:84
```

每个 tick（1 s）对**每个被订阅的符号**各发一次 `provider.get_quote()`。astock 的
`get_quote` 每次是一条覆盖 30 天窗口的 SQL，于是"能订阅多少"直接等于"能压多少库"。

2026-11-14 实测（一个客户端订阅 1000 个 A 股形态的 6 位代码，provider 每次 1 ms 模拟
一次 DB 往返）：

```
symbols subscribed: 1000
provider.get_quote calls in ONE tick: 1000
one tick took: 1167 ms   (tick_interval = 1000 ms)
```

**一个 tick 已经超过整个间隔**——也就是说 tick 循环永远追不上，`_emit_ticks` 从头到尾
串行 `await`，于是这一个用户的订阅会把**所有用户**的实时流一起拖慢。符号甚至不必存在：
`normalize_symbol` 认 6 位数字形态就够了，之后每次都进库查一遍再返回 `None`。

这条不是新发现，`paper-trading-api` 归档 change 的 task 5.3 写着
「enforce per-user symbol-subscription limits」，一直未勾；5.6 的用例也因它缺席。而
`per_user_symbol_limit` 那个配置从 2026-11-06 起就是**声明了但零执行点**的死键
（2026-11-13 已按事实删除，理由：用户限额统一在组配置，且 user-groups 规格明写
不许有并行的 per-user 覆盖层）。

所以正确的落点是**组配置**，不是恢复那个孤儿字段。

## What Changes

- 新增组配置键 `max_subscriptions`（默认 50）：**按用户**统计该用户全部连接的不重复
  符号总数，超过即拒绝新增订阅。`0` 表示不限（与 `order_rate_per_second=0` 同一约定）。
- `/ws` 超限时回一条 `{"type": "error", "code": "subscription_limit", ...}` 事件并**保持连接
  打开**——关连接会把同一用户其它正常标签页一起打断，而且用户无从知道原因。
- 已订阅的符号**重复订阅不算新增**（在限额上重发同一符号不报错）：前端重连恢复订阅时
  会重发，把幂等操作判成超限会制造假错。
- tick 循环取价改为**有界并发**（模块常量 `_TICK_FETCH_CONCURRENCY = 8`，远低于
  `db_pool_size + db_max_overflow = 30`）：串行取价下即使合法规模也会拖过间隔。
- 限额解析走既有 `group_config(user, "max_subscriptions")`，与其它五键同一机制。

## Capabilities

### Modified Capabilities

- `web-realtime`: 新增「Bounded quote subscriptions」需求（按用户的订阅上限、
  `subscription_limit` 错误事件、重订阅幂等、有界并发取价），既有三条需求不动。
- `user-groups`: 「User groups with config」的键表增加 `max_subscriptions`（50）；
  「Limits enforced at the API layer」的执行点增加 `/ws` 订阅。
