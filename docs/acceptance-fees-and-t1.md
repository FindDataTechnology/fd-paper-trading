# 验收记录：费用模型与 T+1 交收（`paper-fees-and-t1-settlement` 7.1 / 7.2）

> 真实数据、真跑命令，日期 2026-11-25。本文件不是桩算例。

## 0. 先修的两处脚本腐烂（跑之前必须修）

两个验收脚本都把价格库路径写死成 `~/code/DAAS/daas.db`，该目录 2026-11-25 已不存在
（只剩空 `~/code/docker`）。不修则两条 e2e 都**跑不起来**，此前「已完成」的结论无法复现。

修法：路径改为 `FD_PRICE_DB` 环境变量可覆盖，默认指向仓内工作区的 `finddata/DAAS/daas.db`。
改动文件：`scripts/e2e_weight_backtest.py`、`scripts/e2e_weight_paper.py`。

## 1. 零成本 vs 含费（7.1）

同一窗口、同一真实行情、同一策略，只切 `CostModel.enabled`：

```
.venv/bin/python /tmp/cost_compare.py     # FD_PRICE_DB=finddata/DAAS/daas.db
```

| 项 | 值 |
|---|---|
| 标的 | AAPL / MSFT / AMZN / GOOGL |
| 每 N 根调仓 | 10 |
| 初始资金 | 100000 |
| 窗口 | 每标的最近 200 根日线 |
| 成本模型 | commission 0.00025（双边）、stamp_tax 0.0005（仅卖）、slippage 5bps（双边） |
| **零成本 final_equity** | **111993.7639**（60 笔成交） |
| **含费 final_equity** | **111855.7972**（55 笔成交） |
| **差值** | **137.9667（-0.1232%）** |

**含费低于零成本**（`cost_lower: true`），符合判据。成交数 60→55 的差不是丢单：
含费后一笔买单按「含费后的现金要求」判定买不起而**跳过**，这正是 `_apply_signal`
注释所写的可承受性检查（否则模拟现金会转负，与实盘账本不可比）。

## 2. 纸面账本的三列费用（7.2 的库侧部分）

生产 `fd-prod/fd-paper-trading` 的 `fills` 表已有三列（`commission` / `stamp_tax` /
`slippage_cost`）。**但现存 5 行 fills 三列全为 NULL**（2026-10-15 ~ 11-14 写入，
早于本次费用模型上线），所以「一笔真实买单三列有值」这条**今天尚未在存量数据上成立**——
它们是在费用模型上线前落的。7.2 的完整验收（含费扣现金、当日卖被拒、次日卖成功）
需要的是一次**新**的纸面买单，按 §3 执行。

## 3. 7.2：三列费用 + T+1 的行为验证

### 3a. 已有 e2e 覆盖，2026-11-25 实跑 6/6 绿

```
.venv/bin/python -m pytest tests/e2e/test_t1_settlement.py -q
# ......  [100%]   6 passed
```

`tests/e2e/test_t1_settlement.py` 走的是真 HTTP 路径 + 真 `PaperExecutionService`，断言：
买入建 lot 且收佣金（`test_buy_creates_a_lot_and_charges_commission`）、当日卖被拒
（`test_same_day_sell_is_refused`）、次日卖成功（`test_sell_after_settlement_succeeds`）、
FIFO 已实现盈亏按 lot 成本、reset 清 lot。

### 3b. 生产真机这一条**当前不可能做成**（外部阻塞，非代码问题）

按 7.2 原文在生产环境下真买单，被服务自身的守卫拒绝：

```
POST /api/v1/orders {"symbol":"600519","side":"BUY","order_type":"MARKET","quantity":"1"}
→ 400 {"code":"stale_quote","message":"Quote for '600519' is 12.2 days old (max 4.0); refusing to trade"}
```

根因链（已核实到底）：

1. 生产 `MARKET_DATA_SOURCE_ID=astock_daily`，实为 federated_view → canonical
   `fd_open_data`（`100.64.0.3:30432`）。
2. provider 自证水位 `as_of = 2026-11-13`；`source_used='astock_daily'` 在
   `semantic_observations` 里**零行**（全库只有 `akshare` 910657 行，最新 2026-11-22）。
3. `astock_daily` 声明为 `license_state: legacy`（遗留只读表；增量走 gapfill + active_pool），
   而**没有任何定时 gapfill 作业**在跑（tencent 集群 40+ 个 `fd-industry-*` CronJob 里没有）。
4. ⇒ 价格自 11-13 起无新增；`stale_quote`（max 4 天）**正确地**拒绝了成交。这是行情供给断，
   不是费用/T+1 的缺陷。

**结论**：7.2 的下单行为由 3a 的 e2e 证明；生产真机那一条要等 A 股行情恢复增量后才有意义。
本条不是「待验收」，是「被上游阻塞」，已在 tasks 6.x/7.2 就地写明。