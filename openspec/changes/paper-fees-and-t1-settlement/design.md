# Design

## Context

- `execution/service.py::_apply_fill` 是**唯一**记账入口（下单与止损触发共用），现金只有 `cash_delta = -signed_qty * price`。
- 已实现盈亏在 `_update_position` 里用 `position.avg_price` 算——平均成本口径。
- 卖出约束只有一处：`if qty > held: raise OrderRejected("insufficient_position", ...)`。
- 成本算术已存在且已测：`strategy/costs.py::apply_costs`（滑点调价、佣金/印花税各自算出、返回 `cash_delta`）。缺的是把它接进记账，并让组合轨用上。
- `strategy_runs` 已有画像位（change A）。
- 既有测试大量断言当前语义：`tests/test_money_path_invariants.py`、`tests/test_daily_pnl.py`、`tests/e2e/test_strategy_paper_runs.py`（51KB）。

## Goals / Non-Goals

**Goals:**
- 纸面现金变动 = 真实 A 股费结构；逐笔费用可求和。
- 卖出受可卖数量约束；已实现盈亏与"卖了哪批股"一致（FIFO）。
- 回测两条轨与纸面共用一份费用实现。

**Non-Goals:**
- 不做涨跌停/最小变动价之外的撮合细节；不做部分成交队列；不做融券/做空。
- 不回填历史成交与持仓（已定：forward-only）。
- 不引入交易时段/交易日历服务：交收日界沿用既有 `pnl_day_start_utc_hour` 的日界概念。

## Decisions

**D1：费用模型放进 `execution/costs.py`，`strategy/costs.py` 保留为回测侧薄适配。**
纸面记账需要 `FillCost`（含 `effective_price`），回测需要同一算术。把 `apply_costs` 与 `CostModel` 移到 `execution/costs.py`，`strategy/costs.py` 改为 re-export 以免动回测的 import 面。**不新建第二套算术**——这是本仓的既有纪律（`risk_overlay.py` 一模块两消费者）。

**D2：`fills` 增三列 `commission` / `stamp_tax` / `slippage_cost`，均可空。**
NULL = 本列上线前的成交（未记录），0 = 已建模为零成本。与既有 `realized_pnl` 同一处置方式，理由相同：把历史行填 0 等于声称"这笔零费用"。

**D3：新增 `position_lots` 表：`(account_id, instrument_id, symbol, opened_on(date), quantity, remaining, unit_cost)`。**
`remaining` 而不是从成交重算，是因为重置会清批次、而成交行不动——重算会造出已清空却仍可卖的幽灵持仓。`unit_cost` 含买入费用（摊销到每股），使已实现盈亏是**净**盈亏。索引 `(account_id, instrument_id, opened_on)`。

**D4：可卖数量是推导值，不落列。**
`available(account, instrument, today) = Σ remaining WHERE opened_on < today`。存成列就要在每次日界翻点重算全表，且会与批次漂移。推导的成本是一次带索引的聚合，发生在下单路径——可接受。

**D5：历史持仓视为可卖。**
既有持仓在 `position_lots` 里没有行。若严格要求"有批次才可卖"，所有现存账户立刻不可交易——那是把迁移成本转嫁给用户。规则：**当某持仓无任何批次时，其可卖数量等于持仓总量**；一旦该标的产生新批次，改按批次推导。这条必须在 spec 与代码里都写明，否则是隐性不一致。

**D6：卖出跨批次时按 FIFO 消耗，且 `realized_pnl` 改为批次口径。**
`_update_position` 的算术保留（它维护 `quantity`/`avg_price` 供持仓视图），但**已实现盈亏改由批次消耗算出**并写入 `Fill.realized_pnl`。`Position.realized_pnl` 累加同一数值，语义仍为终身累计。

**D7：购买力必须含费。**
`_check_buying_power` 的 `ref_price` 改为"含费单价"：`price*(1+slip)*(1+commission)`（买侧）。否则现金会在成交瞬间穿底——那是一个纸面不该有的状态。

**D8：止损对当日买入的仓位：记录、不执行、不误导。**
`evaluate_open_stops` 在卖出前查可卖数量为 0 → 写一条"交收不可执行"的触发记录（`triggered_at` **不**置位，因为置位会让下次评估跳过触发判定——这正是 2026-11-14 那个 bug 的同族陷阱）。仓位不变。

**D9：回测轨的 T+1。**
单标的轨的 `_Portfolio` 与权重轨的 `_WeightPortfolio` 都是内存仓位，没有批次。最小改动：**当日买入的股数记为 `bought_today`，卖出上限 = 持仓 − bought_today**，每根 bar 结束时把 `bought_today` 归零。这与日线回放的语义一致（每根 bar 就是一个交易日），且不需要在回测里造批次表。

**D10：组合轨成本接入点。**
`_rebalance` 里卖出与买入的 `apply_fill` 改为走 `apply_costs`，与 `backtester._apply_signal` 同源；`strategies.py:424` 的硬拒绝删除。

## Risks / Trade-offs

- [FIFO 改口径会让 `daily-pnl-tracking` 的已实现分量变化] → 这正是本 change 的目的之一（零成本口径让日盈亏乐观）；但历史 `Fill.realized_pnl` 不回填，所以一条跨越上线日的窗口会混口径。缓解：`daily-pnl` 端点已有 `unattributed_fills`（NULL 计数）语义，新增费用列同样以 NULL 表示未记录，混口径在数据上可见。
- [既有 51KB 的 paper-run 测试大量断言平均成本] → 逐条改，不整体重写；凡断言"cash = qty*price"的改为含费；凡断言"当日买卖可行"的改为断言被拒。工作量是本 change 最大的一块。
- [批次表与 `positions` 可能漂移] → 单一写入点（`_apply_fill`）同时写两者；加一条不变量测试：`Σ remaining == position.quantity`（历史无批次者豁免）。
- [重置后批次清空但成交行仍在] → `realized_pnl` 已落在成交行上，重置不重算历史；重置本身记一条 `reset` 交易，语义既有。
- [性能：每次卖出一次批次聚合] → 索引覆盖；单账户持仓量级极小。

## Migration Plan

1. 迁移：建 `position_lots`；`fills` 加三列（可空，无默认）。
2. 部署后新成交写费用与批次；历史保持 NULL/无批次（D5 的可卖规则）。
3. 回滚：新列与新表对旧代码不可见（旧代码不 select 它们）；回退镜像安全。**注意**：回滚后新产生的批次行会残留，重新上线时会被当作真实批次——若需彻底回滚，删表数据。
4. 验收顺序：单元（费用算术/批次 FIFO）→ e2e（当日买卖被拒、次日可卖、含费购买力）→ 生产冒烟（一条真实策略，记录含费 vs 零成本的差异）。

## Open Questions

- 印花税在未来若改为单边征收或调整税率，是否需要按生效日分档？（当前只存单一费率；分档属后续能力，不阻塞。）
- 组合轨回测的内存仓位尚未按 bar 记 `bought_today`（T+1 只在单标的轨与纸面账本强制）。组合调仓点通常不在同一 bar 上又买又卖同一标的，故当前影响可忽略；若将来出现同 bar 反向调仓的需求，组合轨需补同一约束。
