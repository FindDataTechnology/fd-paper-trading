## ADDED Requirements

### Requirement: 回测接受 run 级风控覆盖

回测请求 SHALL 接受可选 `risk` 配置（`stop_loss_pct` / `trailing_stop_pct` /
`cooldown_bars`），并由回测器逐 bar 执行与 live runner 同一覆盖逻辑：bar `low` 触达止损
位即触发、成交价取止损位与该 bar 开盘价的较不利者（跳空保守成交）、触发产出归因平仓
指令并记入回测结果事件。结果统计与既有统计扩展共用口径；`risk` 缺省时行为逐位不变。
回测结果 SHALL 标注风控配置与触发清单，使"带止损与不带止损两次回测"的差异可归因。

#### Scenario: 带止损回测触发可追溯

- **WHEN** 回测请求带 `stop_loss_pct=5` 且窗口内存在触发 bar
- **THEN** 结果事件列出触发 bar 日期与成交价，且期末持仓与逐笔记录一致

#### Scenario: 缺省零变化

- **WHEN** 回测请求不带 `risk`
- **THEN** 结果与既有行为逐位一致（成本默认关闭的既有回归同样成立）
