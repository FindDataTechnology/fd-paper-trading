## Context

现状（读自本仓 2026-11-06）：

- **撮合**：`execution/fill_engine.py` 无状态计算器，订单型仅 MARKET/LIMIT；成交语义"market buy→ask / market sell→bid / limit 触发即成交否则 resting"已由 spec 钉死。live 报价由日线最新收盘 + 合成半价差（5bps）推导——**日线粒度是服务的现实**，任何"盘中触发"语义都是假象，必须如实按日线声明。
- **订单生命周期**：orders 表 + 状态机（OPEN/FILLED/CANCELLED/REJECTED），cancellation path 存在；策略单带 `strategy_run_id` 归因；日单量帽（`max_orders_per_day`）对 run 的所有指令计数。
- **回测器**：`strategy/backtester.py` fill-at-close 内存组合；权重轨 `weight_backtester.py` 有独立的目标规则与整数股取整。既有 spec 纪律："回测与实盘共用同一评估契约"、"FAILED 不得伪装 COMPLETED"。
- **live runner**：`strategy/runner.py` 逐 run asyncio 任务、30min 轮询、bar 窗口逐 tick 重取；停跑语义（既有 "Stop semantics" requirement）= 取消该 run 挂单、不动持仓——与本变更的止损单是两个概念，措辞必须区分。
- **上游**：批 6 ① `astock-price-channel` 给 provider 加复权口径声明；本变更消费之（前复权"锚定最新价、调整历史价"意味着最新成交价恒为真实价、账本不需重写，风险只在混口径，做成留痕即可）。

## Goals / Non-Goals

**Goals:**
- 账户层止损成为产品能力：STOP/STOP_LIMIT 可下、可撤、触发即成交。
- 风控跨引擎一致：同一覆盖逻辑回测逐 bar、live 逐 tick——"回测与实盘共用同一评估契约"在风控维度成立。
- weight 模式可风控：止损+冷却防"卖了又买回"。
- 口径事件化：除权再调整可见、账本不重写。

**Non-Goals:**
- 不做盘中逐笔/盘口触发；不做 OCO/条件单组合；不做做空方向止损。
- 不改 fd-quant 契约（TargetWeights 不加字段）与 group 限额数值。
- 不做止损单的移动止损托管（移动止损是 run 级覆盖的能力，不是订单型——挂单价不随行情自动移动）。

## Decisions

### D1. 止损单触发评估放在"新报价到达"这个唯一时点，撮合器保持无状态

**选择**：触发判定 = 报价到达时的 bid/ask 与 stop_price 比较；撮合计算器输入加一个"已触发"判定而非让撮合器记账。live 下报价到达 = runner tick / 手动补单 / run 评估；判定频率与日线粒度一致（每天最多几次 tick，不做盘中假象）。
**理由**：与既有"无状态计算器"spec 兼容（状态只在订单行：TRIGGERED 与否）；日线服务的报价节奏本来就是离散的，把节奏写进 spec 比假装连续诚实。
**备选**：后台监控进程逐秒盯价——日线报价下是纯开销且制造"实时止损"错觉，否。

### D2. run 级风控覆盖而非订单型托管移动止损

**选择**：移动止损（trailing_stop_pct）实现为 run 级覆盖逻辑（runner/backtester 逐期评估持仓峰值回撤），不下 TRAILING_STOP 挂单；固定止损两种表达都给（账户层 STOP 单给手动交易用，run 级覆盖给策略用）。
**理由**：挂单价随行情自动移动需要持续的报价流与订单改写，在日线报价下每根 bar 都要改一次挂单——事件噪音与账本污染远大于收益；run 级覆盖在两引擎共用一份逻辑，回测/实盘一致性由构造保证。6.5 的实测结论"止损须策略内表达或外部监督"由此收敛为"平台内置监督"。
**备选**：TRAILING_STOP 订单型——日线粒度下事件噪音大、与限价单 resting 语义冲突，否。

