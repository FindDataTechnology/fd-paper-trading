## Purpose

为策略引擎增加"权重向量"模式：策略以 `target_weights`（多标的到目标权重的映射）表达仓位意图，回测与纸面实盘消费同一输出——使组合、轮动、因子类多标的策略（P2 自适应轮动、因子选股、组合风险）能够被表达、回测与在模拟盘运行，并让 `TargetWeights` 契约实例可直接进入引擎。

## ADDED Requirements

### Requirement: 权重模式 spec 形态与校验

策略 spec SHALL 支持第三种 kind `weight`，形态为 `{"kind": "weight", "universe": [<symbol>...]}` 加上二者之一：`"source": <python>`（自定义代码轨，须定义 `target_weights(ctx)`）或 `"weights_series": {<ISO 日期>: {<symbol>: <数值>}}`（静态权重序列）；并 SHALL 含 `"rebalance": {"every_n_bars": <正整数>}`。校验 SHALL 拒绝：`universe` 为空或含重复项、`every_n_bars` 非正整数、`source` 与 `weights_series` 同时给出或同时缺失、`weights_series` 中任一期权的权重超出序列声明的 universe、任一期权重为负或和超过 1（浮点容差 1e-9）。校验 SHALL NOT 自动归一化或裁剪非法权重。

#### Scenario: 合法权重 spec 通过
- **WHEN** 创建 `{"kind":"weight","universe":["600519","000001"],"rebalance":{"every_n_bars":5},"source":"def target_weights(ctx): return {\"600519\":0.5,\"000001\":0.3}"}`
- **THEN** 创建成功、version = 1

#### Scenario: universe 为空被拒
- **WHEN** 创建 `universe` 为空的权重 spec
- **THEN** 响应 `400` 并指明 `universe` 违规

#### Scenario: 两种来源同时给出被拒
- **WHEN** spec 同时含 `source` 与 `weights_series`
- **THEN** 响应 `400` 指明必须二选一

#### Scenario: 非法权重被拒而非归一化
- **WHEN** `weights_series` 某一期权重的和为 1.2
- **THEN** 响应 `400` 指明该日期与权重和；spec 不被接受，权重不被自动缩放

### Requirement: target_weights 评估契约

代码轨权重策略 SHALL 在既有沙箱（`strategy-sandbox` 的 AST allowlist 与资源限制，不得放宽）中执行，入口函数 SHALL 为顶层 `target_weights(ctx)`，返回 `{symbol: weight}` 映射。`ctx` SHALL 提供既有 `on_bar` 上下文能力（历史序列访问、指标计算）并按 universe 逐标的提供序列访问（如 `ctx.closes(symbol)`），SHALL 提供 `ctx.universe`、`ctx.date`、`ctx.cash` 与当前持仓权重 `ctx.current_weights()`。返回值 SHALL 只用 universe 内的 symbol 为键；出现 universe 外键、负权重或权重和超 1 时该期评估 SHALL 失败并记录原因，SHALL NOT 静默丢弃或修正。

#### Scenario: 返回映射被采纳
- **WHEN** `target_weights` 返回 `{"600519":0.4,"000001":0.4}`
- **THEN** 该 rebalance 期目标现金为 0.2，引擎按两标的权重差生成调仓指令

#### Scenario: universe 外键导致该期失败
- **WHEN** 返回映射含 `{"300750":0.1}` 而 universe 只有 `["600519","000001"]`
- **THEN** 该期评估失败、原因记录在运行事件中，该期不产生任何订单

#### Scenario: 沙箱限制不放宽
- **WHEN** 权重策略源码含 `import` 或下划线开头标识符
- **THEN** 与代码轨单标的策略相同地被沙箱拒绝，错误指明违规点

### Requirement: 多标的调仓回测

回测 SHALL 支持权重模式：按 universe 的回放日期轴（各标的日线按日期对齐，缺 bar 的标的按最近可用收盘价估值并在结果中标注缺口）执行；每个 rebalance 点调用评估（代码轨）或读取当期权重（静态轨）；按目标权重与当前市值差生成调仓指令，指令 SHALL 以整数股取整（标注取整假设：向下取整至整数股，余量留现金）；买入受现金约束、卖出受持仓约束，超限按比例缩量并在结果中记录。回测 SHALL 输出：组合级统计（既有 6 项 + 批 1.2 扩展项，若 1.2 已就绪）、每期权重快照（日期→实际权重）、每次调仓的指令明细（标的/方向/股数/原因）。

