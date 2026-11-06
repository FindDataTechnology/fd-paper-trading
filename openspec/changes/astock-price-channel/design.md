## Context

动机与挂账出处见 `proposal.md`。现状（读自本仓 2026-11-06）：

- **provider 家族**：`marketdata/` 现有 simulated / polygon / fd-open-data 三实现，全部实现 `MarketDataProvider` 接口；fd-open-data 走 `fd-open-data-mcp` 库内读（`read_range` 批量），`get_bars_with_coverage` 的覆盖度契约（missing/market_closed/fill_failed/fingerprint）已由 backtest-data-coverage spec 钉死并被 coverage gate 消费。
- **数据底座**：分片库 `astock_daily`（4,576 只、2006–2026、前复权）——dashboards 线 `build_astock_trading_spec.py` 的既有读法是权威参考：`FD_OPEN_DATA_DSN`（汇总库 100.64.0.3:30432）的 `semantic_observations` 基表不收 astock_daily，须经 `semantic_observations_all` 联邦视图并把 `source` 钉死 `astock_daily`（混两个复权基准会出"收盘价高于当日最高价"，实测过）。概念 id：open=233 / close=234 / high=235 / low=236 / amount=238。
- **政策边界**：公共本体 `price.*` 按政策弃用且不得重注册；本变更是服务内部只读消费，不触碰公共目录（用户 2026-11-06 确认）。
- **消费方**：回测（coverage gate 消费 `get_bars_with_coverage`）、live runner（`_latest_close` / `get_daily_bars`）、撮合（`get_quote` 半价差推导）、instrument 搜索（web 自选股 + 下单前置）。
- **批 6 依赖线**：`stop-order-execution` 的双轨取数与本变更的口径钉死相关；工作区根 `add-trading-real-data-closeout` 的因子补跑/全量实测以本变更生产可用为前置。

## Goals / Non-Goals

**Goals:**
- A 股个股在生产 provider 上真实可交易：搜索→下单→撮合→回测→live 全链吃真实日线。
- 复权口径成为一等事实：provider 声明、coverage 携带、run 指纹留痕。
- 基准端到端收口：000300/000905 指数日线可得后，backtest-engine-v2 8.3 挂账闭环。
- 默认行为零变化：默认 provider 不动，未配置凭据不拖垮既有部署。

**Non-Goals:**
- 不做盘中/tick/实时行情（日线 fill-at-close 既有语义延续）。
- 不自研复权计算（消费 `astock_daily` 既有前复权口径）。
- 不写任何库；不注册公共 `price.*` 概念；不做做空与杠杆。
- 不改撮合语义与 group 限额数值（只要求限额对新 provider 同样生效）。

## Decisions

### D1. 读法：库内直连联邦视图，不复用 fd-open-data-mcp 的分发面

**选择**：`AstockMarketDataProvider` 用自己的 asyncpg/SQLAlchemy async 只读会话读汇总库的 `semantic_observations_all` 联邦视图（`source='astock_daily'` 钉死），一次窗口一条 SQL（透视 OHLC），不经 fd-open-data-mcp 的概念分发。
**理由**：dashboards 线已验证这条读路（钉源纪律、临时视图 pin_ohlcv_read）；fd-open-data-mcp 的概念目录里 A 股价格家族是弃用态，绕行它意味着要么改公共目录（违政策）要么在库内造影子概念（脏）。直连只读视图最小且诚实。
**备选**：(a) 给 fd-open-data-mcp 加 astock 家族——碰公共目录政策红线，否；(b) 走生产 fd-daas-mcp——PVC 库 A 股只有 6 只 seed，覆盖不了 4,576 只，否。

### D2. 实体解析：entity_id 直查，代码规范同时收 `600036` 与 `600036.SH` 两种写法

**选择**：search/get_instrument 查联邦视图的实体表（code/name/venue），venue 按代码段位映射 `XSHG`（6 开头）/`XSHE`（0/3 开头）；入参规范化接受裸代码与带后缀两种写法，内部统一为裸代码。
**理由**：entity_id 已在 dashboards 线核对过（600036=3087 等）；两种写法都收是 web 搜索体验的最小让步，内部单写法避免双键漂移。
**备选**：只收裸代码（对 web 用户不友好）；收全后缀族（.SS/.SH/.SHE…组合爆炸），否。

### D3. 指数日线：spike 定论——候选 B 死路，端到端交接数据线（2026-11-06 实证）

