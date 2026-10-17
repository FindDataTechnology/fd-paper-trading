## Context

动机见 `proposal.md`。现状与约束（读自本仓）：

- **单标的是当前的全部形态**：`strategy-management` 的 spec 只有 `template`/`code` 两种 kind，`symbol` 是单值；`strategy-backtest` 按 `strategy's symbol` 回放；`strategy-live-execution` 的 tick 循环检查"the strategy's symbol"的新 bar。权重模式要在**三个 spec 上同时开口**（本变更的 delta 已覆盖），且每一处都要保持旧路径不变。
- **可复用的现成件**：①`strategy/indicators.py` 的指标库（`target_weights` 里可用）；②`strategy/codegen.py` 的 AST allowlist 与 `sandbox_worker.py` 的子进程沙箱（原样复用，入口函数名从 `on_bar` 换成 `target_weights`）；③`PaperExecutionService`（多标的订单逐笔走既有路径，无需新撮合逻辑）；④`strategy_equity_points`（存组合曲线）；⑤`marketdata` 的批量日线读取（`get_bars`/`read_range`，已有 `_MAX_WINDOW_DAYS=730` 约束）。
- **契约已在案**：`TargetWeights`（批 0.2）的不变式（非负、和 ≤ 1、cash_floor 合计 ≤ 1、universe 包含、拒绝而非归一化）——本变更的权重校验**直接对齐**，避免引擎与契约两套口径（引擎拒绝的规则 = 契约拒绝的规则）。
- **group 配置**：`DEFAULT_GROUP_CONFIG` 有 `max_active_strategies`/`max_backtest_days`/`max_orders_per_day`/`allowed_symbols`；权重模式需要新增 `max_universe_size`，并明确"一次调仓的多笔订单计入 `max_orders_per_day`"（该交互在 spec 里已钉）。
- **取整是必须显式化的假设**：整数股 + 向下取整 + 余量留现金。上游 FinRL-X 用 `integer_share_allocator` 并记录了 `bt` 在大额下二分失败的问题——我们自研取整逻辑简单，但要写清假设（与既有"limit 不跨 bar 结转"同款文档化纪律）。
- **数据对齐**：多标的回放需要日期对齐；A 股各标的交易日基本一致但有停牌差异，因此"缺 bar 按最近可用收盘估值 + 标注缺口"是必要规则（已在 spec）。

## Goals / Non-Goals

**Goals:**
- 第三种 kind 与既有两种并存、互不影响；组合/轮动策略可表达、可回测、可纸面实盘。
- 引擎的权重口径与 `TargetWeights` 契约**同一套**（引用契约层校验，不重写）。
- 取整、缺 bar、缩量三个"看起来是细节但会让结果不可解释"的点全部显式化并产出可查证据。

**Non-Goals:**
- 不做权重优化（不帮用户算权重；权重来自策略代码或静态序列）。
- 不做做空、杠杆、期货、期权（权重非负且和 ≤ 1）。
- 不做盘中再平衡（日线频率）。
- 不做跨策略资金分配（一个 run 一个策略一个资金账户）。
- 不改 `PaperExecutionService` 的撮合、限额、账本逻辑（全部复用）。

## Decisions

### D1. 第三种 kind = `weight`，而不是"给现有 kind 加 universe 字段"

**选择**：新增独立 kind，形态自洽（universe + rebalance + source 或 weights_series）。
**理由**：单标的与多标的的评估契约根本不同（`on_bar` 返回信号序列 vs `target_weights` 返回映射），混在一个 kind 里会让校验与执行路径充满条件分支；独立 kind 让"哪种模式走哪条路径"在 spec 层就清晰。同时旧 spec 零改动（向后兼容由"新增枚举值"天然保证）。
**备选**：`kind: "code"` + 可选 `universe` 字段（同一函数两种返回约定，歧义在运行时才暴露）。否。

### D2. 两种来源形式（source / weights_series）统一为"按期产权重"

**选择**：代码轨按 rebalance 点调 `target_weights`；静态轨按 `weights_series` 的期取权重（缺期的 rebalance 点沿用上一期，首期之前空仓）。
**理由**：静态轨是 `TargetWeights` 契约实例的直接消费路径（P1 pack 产出契约 → 引擎回测，这是"技能产出可被验证"的闭环），必须有；把两者统一为"提供 权重(date) 的函数"后，回测/实盘的核心循环只有一份实现。契约实例（单 as_of）作为"只有一期"的退化情形自然支持。
**备选**：只支持代码轨（pack 产物进不来）；只支持静态轨（无法表达动态策略）。否。

### D3. 取整：向下取整到整数股，余量留现金，缩量按比例

**选择**：每笔调仓 `qty = floor(|Δ市值| / 价格)`；买入受现金约束时按可买量缩量（同样向下取整）；结果记录每次缩量。
**理由**：A 股整数股是现实约束（一手=100 股在多数标的，但记 1 股粒度更通用，收口在"整数股"这一层）；向下取整保证现金约束不可能被违反（向上取整会引入负现金）。缩量必须记录，否则"目标 0.5 实际 0.47"的差异无法解释。**不做**"按手（100 股）取整"——那会引入另一个可配假设，且与 paper trading 的现有限制（未规定手数）不一致；留待 group 级配置扩展。
**备选**：按金额近似不取整（回测结果不可执行，与纸面实盘必然分叉）；向上取整（负现金风险）。否。

