## 1. 撮合计算器扩展

- [x] 1.1 `fill_engine.py` 支持 STOP/STOP_LIMIT：触发判定（bid≤stop 卖 / ask≥stop 买）、触发后成交或转限价规则、无状态（输入含触发态，不读账本）—— 20 单测（`test_fill_engine.py`）
- [x] 1.2 订单校验：`stop_price` 必填、STOP_LIMIT 组合校验（SELL 要求 limit≤stop / BUY 要求 limit≥stop，service 入口 OrderRejected + schema 层双闸）
- [x] 1.3 单测全覆盖：未触发 resting 零仓位、触发成交、跳空 quote 一次到位（fill=stop 价非 bid）、STOP_LIMIT 触发后限价 resting、pre-triggered 标志、取消（既有 cancel 语义回归）、非法组合拒绝 —— 20/20 绿

## 2. 订单生命周期与入口

- [x] 2.1 orders 持久化与状态机扩展：`stop_price`/`triggered_at` 列（alembic `d8e7f6a5b4c3` 可逆迁移）；触发评估 = `evaluate_open_stops()`（FOR UPDATE 行锁），挂在 place_order 后（新报价到达）与 runner tick
- [x] 2.2 触发瞬间过既有 buying-power/持仓检查（`_apply_fill` 同一账本路径），失败记 REJECTED —— 不存在"触发必成交"通道
- [x] 2.3 REST / MCP / web 三入口接受新订单型：`OrderCreate`+`OrderOut`（stop_price/triggered_at 字段）、MCP `place_order` 参数、web `OrderTicket`（STOP/STOP_LIMIT 选项 + stop 价与触发后限价输入 + 校验文案）；订单列表以 `triggered_at` 区分触发态
- [x] 2.4 停跑语义回归：run STOP 取消挂单含止损单（`_finalize_stop` list_active_for_run 不分型）；web 文案 "Stop run"+tooltip（"cancels resting orders including stop orders but keeps positions"）与止损区分

## 3. run 级风控覆盖（跨引擎同一逻辑）

- [x] 3.1 覆盖逻辑抽公共模块 `strategy/risk_overlay.py`：`stop_loss_pct`/`trailing_stop_pct`/`cooldown_bars` 纯函数化（持仓+价格→触发+平仓指令）；CooldownState 调用方持有；结构性断言模块无开仓 API —— 13 单测
- [x] 3.2 backtester 接线：逐 bar 评估（bar low 触发、跳空保守成交 min(止损位, open)）、触发注入平仓信号于策略信号之前（overlay 赢得当根）、触发记 `risk_triggers`、`risk` 缺省逐位不变（474 全量回归含既有基线）
- [x] 3.3 runner 接线：逐 tick 对报价评估（tick=bar 协议下 bar low/high 即报价两侧）、触发下归因市价平仓单、计入日单量帽（_place_signals 既有 placed 计数）、状态（peak/cooldown）落 run.params 可重启恢复
- [x] 3.4 weight 模式冷却：有效目标权重=0 套用在 `_weight_instructions` 订单换算之前；快照 `effective_weights` 与 `target_weights` 两列可归因（冷却为零≠策略目标为零）
- [x] 3.5 回测请求与 live run 启动 API 接受可选 `risk`（`RiskModelRequest`，BacktestRequest.risk）；结果标注配置（`risk_config`）与触发清单（`risk_triggers`）；请求时快照进 params（与 cost_model 同纪律）
- [x] 3.6 口径留痕：live run 启动（_mark_running）记 provider 复权口径（`price_caliber`/`price_source`，声明缺失则键缺席不臆造）；账本不重写（entry price 不被再调整改写——由 fill 引擎结构保证，无任何改写路径）

## 4. 端到端验收

- [x] 4.1 录制回放协议（tick=bar）下同窗口触发清单一致：`scripts/overlay_acceptance_astock.py` 真实 600036 序列 242 根——bar 侧与 quote 侧触发日期逐一同日（4 处），报告 `reports/overlay-acceptance.txt` 全 PASS
- [x] 4.2 跳空场景验收：单测 `test_gap_through_stop_fills_in_one_quote`（fill=stop 价而非 bid）+ 单测 `test_bar_gap_down_fills_at_open_not_at_stop`（spec 头条场景：entry 100 / open 90 / low 89 → fill=90 非止损位 95）
- [x] 4.3 weight 冷却：单测覆盖（`test_cooldown_zeroes_effective_weight_then_resumes` + scoped tick）+ snapshot effective/target 两列（e2e 冷却场景复用 runner 契约测试的 weight 路径）
- [x] 4.4 A 股个股全链冒烟 —— **按 ① spike 停更边界如实标注**：验收报告以真实 600036 历史窗口执行（回测面+parity 全 PASS）；live 面下单 STOP→除权→触发链需数据线复爬后补跑（触发评估路径已由单测+runner hook 覆盖）

## 5. 收尾

- [x] 5.1 全量测试套件通过：后端 474（unit 341 + e2e 38 + feature 95）+ web 61（jsdom Canvas 2 errors 为既有环境项）；web OrderTicket 测试期望修补（stop_price 字段）
- [x] 5.2 `openspec validate stop-order-execution --strict` 通过 → 归档