**选择**（spike 后修订）：指数实体在库中存在（沪深300=9676、中证500=10263）但**全库零观测**（候选 B 死路；候选 A=akshare 种子入 DAAS 也救不了本 provider——D1 读的是分片库联邦视图，且价格抓取服务端冻结）。证据与原始探针输出见 `reports/astock-spike-index-data.md`。provider 代码侧已备好指数读路（`_resolve_entity` 在 stock 无数据时 fall back 到 index 实体；spec「可用即用、缺了报缺」成立——冒烟第 8 项实证当前恒为报缺态）。种子落地受 2026-10-07「服务端不重开价格抓取」政策约束，属数据线决策，不在本 change 的只读授权内。
**交付形态**：本 change 完成 provider 就绪 + 报缺纪律实证 + 挂账交接；backtest-engine-v2 8.3 **继续如实挂账**，指数种子一到即端到端收口、不改接口（先例：批 4 "backtest.executable=false 写进机器可读输出"——机制就绪 ≠ 端到端通过）。
**备选**：本仓写脚本直插分片库指数行——跨服务写平台数据仓、违"只读"承诺与政策，否。

### D4. coverage 与指纹：口径是增量字段；日历用参照篮探针（实测后修订）

**选择**：`get_bars_with_coverage` 沿用既有契约键（missing/market_closed/fill_failed/fingerprint）+ 新增 `adjusted: "forward"` + `source`；run `params` 逐标的记录带口径。休市判定采用**参照篮日历**：5 只全史密集大盘股（600000/600036/600585/600900/601318，astock_daily 4,300-4,700 行/只）在同一 pinned source 的交易日存在性即市场开盘证据——平日 ≥2/5 篮成员有行=开盘日；篮无行=休市，**但不得越过篮子覆盖上限**（源停更日之后不可证休市：停爬看起来和长假一模一样——fail-closed 计入 missing）。周末纯区间算术判定，不花查询。探针查询失败整窗 `fill_failed`。
**理由**：全市场日历扫描实测不可用（无 entity 过滤的 group-by >200s/年窗；单日市场计数也要 ~3.8s）。参照篮走 entity_id 索引：260 平日×5 参照实测 **4.9s**；730d 全窗 coverage（透视+篮+上限）实测 **2.4s**。截断标的实证：600519（2015 截断）在 2025-2026 窗口整窗报缺而非伪装休市。
**备选**：(a) 静态节假日表——外部日历腐烂，且 coverage.py 既有纪律明确拒绝用假设日历数交易日，否；(b) 市场全量日计数——实测成本不可接受，否；(c) 缺口一律记 missing（不辨长假）——每个跨长假窗口被拒，把 spec 的 market_closed 语义打空，否；(d) 全市场日历仅在必要时按日探测——首测 730d 超时即证伪，改篮。

### D5. 部署：凭据走部署 secret 注入，未配置零行为

**选择**：DSN 注入 fd-infra-deploy 生产清单的 env（secret 引用）；`MARKET_DATA_PROVIDER=astock` 且凭据缺失 → 启动明确失败；其他情况下 provider 不实例化。
**理由**：与既有 service-deployment spec 的"连接串经部署环境供给、不落仓库"要求同构；dashboards 线的 DSN 管理是先例。
**备选**：先落配置文件——违既有 spec，否。

## Risks / Trade-offs

- **[联邦视图跨分片查询的时耗]** → 回测窗口读一次全景（单 SQL 透视）而非逐日分发；730 天上限下量级可控（dashboards 线同视图实测可用）。若实测过慢，任务内回退为按年分片读，不改契约。
- **[astock_daily 停更风险]** → coverage 的 missing/fingerprint 会如实暴露陈旧；不建缓存替身、不伪造新鲜度。
- **[指数 spike 双候选都不可行]** → 基准保持报缺态收口（8.3 如实挂账），不阻塞本变更其余部分。
- **[默认 provider 不变的部署漂移]** → 生产切 `astock` 是部署清单一行改动，验收任务覆盖真库冒烟，属上线步骤而非本变更验收。

## Migration Plan

1. provider 实现 + 单测（simulated 库桩测读路；真实库冒烟单列任务）。
2. 搜索/instrument/quote 接线 + group 限额回归。
3. coverage 口径字段 + run 指纹增量（既有测试零成本默认下不变）。
4. 指数 spike → 定路 → 指数序列可得 → 基准端到端任务。
5. 部署清单 env 注入（fd-infra-deploy 侧提交）；生产切换与真库冒烟是上线动作，任务如实记录。
6. 回滚：provider 由环境变量选择，回退即恢复旧行为；无库写、无迁移。

## Open Questions

（两条提案期悬置均被 2026-11-06 spike 实证关闭，结论在 D3/D4；证据 `reports/astock-spike-index-data.md`。）

- ~~指数日线走哪条候选~~ → 指数实体全库零观测（B 死路），A 卡 10-07 抓取政策；端到端如实挂账，种子一到即收口不改接口。
- ~~volume 概念取舍~~ → 概念 237 存在且 astock_daily 密集有行；`_query_bars` 已含 volume 列，缺值归 0（既有 Bar 消费者兼容，单测覆盖）。
