# 模拟盘 run 真起来 + 三方对账（2026-11-07）

## 结论

生产 `fd-paper-trading`（sha-3be3d49）的 A 股 astock 价格通道**真跑通**：
run `e22d060f-0931-4142-91df-ea3fab0360f9`（600000 浦发银行，SMA 5/20，paper mode）
RUNNING、零 error 事件、首 tick 消费到 pinned source 真实 bar，
`last_bar_date=2026-07-10`（astock_daily 在分片库的真实最大日期）。

## 三方对账

| 腿 | 证据 | 结果 |
|---|---|---|
| ① run 记录 | `strategy_runs.params.last_bar_date = 2026-07-10`；RUNNING；error=null | ✅ |
| ② 会话/账面 | `strategy_events` 1 条（run started）、0 error；`strategy_equity_points` 落 2026-07-10 / 100000 起始点；orders=0（SMA 首日无交叉，评估器按 250 根历史 prime 后仅最新 bar 可交易，预期内） | ✅ |
| ③ 数据源真值 | entity 3084（=600000）@2026-07-10：open 8.98 / close 9.06 / high 9.11 / low 8.92 / vol 765022，`source_used='astock_daily'`（pinned） | ✅ 一致 |

## 对账拦下的三个真缺陷（本批修掉）

1. **老镜像无 astock 分支**：sha-587089c 的 provider 工厂没有 `astock` 值，
   `MARKET_DATA_PROVIDER=astock` 静默落 **simulated 回落**——`last_bar_date=2026-11-06`
   是模拟器伪造的当日、equity 恒 100000。修复=推 origin 5 提交出片 sha-3be3d49
   （途中还修了 web 夹具漏 `stop_price` 的 TS 编译错）。
2. **ASTOCK_DSN 口令错串**：分片 FDW server options 里的 `28ux…` 口令只对
   100.64.0.6/0.7 分片机有效；主库 100.64.0.3 的 fd 口令与
   `FD_OPEN_DATA_MCP_DATABASE_URL` 同串（`vxlA…`）。两串实测后纠偏入 Git
   （commit 19596ca）。
3. **secret 曾被占位覆盖**：11-06 的占位 secret sync 摧毁了带外注入的
   `DATABASE_URL` / `FD_OPEN_DATA_MCP_DATABASE_URL` / `JWT_SECRET` 三键
   （10-14 建仓时从未进 Git），pod crashloop alembic→localhost:5432。
   从旧 pod `/proc/1/environ` 抢救回填入 Git（commit cc83b69）。
   教训：**secret 键只增不删、新键先落 Git 再同步**。

## 新发现（如实挂账，非阻塞）

- **template run 的 `price_caliber`/`price_source` 不落**：`_mark_running` 取
  `run.params.universe[0]`，template run（symbol 在 spec 里）universe 为空 →
  caliber 键缺失。astock provider 明明实现了 `get_bars_with_coverage`。
  修法一行：first_symbol 兜底取 spec.symbol。留作后续小修。
- 事件表 `strategy_events` 列名是 `type/ts/data/message`（`event_type` 是文档口误）。

## 生产拓扑事实（本次实探确认）

- astock provider 单 DSN 面：`entities` + `concepts` + `semantic_observations_all`
  三查询面同库 = 主 open-data 库（100.64.0.3:30432/fd_open_data）；A 股价格行物理在
  shard3（100.64.0.7，entity_id 58..5322 共 4576 只、max 2026-07-10），
  经 FDW `ft_sem_obs_xinru3` 联邦视图透出。
- tick poll 默认 1800s（design D5）；本验等待两轮后才见首 tick。
- pod 需手动 delete 才能重读 envFrom secret（envFrom 不热载）。
