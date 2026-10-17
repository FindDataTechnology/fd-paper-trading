## Context

动机见 `proposal.md`。现状（读自 `fd-paper-trading` 仓）：

- **回测器实现**：`src/fd_paper_trading/strategy/backtester.py`——fill-at-close 内存组合，6 统计量，无成本无基准；`strategy/templates.py` 的 5 个模板（`sma_cross`/`rsi_reversion`/`bollinger_breakout`/`macd_cross`/`dca`），`strategy/indicators.py` 共用指标实现；`strategy/runner.py` 的 `PaperRunSupervisor` 跑同一 `on_bar` 契约。spec 的既有约束（`openspec/specs/strategy-backtest/spec.md`）要求"回测与实盘共用同一评估契约"、"运行钉住策略版本"——本变更全部继承。
- **数据源**：`marketdata/` 三个 provider，生产强制 `fd-open-data`（基金 NAV 家族当前配置）。回测吃日线，`_MAX_WINDOW_DAYS=730`，`max_backtest_days` 按用户组。
- **分批约束**：group 配置有 `max_backtest_days`（默认 730）——walk-forward 与参数扫描会显著放大总数据量与时耗，必须在本变更里明确它们与既有 group 限额的关系（见 D6）。
- **计算面外部依赖**：批 1.1 `fd-quant-compute-plane` 提供组合统计与 walk-forward 切分工具（MCP：stdio + HTTP）。跨仓调用在服务进程内走 HTTP MCP 客户端；fd-paper-trading 已有 `fd-open-data-mcp` 作为 Python 依赖库的先例（`Design` 里记为"同栈优先"）。
- **上游纪律**：FinRL-X 的 `WalkForwardAnalyzer` + `validate_no_lookahead`（`walk_forward.py:478` 抛错）；TradingAgents 的"所有带日期工具钳到 trade_date"；FinRL-X 的成本默认量级（佣金 0.1% 双边、`transaction_cost_pct=0.001`）——我们的默认值改按 A 股实际（佣金 0.025%、印花税卖出 0.05%、滑点 5bps），因为 CN 市场的费结构与美股不同且印花税单边。

## Goals / Non-Goals

**Goals:**
- 单窗回测**完全向后兼容**；新能力全部以可选字段/新端点接入。
- 三项新能力（walk-forward、成本、基准）各自独立可验收，互不阻塞。
- 统计口径与批 1.1 计算面**跨仓对拍**一致——这是"模式可比"的地基。

**Non-Goals:**
- 不做 tick/盘口模拟、不做做空与杠杆、不做多标的组合回测（P1 内承载）。
- 不做最优参数的"推荐"（只做扫描 + 样本内标注）。
- 不改 live runner 与 iOS/Web 既有交互（web 面板仅加展示字段）。
- 不做基准数据的采集（走既有 provider；不可用即报缺）。

## Decisions

### D1. 三种模式并存：单窗（默认）/ walk-forward / 扫描；通过请求字段切换

**选择**：`backtests` 请求新增可选字段 `train_window`/`test_window`/`step`（三者齐全启用 walk-forward）；`param_grid` 启用扫描；`cost_model`、`benchmarks` 独立可选。不新建平行端点（除扫描结果查询需要一个读取端点）。
**理由**：既有 API 形状（`POST /api/v1/strategies/{id}/backtests`）已是"一次回测"的语义容器，三种模式是同一语义的三种配置；新建平行端点会让 web 客户端与 MCP 工具面都翻倍。向后兼容由"全部可选 + 默认不启用"保证（spec 已钉）。
**备选**：为 walk-forward 新建 `/backtests/walkforward` 端点——更"显式"但重复三套请求/结果模型。否。

### D2. 计算面走 MCP HTTP 调用，不复制实现

**选择**：组合统计与窗口切分/验证全部调用批 1.1 的工具；fd-paper-trading 内不新增统计实现。
**理由**：跨仓对拍是 spec 级验收项；两份实现（哪怕同源复制）必然漂移，而"账本/回测/研报三处口径不一致"是这类系统最隐蔽的错。调用面是纯函数、无状态，HTTP 调用失败时表现是明确的失败而非静默降级。
**备选**：(a) 本仓内实现并靠测试对拍——漂移风险仍在；(b) 把批 1.1 设为硬前置、否则不启动本变更——失去并行度。选择"调用 + 若批 1.1 未就绪则以内部实现垫底但在任务中标注对拍前置、不通过则不宣称完成"（proposal 已声明）。
**风险与缓解**：MCP HTTP 不可用时回测直接报缺（不静默降级为内部实现），避免"时而两种口径"。

### D3. 无未来函数断言放在"数据装载后 + 每窗执行前"，失败即整轮 FAILED

**选择**：断言点两处（装载后全量一次；walk-forward 每窗执行前一次，`as_of` 为窗末）；越界使运行 `FAILED`，错误含日期。
**理由**：装载后的全量断言防"整体数据就超窗"，逐窗断言防"多窗拼接时窗口管理出错"。整轮 FAILED 而不是跳过该窗——跳过会让用户拿到"少了几窗"的结果而不知（对照 spec 的"FAILED 不得伪装 COMPLETED"既有纪律）。
**备选**：仅装载断言（漏掉窗口管理 bug）；警告继续（等于没有断言）。否。