### D4. 缺 bar：估值按最近可用收盘，交易按"该期不调仓该标的"

**选择**：估值用最近可用收盘价；若某标的在 rebalance 点当天无 bar，则该期对该标的**不产生调仓指令**（保持原持仓），并在结果标注。
**理由**：停牌标的不可交易——生成指令会让回测看起来能成交（不可执行）。估值与交易分离（估用最近价、交易跳过）是行业标准处理，也是唯一诚实的选择。缺口必须在结果可见（可解释性）。
**备选**：前向填充并照常交易（伪造可成交性）；跳过整个 rebalance 期（过度保守，其他标的的调仓也被耽误）。否。

### D5. 引擎引用契约校验，不重写权重规则

**选择**：`weights_series` 与 `target_weights` 返回值的合法性校验调用批 0.2 的契约 `validate()`（权重非负、和 ≤ 1、symbol ⊆ universe）；引擎不实现第二套规则。
**理由**：批 0.2 的设计 D4 就是"拒绝而非归一化"，两处规则若各写一份必然漂移（这正是路线图"两个契约"要消除的问题）。依赖方向：fd-paper-trading → fd-quant 契约（Python 包依赖，已有 fd-open-data-mcp 作 Python 依赖的先例）。
**备选**：引擎内复制校验（漂移风险）；不校验（非法权重进入执行）。否。
**风险与缓解**：新增跨仓 Python 依赖——若批 0.2 未就绪，实现期以"等价校验 + TODO 标注"垫底并在 tasks 中把"切换为契约校验"设为显式交付项（对拍测试钉住等价性）。

### D6. 纸面实盘的调仓 = 遍历目标标的逐笔下单，不加批量接口

**选择**：一次调仓产生 N 笔单标的 MARKET 订单，逐笔经 `PaperExecutionService.place_order`（或既有批量路径）。
**理由**：复用既有幂等/限额/账本的全部语义，零新风险；N 受 `max_universe_size` 约束（默认上限让 N 可控）。多标的原子性不做（部分成交是现实，逐笔归因反而更清晰）。
**备选**：新增组合级原子下单接口（引入新事务语义，与既有订单模型冲突）。否。

### D7. run 记录 universe 与权重快照，钉住版本语义

**选择**：`strategy_runs` 记录 `universe`（或复用 spec 快照字段）与每期权重快照、指令明细（含缩量标记）；编辑策略不影响已记录 run（与既有"run pins version"纪律一致）。
**理由**：与既有 `strategy_version` 钉住语义一致；权重快照是"引擎实际做了什么"的唯一证据（目标 ≠ 实际，取整与缩量都会偏离），也是 P5 复盘与账本对接的原料。
**备选**：只存目标权重（丢失实际执行的真相）。否。

## Risks / Trade-offs

- **[多标的回放的数据量]** → universe 上限（group 配置）+ 既有 `max_backtest_days` 双层约束；批量读取复用 `read_range`。
- **[跨仓 Python 依赖（批 0.2）未就绪]** → 等价校验垫底 + 对拍测试 + 显式切换任务（D5 的风险缓解）。
- **[沙箱内的多标的上下文（`ctx.closes(symbol)`）增加沙箱接口面]** → 只读访问、无新副作用；沙箱既有限制（无 import、无下划线、无 eval）不变；接口在 tasks 中用沙箱内测试覆盖。
- **[性能：一次调仓 N 笔订单在纸面实盘]** → N 受上限约束；订单走既有异步路径；日订单上限防风暴（spec 已钉）。
- **[取整残差累积导致长期偏离目标权重]** → 每次调仓按"目标 vs 当前实际"重算差值（残差自然被下次调仓纠正）；结果快照暴露偏离程度，不静默。
- **[单标的与多标的在 web 面板的编辑体验差异]** → 面板按 kind 渲染（spec delta 已钉：universe 编辑器 + rebalance 输入 + 双来源编辑器）。

## Migration Plan

1. 契约依赖引入（或等价校验垫底）+ `TargetWeights` 消费路径的校验对拍。
2. spec kind 扩展（校验 + 序列化 + API）→ 旧 kind 回归测试。
3. 多标的回测路径（对齐、估值、取整、缩量、快照）→ 与单标的路径并存的回归。
4. 纸面实盘权重分支（rebalance 判定、订单转换、上限交互）→ 复用执行服务。
5. web 面板按 kind 渲染。
6. group 配置新增 `max_universe_size`（迁移）。
7. 回滚：新增 kind 可禁（校验层拒绝 `weight` 即回退）；数据无迁移（新字段可空）。

## Open Questions

- `max_universe_size` 的默认值（实施时按 A 股主流组合规模与订单上限的交互定，如 20/50 两档）——不改 spec 语义（spec 只要求"受 group 配置约束并拒绝超限"）。
- 手数（100 股）粒度是否作为 group 级可配——当前不做（D3），若 A 股实盘模拟需要再扩。
- 权重模式是否需要"再平衡阈值"（偏离小于 X% 不调仓，减少换手）——当前无，rebalance 频率即纪律；若 P2 自适应轮动需要，作为该 pack 侧的策略逻辑（不是引擎参数）。
