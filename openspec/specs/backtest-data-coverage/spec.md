# backtest-data-coverage Specification

## Purpose

约束回测取数的覆盖度事实：provider 以可选能力报告 missing / market_closed / fill_failed / fingerprint（模拟源不得冒充已检查），部分覆盖 FAILED 要点名标的与区间，休市放行而探明失败按缺失处理，权重轨逐标的检查，run 记录钉住指纹、覆盖标记与复权口径。

## Requirements

### Requirement: Provider 暴露覆盖度的可选能力

数据源 MAY 实现可选方法 `get_bars_with_coverage(symbol, start, end)`，返回该窗口的 bar 序列**与该窗口的覆盖度事实**：`missing`（未覆盖且未证明无数据的区间）、`market_closed`（经询问确认为无数据的区间）、`fill_failed`（询问是否失败）、`fingerprint`（内容哈希）。

未实现该方法的 provider SHALL 被门禁视为"未能检查"，SHALL NOT 被当作"完整"或"不完整"。

系统 SHALL NOT 在任何 provider 上伪造覆盖度结论：模拟/合成数据源 SHALL 保持不实现该能力，其"序列即全部"的假设必须显式地不被当作真实数据的完整性证明。

#### Scenario: 真实数据源报告覆盖度
- **WHEN** 通过实现了该能力的 provider 读取一个 730 天窗口
- **THEN** 返回的覆盖度中 `missing` 与 `market_closed` 被分开报告，且 `fingerprint` 可用于事后比对

#### Scenario: 模拟 provider 不被当作完整
- **WHEN** 回测使用未实现该能力的 provider
- **THEN** 门禁不做出完整性判断，且 run 记录标注 `coverage_checked=false`

### Requirement: 部分覆盖的回测被拒绝

回测在取数后 SHALL 检查覆盖度。当 provider 报告 `missing` 非空时，run SHALL 转为 `FAILED`，错误 SHALL 点名**标的**与**至少一段缺失区间**（含起止日期），并说明缺失段数。

`market_closed` 非空 SHALL NOT 导致拒绝：经确认无数据的区间是正常休市，序列在其交易日上是完整的。

#### Scenario: 部分覆盖导致失败并点名
- **WHEN** 某标的在请求窗口内只有一段区间的数据，其余为未证明的缺失
- **THEN** run 转 `FAILED`，错误含该标的与缺失区间起止

#### Scenario: 只有休市缺口时回测照常完成
- **WHEN** 窗口内的缺口全部是经询问确认无数据的休市区间
- **THEN** run 正常 `COMPLETED`，不因 `market_closed` 被拒

#### Scenario: 探明失败即视为缺失
- **WHEN** 覆盖度检查中询问上游失败（`fill_failed=true`）导致缺口无法证明是天休
- **THEN** 这些区间仍计入 `missing`，run 被拒——证明不了完整就不当完整

#### Scenario: 缺口过多视为数据破碎并拒绝
- **WHEN** 一次检查遇到的缺口段数超过探明预算（正常 A 股两年约 7 段长假，预算 20 段）
- **THEN** 超出的区间不探明、保持 `missing`，run 被拒且错误说明缺口段数过多需先补数

### Requirement: 权重轨逐标的检查

权重轨回测 SHALL 对 `universe` 中**每个**标的做覆盖度检查。任一标的 `missing` 非空时，整个 run SHALL `FAILED` 并点名该标的。

#### Scenario: universe 中一个标的不完整即整体拒绝
- **WHEN** 三标的 universe 中只有一个标的部分覆盖
- **THEN** 整个 run `FAILED`，错误点名那个标的，不静默用其余标的完成回测

#### Scenario: 全部标的完整时组合回测照常
- **WHEN** universe 中每个标的的缺口都只是休市
- **THEN** 组合回测正常完成并产出权重快照

### Requirement: run 记录钉住数据指纹

回测 run SHALL 在 `params` 中记录：每个标的的数据 `fingerprint`、该标的的覆盖度摘要（缺失段数与休市段数）、以及 `coverage_checked`（本次是否真的做了检查）。

#### Scenario: 指纹随 run 留存
- **WHEN** 一次回测完成
- **THEN** 其 `params` 含每个标的的指纹，可用于与另一次 run 比对是否基于同一份数据

#### Scenario: 未检查被显式记录
- **WHEN** provider 不支持覆盖度能力
- **THEN** `params.coverage_checked` 为 false，使"这次没检查"可见而非隐形

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
