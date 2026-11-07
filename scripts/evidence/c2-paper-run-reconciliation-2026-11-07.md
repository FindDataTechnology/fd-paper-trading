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

## 挂账清偿（2026-11-07 上午追加）

| 挂账 | 终态 |
|---|---|
| template run `price_caliber` 不落 | **已修+已上产**。两滚：sha-6489ed8（`_mark_running` 以 `spec.symbol` 兜底）→ sha-f0d90d9（探针窗 35d）→ **sha-ae999e8（探针窗 400d，实测 35d 仍会在跨月滞后源上落 unknown）**。终验 run a1405764：`price_caliber=forward / price_source=astock_daily / last_bar_date=2026-07-10` 全落库。修法演进中实测两个中间态（same-day→unknown；35d→unknown），最终以 400d 窗跨过 feed 滞后；unprovable 尾巴如实 missing 不动声明口径。381 passed 全绿。 |
| CI 前端未处理错误 | 已修（a1a6fcc）：BacktestPanel 测试 stub 双图组件——jsdom 无布局引擎，lightweight-charts 首次 adjust 异步抛 `Value is null`。61/61 绿、tsc 绿。 |
| `deployToRegistry` >120s | **定因立档不修**：deployToRegistry 自身是串行小请求（composeAgentCard 纯函数、PUT+toggle/agent），慢在 registry `PUT /api/agents/register` 内部的注册时安全扫描（匿名拉 SKILL.md×技能 + 后端卡片探测）。修法属 registry 侧（扫描并行化/异步化），另立 change。 |
| rotation pack 重发 | **v10 已发布**（packId noL2XKaTZP2kiERrC_gtKA）。发布拦两道既有门：官方 en 覆盖 7/8（缺 `i18n.en.tags`）→ 按「en tags 数须与 zh 对齐」规约补 4 枚后过门。v10 的 rotation-ranking 内容与已推 finddata-skills@0160eb2 逐字节一致（manifest==repo True）。**面板重部署待做**：deploy 路由按设计只认用户会话（柏讯面板），生产鉴权不绕行；重部署后 served descriptor 才从 v9 → v10。 |
| DAAS 仓 spec 树 27 个 strict 失败 | **42/42 全绿**。三类机械修：column-provenance/data-snapshots（REMOVED 能力补 SHALL 需求体）、data-fetch-gateway（泛别名需求补场景块）、24 个占位 Purpose 逐个按 spec 正文写实。openspec/ 在该仓为有意本地（gitignore），不随仓推。批6 A线 seed/refresh 三脚本已上仓（gitee acaaa90）。 |
| cheap3 磁盘三度复发（08:57 DiskPressure，80%） | 处置完成：docker 未用镜像 prune（-2.9G）+ k3s crictl prune + 44 个 Evicted 尸体 pod 清零 → DiskPressure=False、facet 200、fpt health ok。**真因立档：cell 1da519d4（壹座）`data/trace.db` 已 2.1G 持续增长**——回合 trace 无限累积是周期性复发根源，治理（trace 轮转/上限）需产品面决策，另账。facet hostPath 钉死 cheap3 的结构性风险再次实证。 |

新挂账（本次新立）：
1. registry 注册时安全扫描串行化（deployToRegistry >120s 的真实来源）——立 change。
2. cell trace.db 无限增长（1da519d4 已 2.1G；2b043ce0 2.1G 同型）——trace 轮转/上限产品决策。
3. rotation pack v10 面板重部署（用户动作，30 秒）。

## 挂账二次清偿（11-07 午后，「处理所有挂账，然后归档」轮）

| 项 | 终态 |
|---|---|
| deployToRegistry >120s | **已实施**：paas `lib/agent-serving.js` 两阶段并行（技能 fan-out 并行 + agent 注册 Promise.all，端口规划保持串行）。慢源定谳=registry 注册时对每 agent 跑 a2a-scanner 子进程（`agent_security_scan_on_registration=true`，≤60s/agent；skill-scanner 只在手动 rescan）。commit c1cdeb79a（paas main，经 release 分支 3e27118d4 cherry-pick+rebase）。agent-serving 家族 126/126 绿。**未滚版**——随 paas 下次出片生效。 |
| cell trace.db「无限增长」 | **定谳翻案+根治**：trace.js 本有每小时 prune+增量 vacuum；真根因=cell 1da519d4 的 trace.db **SQLite malformed**（`prune failed: database disk image is malformed`）→ prune 静默失败 → 只增不减。处置：`TRACE_RETENTION_DAYS=3` 上产（2f77dd0，env 验证）+ 坏库备份（cheap3:/opt/trace-db-1da519d4-broken-20261107.bak.gz，424M）+ 移出删除 → 磁盘 80%→75%，下次使用重建空库。对照：3fdc7b8e 正常 pruned 36632 rows。 |
| pack v10 重部署 | **定谳=用户面板动作**：deploy 只认用户会话（Logto cookie/MP JWT；client_credentials 被 Logto 拒「grant type not allowed」）。柏讯面板→自适应轮动套件→重新部署。鉴权面设计使然，不绕行。 |

新教训：**「库一直涨」先查 prune 日志再定罪保留策略**——malformed 库让 prune 只打 warn 静默失败，保留机制在坏库上等于零。
