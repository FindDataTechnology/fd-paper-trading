## ADDED Requirements

### Requirement: 纸面运行记录期初权益与终态头条统计

纸面运行 SHALL 在处理第一根新 bar 时记录**期初权益**（现金 + 持仓按最新收盘计价）并随运行留痕。运行进入终态（`STOPPED` 或 `ERROR`，或在被外部停止后完成收尾）时，系统 SHALL 由该运行的权益点序列计算头条统计并写入 `stats`：至少 `total_return`（以**期初权益**为基准，而非账户开户余额）、`max_drawdown`、`sharpe`、`trade_count`，以及计算面可得的年化量。统计 SHALL 复用回测轨的同一套计算（同名字段、同一口径），MUST NOT 另起一套纸面专用实现。

权益点少于两个时，统计 SHALL 记为不可计算并给出原因，MUST NOT 伪造为 0 或沿用上一个值。

#### Scenario: 停止后运行有统计而不是空

- **WHEN** 一条纸面运行处理了 30 个交易日的 bar 后被停止
- **THEN** 该运行的 `stats` 含 `total_return`/`max_drawdown`/`sharpe`/`trade_count`
- **AND THEN** `total_return` 以期初权益为基准，与期初现金相同则收益为 0

#### Scenario: 期初权益不是账户开户余额

- **WHEN** 账户开户余额为 100,000，运行开始时持仓市值使权益为 87,000
- **THEN** 期初权益记为 87,000，`total_return` 以 87,000 为基准

#### Scenario: 权益点不足时如实报缺

- **WHEN** 一条运行只记录了一个权益点就被停止
- **THEN** `stats` 中不可计算的字段为 null，并带原因说明需要至少两个点

#### Scenario: 统计不可用时不静默降级

- **WHEN** 计算面不可用（`FD_QUANT_COMPUTE_ENDPOINT` 不可达）
- **THEN** 年化类统计记为不可计算并写明原因，基线统计（收益/回撤/笔数）仍照常得出

## MODIFIED Requirements

### Requirement: Paper-run equity snapshots and events

Each paper run SHALL persist a daily equity point (cash + positions marked at the latest close) after the first tick that processes a new bar, and SHALL persist an opening-equity record for the run's first processed bar so a return can be measured against the run's own start rather than the account's opening balance. Strategy events (run state changes, orders placed, rejections, errors) SHALL be recorded on the run and broadcast over the existing `/ws` connection manager to the owning user so the web UI updates live.

#### Scenario: Daily snapshot after evaluation
- **WHEN** a run processes a new daily bar
- **THEN** an equity point for that date is persisted

#### Scenario: Event broadcast on order
- **WHEN** a run places an order
- **THEN** a strategy event is broadcast to the owning user's WebSocket connections

#### Scenario: Opening equity is measurable
- **WHEN** a run starts with existing positions on its bound account
- **THEN** the first processed bar's equity point is the run's baseline for return, distinct from the account's opening cash balance
