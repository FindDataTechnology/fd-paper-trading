# Design

## Context

- `strategy/runner.py`（纸面运行）从不写 `run.stats`；`api/routes/strategies.py` 的六个 `run.stats =` 赋值全在回测分支。
- 回测已有一套头条统计（`strategy/backtester.py::compute_stats`）与扩展统计（`strategy/extended_stats.py`，走 fd-quant 计算面）。
- 纸面运行已经逐日写 `strategy_equity_points`（`_snapshot_equity`），即统计所需的曲线**已经在库里**，只是没人读。
- `strategy_runs.stats` 与 `params` 都是既有列（JSONB），**无需迁移**。
- 跨仓可比的既有纪律在 `scripts/compare_backtest_fingerprints.py`：不可比是**数据**（`comparable: false` + 原因），不是省略。

## Goals / Non-Goals

**Goals:**
- 纸面运行停止后有可读的头条统计，口径与回测同名同义。
- 任何一次运行都留下模拟画像，使"能不能比"成为可判定的问题。
- 一次比较读端点，不可比时给出逐条原因且不输出差值。

**Non-Goals:**
- 不改任何成交/记账语义（成本与 T+1 属 change B）。
- 不做参数扫描/窗口重切；比较只在既有两 run 的既有窗口内进行。
- 不实现纸面 `win_rate`（需要按 run 重放成本基础以识别"回合"，留给后续；如实记 null + 原因）。

## Decisions

**D1：期初权益 = 第一个权益点，不新增列。**
`_snapshot_equity` 已在该运行首个处理 bar 上写点；把该点当作基准。替代方案是加 `opening_equity` 列——多一列只为存一个已在曲线里的数，且两条数据源会漂移。首个权益点同时解决"账户开户余额 ≠ 运行起点"（账户可能有先前持仓）。

**D2：统计函数抽到一处，回测与纸面共用。**
新增 `strategy/run_stats.py::headline_stats(equity: list[Decimal], baseline: Decimal, trade_count: int)`，从 `backtester.compute_stats` 抽走纯算术部分（收益/回撤/Sharpe/final）。回测改为调用它并补上自己的 `win_rate`（它的 `_Portfolio` 有回合计数）；纸面调用它并传 run 的成交笔数。抽一处的理由与 `risk_overlay.py` 相同：第二份实现必然漂移，而漂移的那份没人重读。

**D3：纸面 `trade_count` 取该运行归因成交的笔数。**
`Order.strategy_run_id` 既有且已索引，按 run 取 orders → fills 计数。不用"订单数"——一个订单可能部分成交或未成交，订单数会把"挂过单"算成"交易过"。

**D4：扩展统计复用 `extended_stats()` 的既有"不足两点则报缺"行为。**
不新增降级路径；计算面不可用时年化量为 null + 原因（既有行为），基线统计照常给。

**D5：画像存 `run.params["simulation_profile"]`，不加列。**
形状：`{"cost": {"mode": "none" | "modeled", **params}, "lot": "whole_share", "settlement": "T+0" | "T+1", "price_world": <provider source id or "unknown">}`。创建时写一次，之后不重算。回测从请求的 `cost_model` 取值（缺省 → `{"mode": "none"}`，即"零成本，已建模"）；纸面当前恒为 none / T+0（change B 落地后改 T+1）。

**D6：比较端点 `GET /api/v1/strategy-runs/{run_id}/comparison/{other_run_id}`。**
两个 run 都必须属于当前用户、同一 `strategy_id`，否则 400（不是 404——资源都找到了，是关系不成立）。窗口：回测取 `params.start/end`；纸面取首个与末个权益点的日期。重叠交易日 < 2 → 不可比（计算面年化需要 ≥2 点，与 D4 同源）。

**D7：不可比时不返回 `stats`。**
`stats: None` 与 `reasons: [...]` 同现；`comparable: true` 时 `reasons: []` 且 `stats` 含双方值与差值。`stats.diff` 的每个字段都是 `other - self`，且仅在可比时存在。

## Risks / Trade-offs

- [纸面 run 的权益曲线含非交易日的重复日期？] → `strategy_equity_points` 有 `(run_id, date)` 唯一约束，天然去重；统计按稀疏日期序列计算，与回测一致。
- [Sharpe 在点数很少时极不稳定] → 沿用既有实现（回测同病），不在此 change 引入新口径；`extended_stats` 已对 <2 点报缺。
- [画像写进 `params` 后，旧运行的 `params` 无此键] → 正是"未建模"的合法表示，比较时按不同处理（D5 + spec）。
- [纸面 run 停止后不再有统计，若不慎重启则统计过期] → 统计与 `stopped_at` 同事务写入；恢复逻辑（`recover`）只接管 ACTIVE run，不会重算终态。

## Migration Plan

无 schema 变更。部署后：
1. 新建/重启的运行开始写画像；历史运行保持"未建模"。
2. 纸面运行在下一次停止/出错时首次产出统计；已停止的历史 run 不回填（回填需要重放权益曲线，且曲线已存在——可作为后续一次性脚本，不在本 change）。
3. 回滚：端点与写入都是新增，删列无用；回退镜像即可，`stats` 多余内容不影响旧代码。

## Open Questions

- 纸面 `win_rate` 是否值得按 run 重放成交回合实现？（不阻塞本 change；当前 null + 原因。）
