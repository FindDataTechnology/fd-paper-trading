## 1. 撮合计算器扩展

- [ ] 1.1 `fill_engine.py` 支持 STOP/STOP_LIMIT：触发判定（bid≤stop 卖 / ask≥stop 买）、触发后成交或转限价规则、无状态（输入含触发态，不读账本）
- [ ] 1.2 订单校验：`stop_price` 必填、STOP_LIMIT 的 `limit_price ≤ stop_price`（卖出方向）校验与拒绝报错
- [ ] 1.3 单测全覆盖：未触发 resting 零仓位、触发成交、跳空 quote 一次到位、STOP_LIMIT 触发后限价 resting、取消、非法组合拒绝

## 2. 订单生命周期与入口

- [ ] 2.1 orders 持久化与状态机扩展（`stop_price`/触发态；无列则 alembic 可逆迁移），触发评估挂在"新报价到达"时点
- [ ] 2.2 触发瞬间过既有 buying-power/持仓检查，失败记 REJECTED（不引入触发必成交假象）
- [ ] 2.3 REST / MCP / web 下单面板三入口接受新订单型；订单列表区分未触发 stop 与已触发限价
- [ ] 2.4 停跑语义回归：run STOP 取消挂单含止损单，措辞与止损语义在 UI 文案中区分

## 3. run 级风控覆盖（跨引擎同一逻辑）

- [ ] 3.1 覆盖逻辑抽公共模块：`stop_loss_pct` / `trailing_stop_pct`（峰值回撤）/ `cooldown_bars`，输入输出纯函数化（持仓、价格序列 → 触发清单 + 平仓指令）
- [ ] 3.2 backtester 接线：逐 bar 评估、bar low 触发、跳空保守成交（min(止损位, open)）、触发记事件、`risk` 缺省逐位不变（回归既有基线）
- [ ] 3.3 runner 接线：逐 tick 对报价评估、触发下归因市价平仓单、计入日单量帽、帽满跳过记事件
- [ ] 3.4 weight 模式冷却：有效目标权重=0 套用在订单换算前，快照记有效/目标两列；信号轨冷却同理
- [ ] 3.5 回测请求与 live run 启动 API 接受可选 `risk`；结果标注配置与触发清单
- [ ] 3.6 口径留痕：live run 记 provider 复权口径；非新 bar 的指纹变化记 run 事件；账本不重写（回归覆盖）

## 4. 端到端验收

- [ ] 4.1 录制回放协议（复用 `e2e_weight_paper` 逐根揭示）下：同窗口回测触发清单与 live 回放触发一致（tick=bar 协议），证据落 `reports/`
- [ ] 4.2 跳空场景实data验收（astock 或夹具）：成交价取开盘不利者，报告如实
- [ ] 4.3 weight 冷却 e2e：止损→两个调仓点权重 0→第三点恢复，快照可归因
- [ ] 4.4 A 股个股全链冒烟（依赖 ① 生产可用）：下单 STOP→除权事件→口径事件→触发平仓，证据落 `reports/`

## 5. 收尾

- [ ] 5.1 全量测试套件通过；web 单测覆盖新订单型 UI
- [ ] 5.2 `openspec validate stop-order-execution --strict` 通过后归档
