# Proposal

## Why

纸面实盘运行（PAPER run）**记录不出任何绩效数字**：`run.stats` 只在回测分支被写（`api/routes/strategies.py:458/485/541/555/597/685`），`strategy/runner.py` 全文没有任何 `stats` 引用。于是一条跑了几个月的策略，其 `RunHistory` 行在 return / Sharpe / trades 三列上永远显示 `-`——不是"很差"，是**答不出来**。

同时，"回测说 +12%、纸面跑出来 +7%"这个问题今天**无法被提问**：两边没有共同的可比性口径，也没有一个会说"这两组数不可比"的地方。这直接导致分析决策只能凭印象。

## What Changes

- 纸面运行在首次 tick 记录**期初权益**，在终态（STOPPED / ERROR / 完成）按权益曲线计算头条统计，写入 `run.stats`；复用既有 `extended_stats()`（计算面）与既有单标的轨统计字段，**不写第二套实现**。
- 每一次运行（回测与纸面，两种轨）在 `params` 内落一份**模拟画像** `simulation_profile`：成本模型（启用/关闭 + 参数）、整手规则、交收规则。缺席时如实写"未建模"，不写默认值假装存在。
- 新增一个比较读端点：同一策略的指定回测 run 与纸面 run 并排，返回双方统计 + `comparable: bool` + 不可比的**具体原因**（窗口不重叠、成本口径不同、模拟画像不同、数据指纹不同）。不可比时**不比较统计**，且该状态以可机读字段出现——不是省略、不是静默 0。
- 前端 `RunHistory` 的 return / Sharpe / trades 三列开始显示真实值；比较结果显示不可比原因。

## Capabilities

### New Capabilities

- `run-performance-comparison`: 跨运行比较的口径与判据——什么是"同一实验"、不可比时如何如实报出、比较结果的可机读形状。

### Modified Capabilities

- `strategy-live-execution`: 纸面运行 SHALL 在终态记录头条统计（不得只落权益点）；SHALL 为运行落模拟画像。
- `strategy-backtest`: 回测运行 SHALL 落同一份模拟画像（与纸面运行同名同形），使比较有共同口径。

## Impact

- 代码：`strategy/runner.py`（期初权益、终态统计、画像）、`api/routes/strategies.py`（比较端点、画像写入）、`api/schemas.py`（比较响应模型）、`web/src/components/RunHistory.tsx` 与比较视图。
- 数据：`strategy_runs.stats` 与 `params.simulation_profile` 开始有内容；**无 schema 迁移**（两列已存在）。
- 依赖：统计口径依赖 `FD_QUANT_COMPUTE_ENDPOINT`（既有），不可用时按既有纪律**如实失败**，不降级为本地实现。
- 不变：任何既有回测统计字段、任何成交/记账语义、任何订单路径。
