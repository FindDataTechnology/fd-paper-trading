# Spec Delta

## Purpose

定义跨运行比较的口径：什么算"同一次实验"、不可比时如何如实报出、以及比较结果的可机读形状——使"策略在回测里行、在纸面跑里不行"这类判断有依据而不是印象。

## ADDED Requirements

### Requirement: 可比身份与不可比原因

系统 SHALL 为每次运行记录一份**模拟画像**：成本模型（启用状态与参数）、整手规则、交收规则。画像在运行创建时写入，之后不随配置变化而改写。缺失某项时 SHALL 记为该维度「未建模」，MUST NOT 代入默认值冒充已建模。

比较两次运行时，系统 SHALL 按以下维度判定「是否同一实验」：策略标识与版本、运行模式与窗口、初始资金、成本模型、模拟画像、整手与交收规则、数据覆盖身份。任一维度不同，比较结果 SHALL 返回 `comparable: false` 并**逐条列出不同的维度**，且 MUST NOT 返回任何统计差值。

#### Scenario: 成本口径不同 → 不可比

- **WHEN** 比较一次零成本回测与一次含佣金印花税滑点的纸面运行
- **THEN** 返回 `comparable: false`，差异清单含 `cost_model`
- **AND THEN** 结果中不含任何统计差值字段

#### Scenario: 全部维度一致 → 可比

- **WHEN** 同一策略版本、同一窗口、同一初始资金、同一模拟画像的两次运行被比较
- **THEN** 返回 `comparable: true`，并给出双方统计与逐字段差值

#### Scenario: 缺失画像不得冒充已建模

- **WHEN** 一次早期运行没有模拟画像记录
- **THEN** 该运行的画像维度记为「未建模」
- **AND THEN** 与任何有画像的运行比较时该维度计为不同，结果为 `comparable: false`

### Requirement: 运行记录模拟画像

每次运行（回测与纸面、单标的与权重轨）SHALL 在创建时记录模拟画像 `simulation_profile`：成本模型（启用状态与参数）、整手规则、交收规则、行情口径。画像 SHALL 只反映该运行创建时的事实，后续配置变化 MUST NOT 改写既有运行已记录的画像。

#### Scenario: 画像在创建时固定

- **WHEN** 一条运行以零成本、整手、T+1 画像创建，之后部署配置改变
- **THEN** 该运行的画像仍是创建时的那一份

#### Scenario: 画像字段可机读

- **WHEN** 读取任一运行的详情
- **THEN** 响应含 `simulation_profile`，其成本/整手/交收维度各为明确取值或「未建模」

### Requirement: 窗口重叠是硬条件

两次运行只有在**有效重叠区间**足够产生统计时才可比较：重叠交易日数少于计算面所需下限时，比较 SHALL 返回 `comparable: false` 并给出重叠区间与所需下限，MUST NOT 用各自全窗口的统计做并排。

#### Scenario: 窗口不重叠

- **WHEN** 回测窗口为 2025-01-01..2025-06-30、纸面运行为 2025-09-01 起
- **THEN** 返回 `comparable: false`，原因指明无重叠区间
- **AND THEN** 不返回双方统计的并排

### Requirement: 比较结果的可机读形状

比较结果 SHALL 是结构化对象：`comparable`（布尔）、`reasons`（不可比时非空的原因数组，每项含维度与说明）、`window`（双方窗口与重叠区间）、`runs`（双方标识与画像）、`stats`（可比时的逐字段双方值与差值）。`reasons` 非空与 `comparable: true` MUST NOT 同时成立。

#### Scenario: 不可比时统计为空而非零

- **WHEN** 比较结果为 `comparable: false`
- **THEN** `stats` 为 null 或空，且 `reasons` 至少一项
- **AND THEN** 任何差值字段不出现（不写 0，不写 null）
