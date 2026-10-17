## Why

FinRL-X 全仓最值得抄的一条是**权重向量契约**：策略输出恒为"每个标的目标权重"的向量，回测与实盘吃同一个输出（`README.md:40-44` 的 `w_t = R(T(A(S(X≤t))))`）。我们的 fd-paper-trading 今天是**单标的**形态——`spec.symbol` 是单值（`strategy-management` spec）、回测与纸面实盘都按一个标的评估 `on_bar`。这意味着：

- 任何组合/轮动类策略（P2 自适应轮动、批 4.2 因子选股、批 4.4 组合风险）**无法表达**——它们天然是多标的权重决策；
- 批 0.2 已立 `TargetWeights` 契约但没有消费方——契约需要一个能"吃下权重向量并执行"的引擎；
- 我们已有 4,576 只 A 股与批量日线读取能力，多标的回测的数据侧没有障碍。

本变更是路线图批 3.1（P1 策略工坊）的引擎前置，也是 P2（批 3.2）的直接依赖。批 1.2（回测 v2）的 walk-forward/成本/基准能力与本模式正交，两者都完成后组合即得"多标的 + walk-forward + 成本 + 基准"的完整回测。

## What Changes

- **第三种策略 spec kind：`weight`**。spec 形态为 `{"kind": "weight", "universe": [<symbol>...], "rebalance": {"every_n_bars": <int>}, "source": <python>}`——自定义代码轨约束下的 `target_weights(ctx) -> dict[symbol, float]`（复用既有 AST allowlist + 子进程沙箱，不放宽任何沙箱限制）；`universe` 声明参与标的（上限按 group 配置，见 tasks）。
- **权重模式回测**：多标的日线回放；每个 rebalance 点调用 `target_weights`；按目标权重与当前市值差异生成调仓指令（按整数股取整，标注取整假设）；跨标的同时维护现金与持仓；输出组合级统计（复用批 1.2 的统计扩展与基准对比）+ 每期权重快照。
- **权重模式纸面实盘**：同一 `target_weights` 契约在 paper run 中执行——rebalance 点将目标权重转换为各标的的 MARKET 订单（经既有 `PaperExecutionService`，买入受现金约束、卖出受持仓约束），全部走既有归因、限额、账本路径。
- **契约消费**：模式接受 `TargetWeights` 契约实例作为可选输入路径（"直接给定权重序列"的静态模式：`{"kind": "weight", "universe": [...], "rebalance": {...}, "weights_series": {<date>: {<symbol>: <weight>}}}`），使契约实例可被直接回测——这是 pack 产出（P1 技能）与引擎之间的桥。
- **权重与现金约束**：目标权重和 ≤ 1（浮点容差），残差为现金；权重为负或和超 1 组成的 spec/序列 SHALL 被拒绝（不在引擎内静默归一化，与批 0.2 契约同纪律）。
- **不破坏既有两种 kind**：模板轨与代码轨行为不变；权重模式是新增 kind，旧 spec 继续按原语义执行验证。

## Capabilities

### New Capabilities

- `strategy-weight-mode`: 权重向量策略模式——spec 形态（universe/rebalance/source 或 weights_series）、`target_weights` 评估契约、多标的调仓回测（整数股取整、权重与现金约束、权重快照）、纸面实盘的同契约执行（信号→多标的订单）、与 `TargetWeights` 契约实例的直接消费路径。

### Modified Capabilities

- `strategy-management`: 策略 spec 的合法 kind 从两种扩为三种（新增 `weight`），校验规则随新形态定义（universe 非空且去重、rebalance 合法、source 或 weights_series 二者其一）。
- `strategy-backtest`: 回测入口支持权重模式（多标的回放、组合级统计、权重快照产出）。
- `strategy-live-execution`: 纸面实盘支持权重模式（rebalance 点信号转多标的订单、每 run 的标的数上限、归因与既有路径一致）。

## Impact

- **仓**：`fd-paper-trading`（`src/fd_paper_trading/strategy/` 新增 weights 评估器、多标的回测路径、runner 的权重分支；API 与 web 面板支持新 kind 的参数编辑）。
- **数据**：`strategy_runs` 复用（新增权重快照存储；`strategy_equity_points` 复用组合曲线）；策略 spec 无需迁移（新增 kind，旧行不变）。
- **契约依赖**：`TargetWeights`（批 0.2）——权重模式的静态路径直接接受契约实例；校验复用其不变式（拒绝而非归一化）。
- **沙箱复用**：`strategy-sandbox` 的 AST allowlist 与资源限制**原样复用**，`target_weights` 与 `on_bar` 同一沙箱；不放宽任何限制。
- **限额**：`universe` 标的数与每 run 的标的数受 group 配置约束（新增 `max_universe_size`，默认值在实施时定并写入 spec）；rebalance 频率与既有 `max_orders_per_day` 交互（一次调仓可能产生多笔订单）——本变更明确该交互规则。
- **不做**：不做做空与杠杆（权重非负）、不做盘中再平衡（日线）、不做多策略资金分配（一个 run 一个策略）、不做优化器（权重由 `target_weights` 或 weights_series 给出，引擎不"帮你想权重"）。
