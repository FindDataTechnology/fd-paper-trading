## Context

`/ws` 是唯一一条"客户端说订阅多少就订阅多少"的输入面。tick 循环每秒一次，对聚合后的
每个不重复符号各调一次 `provider.get_quote()`。生产 provider（astock / fd-open-data）
的 `get_quote` 都是**一次数据库读**，所以订阅数量是数据库负载的乘数。

实测（2026-11-14，单客户端 1000 个 6 位代码，provider 每次 1 ms）：一个 tick 内
1000 次取价、耗时 1167 ms > 间隔 1000 ms。

## Goals / Non-Goals

**Goals**

- 一个用户不能通过订阅把共享的 tick 循环压垮（不管他自己开几个连接）。
- 超限是**可解释的拒绝**，不是静默丢弃，也不是掐断连接。
- 合法规模下的取价不再串行拖过间隔。

**Non-Goals**

- 不做跨进程/跨副本的订阅配额（与订单限流器同一取舍：进程内实现，
  多副本时每副本各计一份——见 `execution/service.py` 的注释）。
- 不改前端协议形状：仍是 `{type, symbol}` 消息，新增的只是**服务端可能回的错误事件**。
- 不按符号做白名单校验（`allowed_symbols` 是策略面的事；订阅一个不存在的符号
  本来就是合法的——它只会一直拿不到 tick）。

## Decisions

### D1 限额按**用户**而不是按连接

一个用户可以开多个标签页/多台设备。按连接限额等于"每开一个标签页就多一份预算"，
而这正是要堵的口子。因此在 manager 内按 `user_id` 聚合所有连接的不重复符号。

实现放在 `ConnectionManager.try_subscribe(conn, symbol) -> bool`：在 `_lock` 内统计
`sum(len(c.subscribed) for c in self._connections if c.user_id == conn.user_id)` 的对齐口径
（按集合去重后再计数），加上新符号是否越界。

代价：每次订阅一次 O(连接数 × 订阅数) 扫描。订阅事件是人工频率（每次点击一条），
而 tick 是每秒一次，两者不同量级，不需要另建索引。

### D2 `0` = 不限

与 `order_rate_per_second: 0 disables the limiter` 同一约定（同一文件里已经有这个先例）。
不自造第二套"空值/负数/None"的语义。

### D3 超限回错误事件，不关连接

- 关连接：同一用户其它标签页一起断，且前端只会看到"断了"，无从知道是因为订阅限额。
- 静默丢弃：客户端以为订阅成功，UI 永远不更新，最难查。
- 回事件：`{"type":"error","code":"subscription_limit","limit":N,"symbol":S,"message":...}`，
  连接与既有订阅全部保留，客户端可以退订后重试。

### D4 重订阅幂等

前端重连后会重发当前活跃符号（`web-realtime` 既有需求：`restores prior subscriptions after
reconnecting`）。若在限额上重发同一符号被判超限，前端会显示一个假错误。因此
"已在本用户的订阅集合里"的符号直接返回成功、不计新增。

### D5 tick 取价有界并发

`_TICK_FETCH_CONCURRENCY = 8`：`asyncio.gather` + `Semaphore`。8 的取法——低于
`db_pool_size(10)`，留两格给订单/查询路径；不为每个符号开一个任务，避免上千个
DB 连接同时被索取（pool 满时 asyncpg 会排队，等于把串行推迟到别处）。

这里刻意**不用**"每符号独立任务 + 全量 gather"：符号数由用户决定，任务数不该由它决定。

### D6 不加迁移去把键播进各组的 config

`max_universe_size` 当初带了一道迁移（`b4e2c7a18f03`）把键**只在缺失时**播进每个组的
config。这里刻意不照做：`group_config` 的兜底已经让"组里没有这个键"解析成 50，
而一旦把当前默认值写进行里，以后改默认值就再也影响不到那些行——**默认值会静默冻结成
旧值**。键的可见性由 `/api/v1/groups` 返回的实际 config 与
`DEFAULT_GROUP_CONFIG` 的注释承担，不需要用数据副本换。

## Risks / Trade-offs

- **默认 50 可能对某些用户偏小**：单个图表只订阅 1 个符号，移动端按 tab 订阅几个。
  50 是"人不会手点这么多"的量级；真有需要就改组配置（这正是放组配置而不是环境变量的原因）。
- **限额不覆盖到"已订阅但从未退订"的泄漏**：连接断开时 `subscribed` 随连接对象回收，
  没有跨连接的状态，所以不会累积。
- **有界并发改变了 tick 内的失败次序**：取价失败仍按符号各自 `continue`（既有语义），
  只是并发执行；事件顺序按符号聚合后的字典序输出，与之前一致。
