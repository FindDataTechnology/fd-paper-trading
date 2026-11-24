## ADDED Requirements

### Requirement: 交收规则生效后的卖出约束

卖出订单 SHALL 受持仓的**可卖数量**约束，而非持仓总量：数量不得超过可卖数量，超出时被拒绝并在错误中同时给出可卖数量与持仓总量。可卖数量由持仓批次与交收规则（`settlement-and-lots`）推出。成交记账 SHALL 应用成交成本（`trading-costs`）并记录费用明细，已实现盈亏 SHALL 按 FIFO 批次消耗计算。

持仓总量与平均成本 SHALL 继续对持仓视图（`positions` 端点）可见，但 MUST NOT 再作为卖出校验或已实现盈亏的唯一依据。

#### Scenario: 当日买入当日卖出被拒

- **WHEN** 用户在买入某标的的同一交易日提交卖出
- **THEN** 系统以 `insufficient_position` 拒绝，错误文本同时给出可卖数量 0 与持仓总量

#### Scenario: 成交扣费并记录明细

- **WHEN** 一笔卖出成交
- **THEN** `Fill` 行记录佣金与印花税，现金按其净额增加

#### Scenario: 止损对当日买入的仓位不可执行

- **WHEN** 当日买入的仓位在当日触达止损价
- **THEN** 触发被记录为交收不可执行，仓位不变，且不产生成交或平仓记录
