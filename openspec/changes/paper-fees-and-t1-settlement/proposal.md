# Proposal

## Why

模拟交易今天对成本与交收**完全不建模**：`execution/service.py::_apply_fill` 只做 `cash -= qty*price`，没有佣金、没有印花税、没有滑点；`position.quantity` 既是持仓量也是可卖量，于是当天买入当天卖出（A 股 T+1 明令禁止）在纸面账户上是合法操作。后果有两个，都在生产里可验证：

1. **回测与纸面天然不可比**：回测可开成本模型（佣金 0.025% / 印花税 0.05% / 滑点 5bps），纸面永远零成本。同一策略两处跑出的数字差异里，有相当一部分只是费用的差。
2. **组合（权重）轨根本不能开成本**：`api/routes/strategies.py:424` 至今对 `cost_model.enabled` 直接抛错——而组合轨正是套件驱动的那一轨。

`daily-pnl-tracking` 是照着"真实成交"写的，零成本 + 无 T+1 让它算出的盈亏偏乐观。

## What Changes

- 新增一处**成交成本模型**，由纸面记账与回测两轨（单标的 + 组合）共用：佣金（买卖双边）、印花税（仅卖出）、滑点（买卖双边）。纸面记账在 `_apply_fill` 侧应用，落的 `Fill` 行 SHALL 记录费用明细。
- 组合轨的"成本不支持"硬拒绝改为走同一成本模型。
- **T+1 交收**：卖出受**可卖数量**约束而非持仓总量。引入**持仓批次（lots）**：每笔买入生成一个带开仓日与单位成本的批次，卖出按 FIFO 消耗批次，已实现盈亏按批次成本计算——即 `realized_pnl` 的算法从"平均成本"改为"批次成本"，使"结算规则"与"盈亏口径"对**同一批股**成立。
- 持仓新增可卖数量语义：当日买入的批次当日不可卖，次日开盘后可卖。
- 顺带修正两处必须跟随的语义：`reset` 的持仓摊平也要清空批次；止损在 T+1 下**不会**对当天买入的仓位触发（如实拒绝并留痕，而不是静默跳过）。
- **BREAKING**：`realized_pnl` 的口径由平均成本改为 FIFO 批次；既有成交与持仓**不回填**，保持原值。

## Capabilities

### New Capabilities

- `trading-costs`: 成交成本的构成、方向性（印花税仅卖出）、在哪些执行路径生效、以及费用在账本上的落点。
- `settlement-and-lots`: 持仓批次、可卖数量与交收规则——什么可以被卖、FIFO 消耗规则、已实现盈亏如何由批次得出。

### Modified Capabilities

- `order-execution`: 成交记账 SHALL 扣费用、SHALL 按批次计算已实现盈亏、卖出校验由"持仓总量"改为"可卖数量"。
- `strategy-backtest`: 组合（权重）轨 SHALL 支持成本模型（替换当前硬拒绝）；单标的轨成本语义不变。
- `paper-accounts`: 账户重置 SHALL 同时清空持仓批次与可卖状态。

## Impact

- 代码：`execution/service.py`（记账、卖出校验、止损触发）、新增 `execution/costs.py` 与 `persistence/models.py::PositionLot`、`alembic/` 新增迁移（`fills` 费用列 + `position_lots` 表）、`strategy/weight_backtester.py`（成本）、`strategy/costs.py` 与新共用模块合并、`accounts/service.py`（reset）。
- 数据：新表 + `fills` 新增可空列。既有行保持 NULL（本列上线前 = 未记录费用），不写 0。
- 契约/下游：`daily-pnl-tracking` 的已实现分量随口径变化；`FD_QUANT` 无影响。
- 受限：现金为负不可达（既有购买力检查需把费用算进去，否则费用会让现金穿底）。
- 明确不做：涨跌停、最小价格变动之外的整手、日内 T+0 品种、融券/做空、部分成交队列。