#### Scenario: 调仓产生多标的指令
- **WHEN** 初始空仓、目标 `{A:0.5, B:0.5}`、现金 100000
- **THEN** 生成两笔买入指令，数量为按各自收盘价向下取整的整数股，记账后权重接近目标（取整残差留现金）

#### Scenario: 卖出受持仓约束
- **WHEN** 目标权重将某标的从 0.6 降到 0.2 而持仓不足以覆盖差值（含取整）
- **THEN** 实际卖出量受持仓约束，结果记录该次缩量

#### Scenario: 权重快照按期产出
- **WHEN** 回测完成
- **THEN** 结果含每个 rebalance 期的实际权重快照，日期与权重可查

#### Scenario: 缺 bar 标的按最近收盘估值并标注
- **WHEN** 某标的在回放期某日无 bar
- **THEN** 估值使用其最近可用收盘价，结果中标注该缺口日期与标的

### Requirement: 权重模式纸面实盘

纸面实盘 SHALL 支持权重模式：rebalance 点（策略自身与新 bar 判定）到达时，将目标权重与账户当前持仓的差异转换为各标的的 MARKET 订单，经既有 `PaperExecutionService` 执行（买入受现金与购买力约束、卖出受持仓约束），全部订单携带该 run 的 `strategy_run_id` 归因，并受既有速率限制与账本路径管辖。一次调仓产生的多笔订单 SHALL 计入运行的日订单上限（`max_orders_per_day` 语义扩展为订单笔数）；超限的调仓指令 SHALL 被跳过并记录。启动权重模式 run 时 universe 规模 SHALL 受 group 配置上限约束（超限拒绝并指明上限）。

#### Scenario: 调仓转换为多标的订单
- **WHEN** 运行中的权重策略在 rebalance 点给出 `{A:0.5,B:0.5}` 而当前空仓
- **THEN** 生成两笔带 run 归因的 MARKET 买单，经执行服务后账户持仓与目标一致（取整残差留现金）

#### Scenario: 订单上限对调仓生效
- **WHEN** 一次调仓需要的订单笔数将超过当日剩余上限
- **THEN** 超出的指令被跳过并记录事件，已发出的部分照常执行

#### Scenario: universe 超上限拒绝启动
- **WHEN** 以超过 group `max_universe_size` 的 universe 启动纸面实盘
- **THEN** 响应 `400`/`409` 指明上限与实际规模

### Requirement: TargetWeights 契约实例直接消费

静态权重路径 SHALL 接受 `TargetWeights` 契约实例（批 0.2）作为 `weights_series` 的输入形态：`as_of` 对应期日期、`weights` 对应映射、`cash_floor` 对应目标现金下限；契约实例经契约层校验（拒绝而非归一化）后进入回测与纸面实盘。契约中的 `cash_floor` 与权重和冲突（和超 1 容差外）SHALL 被拒绝。

#### Scenario: 契约实例直接回测
- **WHEN** 以一份合法的 `TargetWeights` 实例（as_of 与 weights 齐备）作为静态权重序列回测
- **THEN** 回测按该权重执行并产出权重快照

#### Scenario: 契约违规被拒
- **WHEN** 契约实例权重和超 1（容差外）
- **THEN** 请求被拒且错误来自契约层校验；引擎不自行归一化

### Requirement: 组合统计与单标的路径不回归

权重模式的回测统计 SHALL 为组合级（组合权益曲线驱动）；既有单标的模板/代码轨的行为与输出 SHALL NOT 因本能力发生改变（统计字段、订单语义、纸面实盘路径不变）。

#### Scenario: 单标的策略行为不变
- **WHEN** 对一个模板轨策略跑既有回测
- **THEN** 行为与统计字段与升级前一致

#### Scenario: 权重模式统计为组合级
- **WHEN** 权重模式回测完成
- **THEN** 统计基于组合权益曲线（现金 + 各标的市值合计），非单标的
