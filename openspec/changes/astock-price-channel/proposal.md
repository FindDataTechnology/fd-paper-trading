## Why

本仓的回测/模拟盘/权重模式/live runner 机制层已全部就位（批 0–4 归档），但生产市场数据 provider 只有基金 NAV（`fd_open_data_concept=nav.unit`），A 股个股 `price.*` 在公共本体按政策弃用——**核心交易线至今没有任何 A 股个股可以在生产上真实交易**。批 4 实测三堵墙都源于此：backtest-engine-v2 8.3 基准端到端受阻（无指数序列）、factor-selection 四项未出数（universe 无 A 股）、pack-adaptive-rotation 6.4 五项待修（基准序列缺失等）。本变更是批 6「交易线真实标的版」的根：不解决标的通道，其余收口全是空转。

数据前提已具备：平台分片库 `astock_daily` 4,623 万行 / 4,576 只 / 2006–2026 前复权日线（dashboards 线已稳定消费，读法经 `semantic_observations_all` 联邦视图钉 `source=astock_daily` 验证过——两个复权基准混读会出"收盘价高于当日最高价"，本变更沿用其钉源纪律）。

## What Changes

- **新增 A 股日线 provider**：`AstockMarketDataProvider` 接入 `fd-paper-trading` 生产 provider 家族，读汇总库联邦视图（`source` 钉死 `astock_daily`），提供 `get_daily_bars` / `get_bars_with_coverage` / `get_latest_close` / `get_quote`（日线收盘快照）/ `search_instruments`（A 股代码与名称搜索）。与既有 `fd-open-data`（基金 NAV）、`simulated`、`polygon` 并列，按配置选择，默认值不变（不破坏既有部署）。
- **标的搜索接入 A 股**：instrument 搜索返回 A 股实体（代码/名称/交易所），`per_user_symbol_limit` 与既有 group 限额对新 provider 同样生效。
- **指数日线供数（基准与轮动的底座）**：先 spike 后定路——候选 A：akshare 种子脚本入 DAAS（`scraw_*` 表 + 指数规则）；候选 B：指数实体并入 `astock_daily` 家族。产出沪深300（000300）/中证500（000905）日线序列后，回测基准对比端到端收口（backtest-engine-v2 8.3 挂账）。
- **复权口径进 coverage 判定**：`get_bars_with_coverage` 报告 provider 复权口径（前复权），口径不可判时如实标注；缺口判定规则不变（既有 backtest-data-coverage 语义延续）。
- **凭据与部署**：只读 DSN 经部署环境注入（沿用 dashboards 线 `FD_OPEN_DATA_DSN` 的秘密管理先例），不落代码、不进镜像。
- **政策边界（不重开公共本体）**：本 provider 只服务本仓交易服务内部读数，不注册公共 `price.*` 概念、不回填服务端冻结本体——2026-10-07「价格走本地 DAAS、服务端只策展」政策针对公共数据产品面，本变更为自有服务内部消费，不与之冲突（用户 2026-11-06 确认）。

## Capabilities

### New Capabilities

- `astock-market-data`：A 股日线 provider 的行为契约——数据读面（联邦视图钉源）、复权口径声明、标的搜索、coverage 报告、与 group 限额的关系、基准序列可用性（指数日线供数落地后 000300/000905 可取、不可用明确报缺）。

### Modified Capabilities

- `market-data`：provider 注册表新增 `astock` 选项；provider 选择与回退语义不变；`get_quote` 对日线型 provider 的语义（最新收盘快照）显式化。
- `backtest-data-coverage`：coverage 报告增加复权口径字段；基准符号的数据可用性判定并入既有报缺纪律（缺数据明确报缺而非静默省略——既有需求延伸到 A 股基准）。
- `service-deployment`：新增只读 DSN 环境注入要求（凭据不进镜像不落代码；未配置时 provider 不可用但服务其余功能不受影响）。

## Impact

- **仓**：`fd-paper-trading`（`src/fd_paper_trading/marketdata/astock.py` 新增、`config.py` 增配置、搜索/coverage 接线、部署清单环境变量）。
- **数据**：不写任何库；只读分片库联邦视图。指数日线若走候选 A 则在 DAAS 根另跑种子脚本（只增不改，沿用 `price-channel-daas-seed` 纪律）。
- **下游**：批 6 依赖线——`stop-order-execution`（除权双轨取数依赖本变更口径钉死）、工作区根 `add-trading-real-data-closeout`（因子四项补跑、6.4 修复、全量回测实测都以本变更生产可用为前置）。
- **向后兼容**：默认 provider 不变；既有回测/live run 行为不受影响。
- **不做**：不做盘中/tick 数据；不做写回本体；不做实时行情（日线 fill-at-close 既有语义延续）；不复权计算自研（直接消费 `astock_daily` 既有前复权口径，并在 coverage 中如实声明）。