### D3. 跳空保守成交：fill = min(止损位, 当期开盘价)（卖出方向）

**选择**：回测 bar 触发时成交价取止损位与开盘价的较不利者；live 触发时按当日报价（若当日开盘已跳穿止损，quote 在 bar 内自然反映）。
**理由**：反事实按止损价成交会系统性高估风控效果（跳空低开是最常见的真实风险形态）；保守方向与成本模型"收益单调不升"的既有验收精神一致。
**备选**：一律按止损价成交（回测好看、实盘做不到），否。

### D4. weight 模式冷却做在"有效目标权重"层，不改契约

**选择**：止损触发的标的在 `cooldown_bars` 个调仓点内有效目标权重=0（runner 在把 TargetWeights 差额换算成订单前套用覆盖），冷却结束恢复策略目标；fd-quant 契约不加字段，覆盖配置存 run。
**理由**：契约是跨仓稳定面（packs/回测/账本三方消费），风控是服务层编排关切；在换算层套用保证"止损当根卖、下点买回"在构造上不可能，而不是靠 skill 文本纪律。
**风险与缓解**：冷却与策略自身目标冲突时以冷却优先——结果快照记录有效权重与目标权重两列，偏差可归因（沿用 rotation-run 偏离归因的既有分类）。

### D5. 口径事件化而非口径固定化

**选择**：live run 记录 provider 口径声明；逐 tick 重取窗口时若某标的指纹因非新 bar 原因变化（再调整）→ 记 run 事件并继续（新窗口内部自洽）；账本不改写。
**理由**：前复权锚定最新价的性质使"最新成交价恒真、历史信号窗口自洽"，唯一真实的坑是跨口径比较——事件化让它在 UI/结果里可见；固定住某日口径则需要在服务端存序列副本，复杂度不值。
**备选**：run 启动时快照整段历史序列（口径冻结）——存储与新鲜度双输，否。

## Risks / Trade-offs

- **[回测与 live 触发时刻天然不可能逐位一致]**（回测有 bar low，live 只有 tick 报价）→ spec 以"同一覆盖逻辑"为承诺而非"同一触发价"；任务验收用"同窗口回测 vs 录制回放 live 触发清单一致性（在 tick=bar 的回放协议下）"落实证，复用既有 e2e_weight_paper 逐根揭示协议。
- **[止损单 resting 期间占用无购买力检查新语义]** → 止损单不锁现金/仓位（触发才成交），但触发瞬间必须过既有 buying-power/持仓检查，失败即拒且记 REJECTED——不引入"触发必成交"的假象。
- **[cooldown 与 max_orders_per_day 叠加饿死平仓单]** → 触发平仓单与普通单同样计数，帽满时跳过并记事件（既有语义），事件流可见；不做帽外特权通道（保持帽的诚实性）。

## Migration Plan

1. 撮合计算器扩展（纯函数 + 单测，模拟报价下全语义覆盖）。
2. 订单生命周期/仓储/API 校验（STOP/STOP_LIMIT 进 web 下单面板与 MCP 参数）。
3. 覆盖逻辑抽公共模块 → backtester 逐 bar 接线 → runner 逐 tick 接线 → weight 冷却。
4. run 启动 API `risk` 字段 + 结果标注。
5. e2e：录制回放协议下回测 vs live 触发一致；跳空场景；冷却场景。
6. 回滚：全部可选字段，回退版本即恢复旧行为；无迁移（orders 表新订单型复用现有列，`stop_price` 若无列则加 nullable 列——按实现期 schema 定，可逆）。

## Open Questions

- orders 表是否已有可复用列承载 `stop_price`/`triggered_at`（实现期看 schema，需迁移则走 alembic 可逆迁移）。
- STOP_LIMIT 触发后转限价的 resting 展示文案（web 订单列表如何区分"未触发的 stop"与"已触发的限价"）——实现期与 UI 文案一起定，状态机字段已留 `TRIGGERED` 位。
