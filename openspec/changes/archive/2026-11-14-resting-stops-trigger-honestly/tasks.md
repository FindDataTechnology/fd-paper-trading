## 1. 先量后改

- [x] 1.1 实测触发记账错：bid=269.95、止损 268.95、价格未动，挂单后 `triggered_at` 已被盖；同标的另一张限价单（自身不成交）带来一次评估 → 止损单 `FILLED @ 268.95`、持仓清零
- [x] 1.2 实测 `evaluate_open_stops` 全仓零测试：`grep -rn "evaluate_open_stops" tests` 只命中本次新增文件（`tests/unit/test_fill_engine.py` 的十条 STOP 用例测的是纯计算器，不是记账那一步）
- [x] 1.3 实测生产数据：fd-prod pod 内只读查 `orders` → 4 行、全 MARKET/FILLED、**零张止损单** ⇒ 无需数据修复迁移

## 2. 修触发记账

- [x] 2.1 `SimFillEngine.trigger_condition_met(quote, side, stop_price)` 抽为触发规则的单一真源；`_compute_stop_fill` 改调它（方向语义逐字不变：买 `ask>=stop`、卖 `bid<=stop`）
- [x] 2.2 `evaluate_open_stops`：先按 `trigger_condition_met` 决定是否盖 `triggered_at`，再带 `triggered=order.triggered_at is not None` 计算成交（删掉 `result.status in ("FILLED","OPEN")` 那个推断）

## 3. sweeper

- [x] 3.1 `execution/stop_sweeper.py::StopSweeper`：`start/stop/_loop/sweep_once/accounts_with_resting_stops`
- [x] 3.2 扫描范围：`status=OPEN AND order_type IN (STOP, STOP_LIMIT)`，且 `account_id NOT IN` 活跃 run 的账户（`ACTIVE_RUN_STATUSES`）
- [x] 3.3 每账户一个会话、账户行 `FOR UPDATE`，走同一条 `evaluate_open_stops`；单账户异常只记日志不中断整轮
- [x] 3.4 触发后的订单重取（带 fills）并按既有 `order` 事件推给属主
- [x] 3.5 配置 `stop_sweep_seconds`（默认 60，0=关）+ `create_app` 挂 `app.state.stop_sweeper` + lifespan 起停 + `.env.example` 一行
- [x] 3.6 导出 `StopSweeper` 于 `execution/__init__.py`

## 4. 判据（每条都能红）

- [x] 4.1 `tests/test_resting_stops.py` 11 条：
  - 挂单不把未触发的单记成已触发
  - **未触及的止损不因同标的别的单而成交**（钱那条）
  - 连续评估三次结果不变（幂等）
  - 真被触及时仍按止损价成交（反向对照）
  - STOP_LIMIT 触发后转限价 resting，不因"已触发"反复成交
  - sweeper：`sweep_once` 唤醒 / 后台循环自己唤醒 / lifespan 起停 / `0` 关闭
  - 扫描范围：无止损单的账户不入选 / 活跃 run 的账户不入选
- [x] 4.2 五次注入各自只红该红的：
  - 恢复旧写法（按 `OPEN` 判触发）→ 红 9 条
  - 永不盖 `triggered_at` → 红 3 条（触发/成交/限价 resting）
  - lifespan 不启动 sweeper → 只红 lifespan 那条
  - sweeper 不排除活跃 run → 只红 run 那条
  - 卖出触发方向取反 → 红 7 条
- [x] 4.3 `pytest tests` 全绿：**768 passed**（改动前 757，新增 11）

## 5. 收尾

- [x] 5.1 `openspec validate 2026-11-14-resting-stops-trigger-honestly --strict`
- [x] 5.2 归档并核对 `Totals`：`order-execution` +1 ~1
- [x] 5.3 归档后 `openspec validate order-execution --type spec --strict`
- [x] 5.4 出片与上线：本仓镜像未自动滚版，止损失修的版本仍在产；上线时点见报告（这是"要不要出片"的用户决策，本 change 只保证代码与规格一致）
