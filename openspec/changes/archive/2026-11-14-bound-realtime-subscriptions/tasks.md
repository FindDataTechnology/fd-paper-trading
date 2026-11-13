## 1. 先量后改

- [x] 1.1 实测无上限的代价：单客户端 1000 个 6 位代码 → 一个 tick 内 1000 次 `get_quote`、耗时 **1167 ms** > 间隔 1000 ms（读数记进 proposal；量法：`ConnectionManager._emit_ticks` 直驱 + 每次 1 ms 的计数 provider）
- [x] 1.2 确认 `get_quote` 在生产 provider 上是**一次库读**：`astock.py::get_quote` → `_query_bars`（30 天窗口），不是内存查表

## 2. 实现

- [x] 2.1 `DEFAULT_GROUP_CONFIG` 增 `max_subscriptions: 50`（`persistence/models.py`）；`groups.py` 的签名词表同步
- [x] 2.2 `ConnectionManager.try_subscribe(conn, symbol, limit) -> bool`：在 `_lock` 内按 `user_id` 聚合跨连接的不重复符号；已持有 → 成功且不计新增；`limit <= 0` → 不限；越界 → `False` 且不落订阅
- [x] 2.3 `ws.py`：`_authenticate` 返回 `(user.id, limit)`，限额**在会话内**解析（detached ORM 上读 `user.group` 会变成闭连接上的懒加载）；超限回 `{"type":"error","code":"subscription_limit",...}` 并保持连接
- [x] 2.4 tick 取价改有界并发：`_TICK_FETCH_CONCURRENCY = 8` + worker pool（任务数＝并发上限，不是符号数）；失败仍按符号各自 `continue`

## 3. 判据（每条都能红）

- [x] 3.1 `test_subscribing_past_the_cap_is_refused_and_explained`：错误事件含 `code/limit/symbol`、消息点名限额、越界那条没进去、连接未关、既有订阅照旧收 tick
- [x] 3.2 `test_the_cap_is_per_user_not_per_connection`：第一个连接占满后第二个连接被拒；退订一个后成功
- [x] 3.3 `test_resubscribing_a_held_symbol_at_the_cap_is_idempotent`：重订阅不报错、计数不变（用一条**该被拒**的后继消息当"前一条已处理"的观测点，不用固定 sleep）
- [x] 3.4 `test_zero_disables_the_cap`：`0` → 四条订阅全成立、零错误
- [x] 3.5 `test_tick_fetch_is_concurrent_but_bounded`：40 个符号、每次 1 ms，断言 `1 < in-flight 峰值 <= _TICK_FETCH_CONCURRENCY`
- [x] 3.6 注入自证（五次，各自只红该红的那条）：
  - 跨连接聚合改回 `if c is conn` → 3.2 红
  - 去掉上限判定（`if False`）→ 3.1 / 3.2 / 3.3 红
  - 取价改串行（`workers = 1`）→ 3.5 红
  - `limit > 0` 改 `limit >= 0` → 3.4 红
  - 删掉重订阅幂等分支 → 3.3 红
- [x] 3.7 额外：`test_a_junk_limit_does_not_take_the_socket_down`（组配置写成 `"fifty"` 只是退回默认值并记警告，不崩连接）

## 4. 收尾

- [x] 4.1 `pytest tests` 全绿：**757 passed**（改动前 751，新增 6 条）
- [x] 4.2 `openspec validate 2026-11-14-bound-realtime-subscriptions --strict` 通过
- [x] 4.3 归档并核对 `Totals`：`web-realtime` +1 ~1，`user-groups` ~2
- [x] 4.4 归档后 `openspec validate web-realtime --type spec --strict` 与 `user-groups --type spec --strict`
- [x] 4.5 `paper-trading-api` 归档 change 的 5.3 / 5.6 按事实更新（5.3 现在可以勾了，5.6 的上限用例已补）
