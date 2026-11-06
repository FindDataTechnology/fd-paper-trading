## Why

执行面只有 MARKET/LIMIT 两型：portfolio-risk pack 的止损纪律（绝对 -5% / 移动 -10%）在 6.5 实测确认"止损不会自动执行"——风控只能靠策略评估器内自写退出逻辑，账户层没有止损语义；weight 模式只消费权重序列，风控字段在 fd-quant 契约里也不存在。这一版要成为可信产品，风控必须进执行语义而不是留在文档里。

同时接 A 股后（批 6 ① `astock-price-channel`）出现一个口径事实要显式化：前复权序列"锚定最新价、调整历史价"，live 每次取窗都按当下口径重取——必须把"单一价格世界 + 口径留痕"的既有原则在 spec 层写实，防止混口径被误读为盈亏。

## What Changes

- **新增 STOP / STOP_LIMIT 订单型**：账户层可下止损单（卖出止损触发价 ≤ 触发价成交；STOP_LIMIT 触发后转限价规则）；触发评估在每次新报价到达时进行，撮合计算器保持无状态；日线下报价由最新收盘推导（触发语义如实按日线粒度，不做盘中假象）。
- **run 级风控覆盖（回测与 live 共用）**：策略 run 启动可带可选 `risk` 配置（`stop_loss_pct` / `trailing_stop_pct` / `cooldown_bars`）；回测器与 live runner 消费同一覆盖逻辑——回测逐 bar 触发（bar low 触发、跳空按不利价成交），live 逐 tick 对报价触发；触发即下归因市价平仓单，计入既有日单量帽与事件账本。**不动 fd-quant 契约**（TargetWeights 不加字段，风控是 run 配置层）。
- **weight 模式冷却语义**：止损触发的标的在 `cooldown_bars` 个调仓点内目标权重按 0 处理（防"止损当根卖、下个调仓点原样买回"），冷却结束自动恢复目标权重。
- **口径留痕**：live run 记录其取数 provider 的复权口径；两次取窗之间指纹变化（除权再调整）记为 run 事件——混口径可见而非隐形。
- **命名澄清**：既有 "Stop semantics"（停跑）与本变更的止损单是两回事，spec 内措辞互不混淆。

## Capabilities

### New Capabilities

（无——全部落在既有能力的延伸上）

### Modified Capabilities

- `order-execution`：无状态撮合计算器的订单型扩展（STOP/STOP_LIMIT 触发与成交语义）；新增止损单 resting/触发/取消生命周期需求。
- `strategy-live-execution`：新增 run 级风控覆盖需求（回测与 live 同一覆盖逻辑、触发→归因平仓单、日单帽、事件）；weight 模式冷却场景；live run 复权口径留痕需求。
- `strategy-backtest`：回测请求接受可选 `risk` 覆盖并逐 bar 执行（与 live 同一语义），结果标注触发事件——保证"回测与实盘共用同一评估契约"在风控维度的延续。

## Impact

- **仓**：`fd-paper-trading`（`execution/fill_engine.py` 订单型扩展、订单生命周期/仓储、`strategy/runner.py` 与 `backtester.py` 的覆盖逻辑、run 启动 API、web 下单面板订单型、MCP 工具面参数）。
- **依赖**：`astock-price-channel`（口径留痕消费其 provider 声明；跳空语义在 A 股数据上验收）。不依赖其完成度时可并行开发（模拟 provider 即可验收核心语义）。
- **向后兼容**：新订单型与 `risk` 配置全部可选；不下止损、不配风控的行为逐位不变。
- **不做**：不做盘中逐笔触发（日线粒度如实声明）；不做条件单组合（如 OCO）；不做做空方向的止损（持仓方向假设不变）；不改 fd-quant 契约与既有 group 限额数值。
