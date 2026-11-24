# Tasks

## 1. 统计函数抽一处（D2）

- [x] 1.1 新增 `strategy/run_stats.py::headline_stats(equity, baseline, trade_count)`，把 `backtester.compute_stats` 的纯算术（total_return / max_drawdown / sharpe / final_equity）搬进来；`backtester.compute_stats` 改为调用它并保留自己的 `win_rate`。验证：`pytest tests/ -k "backtest"` 全绿，且既有回测统计字段逐位不变（`scripts/rerun_backtest_twice.py` 同窗口两次跑，`scripts/compare_backtest_fingerprints.py` 报 `stats_identical: true`）。
- [x] 1.2 为 `headline_stats` 写单测：单个点 → 收益 0/回撤 0/sharpe None；两点上涨 → 收益为正；基准不等于末值 → 收益相对基准。验证：`pytest tests/unit/test_run_stats.py -v` 全绿。

## 2. 纸面运行记录期初权益与终态统计

- [x] 2.1 `strategy/runner.py`：在首个权益点写入时，把该点日期记进 `run.params["opening_equity_date"]`（重启后仍能找到基准），期初权益值直接取第一个权益点。验证：`pytest tests/e2e/test_strategy_paper_runs.py -k opening -v`。
- [x] 2.2 `_finalize_stop` 与 `_finalize_error` 在改状态成功后计算并写入 `run.stats`：基线统计用 `headline_stats`，扩展统计用既有 `extended_stats`（计算面不可用 → 年化 null + 原因）；`trade_count` 取该 run 归因成交笔数（`Order.strategy_run_id` → `Fill`）。验证：e2e 起一条纸面运行、推若干 bar、停止后 `GET /api/v1/strategy-runs/{id}` 的 `stats` 含四个基线字段且 `total_return` 以首个权益点为基准。
- [x] 2.3 权益点不足两点时 `stats` 记 null + 原因，不得为 0。验证：单测断言只有一点时 `total_return is None` 且 `stats.notes` 非空。
- [x] 2.4 纸面 `win_rate` 置 null 并在 `stats.notes` 写明"需按 run 重放成本基础"。验证：同上 e2e 断言 `win_rate is None` 且 notes 含该句。

## 3. 模拟画像

- [x] 3.1 新增 `strategy/simulation_profile.py::build_profile(cost_model, settlement, price_world)` 返回 D5 形状；两个轨共用。验证：单测断言零成本 → `{"cost": {"mode": "none"}, ...}`，而不是缺 `cost` 键。
- [x] 3.2 回测创建运行时把画像写入 `run.params["simulation_profile"]`；纸面运行同样写入（当前 cost=none / settlement=T+0 / lot=whole_share）。验证：e2e 各起一条，读详情断言 `simulation_profile` 存在且三维度各有取值。
- [x] 3.3 画像只写一次：已创建运行的画像不因后续请求参数改变而改写。验证：创建后再次读详情，画像与创建时逐字相同。

## 4. 比较端点

- [x] 4.1 新增 `GET /api/v1/strategy-runs/{run_id}/comparison/{other_run_id}`：校验同属当前用户、同 `strategy_id`（不同则 400 `not_comparable`），计算窗口与重叠区间。验证：e2e 断言跨策略比较返回 400 且 code 正确。
- [x] 4.2 判定可比性：策略版本、窗口重叠、初始资金、成本模型、模拟画像逐维度比对；任一不同 → `comparable: false` + `reasons`（含维度与说明），`stats` 为 null。验证：e2e 用一条零成本回测 vs 一条纸面运行断言 `comparable: false` 且 `reasons[0].dimension == "cost_model"`。
- [x] 4.3 可比时返回双方基线 + 扩展统计与逐字段差值（`other - self`）。验证：e2e 构造同窗口同画像的两条回测，断言 `comparable: true`、`reasons == []`、`stats.diff.total_return` 与手算一致。
- [x] 4.4 重叠交易日 < 2 → 不可比，原因指明重叠区间与所需下限。验证：e2e 用不重叠窗口断言该原因出现且 `stats is null`。

## 5. 前端与前端测试

- [x] 5.1 `web/src/components/RunHistory.tsx`：三列在 `stats` 为空时显示"-"（现状），有值时正常显示；不回退既有回测显示。验证：`npm run test` 中 RunHistory 相关用例通过。
- [x] 5.2 策略详情页新增"与回测比较"入口：列出同策略的其它 run，点击后调比较端点；不可比时**显示原因清单**，不显示差值。验证：`web/e2e/strategy-backtest.spec.ts` 扩一条，断言不可比时页面出现原因文本且无差数字段。

## 6. 端到端验收

- [ ] 6.1 在测试环境对一条真实策略跑「回测 → 起纸面运行 → 停 → 比较」全链，截图/日志保留不可比原因与可比差值两种形态。验证：`docs/` 下留一份验收记录（真实数据，非桩）。
- [x] 6.2 `openspec validate add-paper-run-metrics --strict` 通过。验证：命令退出码 0。

## Workflow follow-up

- 实施完成后按 OpenSpec 流程 sync specs 并 archive；归档前确认三处 delta 都已落入主 spec。
