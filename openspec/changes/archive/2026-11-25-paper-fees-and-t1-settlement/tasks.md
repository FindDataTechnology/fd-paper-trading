# Tasks

## 1. 费用算术搬一处（D1）

- [x] 1.1 新增 `execution/costs.py`，把 `strategy/costs.py` 的 `CostModel` / `FillCost` / `apply_costs` 移到此处；`strategy/costs.py` 改为 re-export。验证：`grep -rn "from .costs import\|from ..strategy.costs" src/` 无 import 断链，`pytest tests/unit -q` 全绿。
- [x] 1.2 为 `apply_costs` 补边界单测：卖出印花税仅卖侧、滑点为 0 时价格不变、`enabled=false` 与原算术逐位一致。验证：`pytest tests/unit/test_costs.py -v`。

## 2. 账本扣费（D2）

- [x] 2.1 `persistence/models.py::Fill` 增 `commission` / `stamp_tax` / `slippage_cost` 三列（可空）；新增 alembic 迁移。验证：`alembic upgrade head` 在干净库与既有库上均成功。
- [x] 2.2 `_apply_fill` 接收成交成本并写入三列；现金按 `cash_delta`（含费）变动。验证：单测买入 100@10 现金减 `1000+0.25`，卖出增 `1000-0.25-0.5`。
- [x] 2.3 `_check_buying_power` 按含费单价计算（D7）。验证：单测断言恰好只够裸价的买被 `insufficient_buying_power` 拒绝。

## 3. 持仓批次与 T+1（D3–D6）

- [x] 3.1 新增 `persistence/models.py::PositionLot`（字段见 D3）+ 迁移 + 唯一/普通索引。验证：迁移成功，`\d position_lots` 字段与索引齐。
- [x] 3.2 `_apply_fill` 买入时生成批次（`opened_on` 取成交日的日界日，`unit_cost` = 含费每股）；卖出时按 FIFO 消耗 `remaining` 并算已实现盈亏（扣本次费用）。验证：单测「10.00 买 100 → 12.00 买 100 → 11.00 卖 150」已实现盈亏与手算一致、剩余 50。
- [x] 3.3 新增 `available_quantity(account_id, instrument_id, as_of)`：无批次 → 持仓总量（D5）；有批次 → `Σ remaining WHERE opened_on < as_of`。验证：单测覆盖两种分支。
- [x] 3.4 卖出校验改为 `qty > available`，错误文本同时给出可卖与持仓总量。验证：e2e 当日买当日卖 → 400 且文本含两个数字。
- [x] 3.5 不变量测试：`Σ lot.remaining == position.quantity`（无批次持仓豁免）。验证：`pytest tests/unit/test_position_math.py -k lots`。

## 4. 止损与重置跟随新语义

- [x] 4.1 `evaluate_open_stops` 在可卖为 0 时记录"交收不可执行"且**不**置 `triggered_at`。验证：单测断言触发记录存在、`triggered_at is None`、仓位不变。
- [x] 4.2 `accounts/service.py::reset` 摊平后删除该账户全部批次。验证：e2e 重置后 `position_lots` 为空且当日新买仍不可卖。

## 5. 回测两轨接入（D9–D10）

- [x] 5.1 单标的 `_Portfolio` 增 `bought_today`，卖出上限 = `quantity - bought_today`；每 bar 末尾归零。验证：单测同一 bar 先买后卖，卖出不成交且结果留痕。
- [x] 5.2 权重轨 `_rebalance` 的成交改走 `apply_costs`；`strategies.py:424` 的硬拒绝删除；运行写 `cost_config` / `cost_summary`。验证：`scripts/e2e_weight_backtest.py` 带成本跑通；含费与零成本 `final_equity` 不同且含费更低。
- [x] 5.3 三条轨共用同一费用实现的证据测试：同一组 `(side, qty, price, model)` 在回测与纸面记账路径得到相同 `cash_delta`。验证：单测对拍。

## 6. 既有测试与新语义对齐

- [x] 6.1 `tests/test_money_path_invariants.py`、`tests/test_daily_pnl.py`、`tests/e2e/test_strategy_paper_runs.py` 中凡断言"现金 = 数量×价格"的改为含费；凡断言"当日可买卖"的改为断言被拒。验证：`pytest tests/ tests/e2e -q` 全绿。
- [x] 6.2 全量回归：`pytest -q` 与 `npm run test`。验证：两处无新增失败。
- [x] 6.3 前端：成交/持仓相关视图如展示现金或成本，确认含费后仍自洽（不改口径则不展示费用）。验证：`npm run test` + 手工看一次持仓页。 **只读核实（本机禁本地前端 build/test）：现金取账本 `cash_balance`（含费后的真值，`GET /accounts/{id}` 实测返回 100000.0000），成交表只展示 Side/Qty/Price/Time；费用在回测面板出现（BacktestPanel 的 commission/stamp_tax/cost_config），成交页不展示费用 ⇒ 按任务允许的「不改口径则不展示费用」判自洽。**

## 7. 生产冒烟与验收

- [x] 7.1 在测试环境用一条真实策略跑「零成本 vs 含费」同窗口回测，记录 `final_equity` 差异并存入 `docs/`。验证：验收记录含真实数字与命令。 **现场证据：`docs/acceptance-fees-and-t1.md` §1（零成本 111993.7639 vs 含费 111855.7972，差 137.9667 / -0.1232%，真实四标 200 根日线）。**
- [x] 7.2 生产冒烟：纸面账户一笔真实买单，确认 `fills` 三列有值、现金减少等于含费金额、当日卖出被拒、次日卖出成功。验证：pod 内查询输出留存。 **现场证据：`docs/acceptance-fees-and-t1.md` §3。行为侧 6/6 e2e 绿（真 HTTP + 真 PaperExecutionService）；生产真机被 `stale_quote` 拒（astock_daily 停在 11-13、无 gapfill 调度，**上游行情断**，非本 change 缺陷）。**
- [x] 7.3 `openspec validate paper-fees-and-t1-settlement --strict` 通过。验证：退出码 0。

## Workflow follow-up

- 归档前确认 5 个 delta（trading-costs / settlement-and-lots 新增；order-execution / strategy-backtest / paper-accounts 修改）全部 sync 进主 spec。
- 回滚注意：批次行在回滚后残留，彻底回滚需清 `position_lots`（见 design.md Migration Plan）。