### D4. 成本模型：参数进请求、快照进结果、明细可汇总

**选择**：`cost_model = {enabled, commission_rate, stamp_tax_rate, slippage_bps}`，默认值见 spec；成交价按方向调滑点；佣金双边、印花税卖出单边；运行记录存配置快照与三项汇总额。
**理由**：默认值取 A 股现实（不是 FinRL-X 的 0.1% 美股量级），并在 spec 里写死默认值——因为"默认成本"就是回测可信度的下限。快照进结果是可解释性要求：同一策略两次回测结果不同时，第一件事就是看成本配置是否变过。
**备选**：全局配置成本（改一次影响所有历史可比性）；只记总费用不记快照（不可解释）。否。

### D5. 基准：默认双基准、缺数据报缺、相对指标三件套

**选择**：默认 `000300.SS` + `000905.SS`；结果含各基准曲线与统计 + 超额收益/跟踪误差/信息比；缺数据明确标注（不省略、不伪造）。基准数据走与标的价格同一 provider 管道（同一 `get_bars` 语义）。
**理由**：基准是"策略 vs 运气"的最小回答；双宽基默认是 CN 语境下的合理起点（P2 自适应轮动将来还会换 QQQ 类基准，那属策略层参数）。缺数据报缺直接对应既有的反捏造纪律与 `equity-valuation` 的"通道不可用即中止"先例。
**备选**：单一基准（信息不足）；基准可选但缺数据时静默省略（沉默的谎言）。否。

### D6. walk-forward 与扫描复用 `max_backtest_days` 的总跨度限额，另加组合数上限

**选择**：walk-forward 请求的总数据跨度（第一窗起点到最后一窗终点）仍受 group 的 `max_backtest_days` 约束（超出即拒）；扫描的组合数上限新增独立配置（默认 50，可配）。
**理由**：group 限额的语义是"每用户回测数据量"，walk-forward 没有理由绕开它——否则单个用户可用 walk-forward 把数据消耗放大数倍。组合数上限防"参数爆炸"把服务打死（笛卡尔积的风险是量级性的）。
**备选**：walk-forward 不受限（限额失效）；扫描不限（可用性风险）。否。

### D7. 扫描结果的"样本内"标注做成机读字段

**选择**：扫描结果含 `in_sample: true` 与 `disclaimer` 文本字段（机读可用），排序字段由请求指定（默认按 sharpe）。
**理由**：FinRL-X 的头号教训之一是"报告的性能未审计"；参数扫描的最佳组是最容易被误读为推荐的输出，标注必须是数据的一部分而不是文档里的一句话（对照 FinRobot 的"不确定性与口径警告进结果"纪律）。
**备选**：只在 UI 文案里提示（MCP/API 消费者看不到）。否。

## Risks / Trade-offs

- **[跨仓 MCP 调用引入失败模式]** → 调用失败即回测 FAILED 并指明"计算面不可用"；不降级、不缓存旧结果（口径一致性优先于可用性）。
- **[walk-forward 的时耗放大]** → 窗口数 × 单窗时长；组合数上限 + group 跨度限额双层约束；运行仍是后台任务（既有 QUEUED→RUNNING 生命周期不变）。
- **[成本默认值改变新旧结果可解释性]** → 默认 `enabled=false`（与现状完全一致），用户显式开启；文档标注"开启后收益下降属预期"。
- **[基准数据在 fd-open-data 当前配置（基金 NAV 家族）下不可用]** → 这正是批 0.1 价格供数要解决的通道问题；本变更的实现按"可用即用、不可用报缺"落地，批 0.1 完成后基准才真正可用。**验收 6.x 的端到端基准对比需在批 0.1 落地后的数据上跑**，若届时仍缺数据则该验收项如实记为受阻（不伪造通过）。
- **[扫描结果被当作调参推荐]** → 机读标注 + 结果排序仅作呈现；P1 技能文本（另 change）负责使用纪律。
- **[指标/统计口径与 web 面板不一致]** → web 面板直接渲染 `stats` 字段，不在前端重算任何统计。

## Migration Plan

1. Alembic 迁移：`strategy_runs` 增字段（成本快照、基准配置、walk-forward 窗列表、扫描参数），全部 nullable/带默认。
2. 统计扩展与成本模型先行（单窗路径）；跑既有回测测试确保零成本默认下结果不变（回归基线）。
3. 断言器接入（调用批 1.1 验证器或垫底实现）→ 泄漏夹具测试。
4. 基准对比（provider 取基准 bars + 相对指标）。
5. walk-forward 模式 → 窗口快照与拼接曲线 → 逐窗统计。
6. 参数扫描（网格 + 上限 + 排序 + 标注）。
7. web 回测面板展示新字段（成本、基准、walk-forward 窗视图、扫描矩阵）。
8. 回滚：迁移可逆（新增列全部可空）；功能由请求字段开关，服务回退版本后旧行为自然恢复。

## Open Questions

- 基准数据的 provider 选择（`fd-open-data` 当前家族是基金 NAV；走哪条通道取 `000300.SS`）——批 0.1 供数落地后按实际可用通道定，不改本变更的接口形状。
- 扫描结果是否需要独立的持久表（vs 存在 `strategy_runs` 的 JSON 字段）——按实现期数据量定；接口语义不变。
