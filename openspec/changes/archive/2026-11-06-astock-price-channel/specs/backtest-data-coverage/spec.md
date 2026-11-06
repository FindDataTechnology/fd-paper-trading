## ADDED Requirements

### Requirement: Coverage 报告携带复权口径

实现了 `get_bars_with_coverage` 的日线价格 provider SHALL 在覆盖度结果中报告其复权口径
（如 `adjusted: "forward"` 前复权 / `"none"` 不复权）；口径无法判定时 SHALL 显式标注
unknown。回测 run 的 `params` 数据指纹记录 SHALL 包含所用序列的复权口径，使"同一策略两
次回测基于不同复权基准"可被事后发现。

#### Scenario: 口径随指纹留痕

- **WHEN** 一次回测在支持口径声明的 provider 上完成
- **THEN** 其 `params` 中每个标的的记录包含该次取数的复权口径

#### Scenario: 混用不同口径的两次 run 可被区分

- **WHEN** 同一策略分别在前复权与不复权序列上回测
- **THEN** 两次 run 的记录显示不同口径值，结果差异可据此解释
