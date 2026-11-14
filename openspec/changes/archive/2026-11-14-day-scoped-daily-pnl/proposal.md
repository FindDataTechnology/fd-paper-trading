## Why

`daily-pnl-tracking` 的三条需求都写着"今天"，而实现从头到尾没有"今天"这个概念：

- 规格：「Daily P&L is defined as the sum of realized P&L from fills that occurred **today**」
  实现（`web/src/components/DailyPnl.tsx`）：
  ```ts
  const realizedPnl = positions?.reduce((acc, p) => acc + Number(p.realized_pnl), 0) ?? 0;
  ```
  `position.realized_pnl` 是**终身**累计值（`execution/service.py`：`position.realized_pnl += realized`），
  于是这张卡的"已实现"是账户开户以来的全部实现盈亏。
- 规格：「reset the realized P&L component at the start of each trading day（默认 00:00 UTC）」
  实现：**没有任何地方按日归零**；后端唯一的"今天"是下单日限额（`count_for_account`），与盈亏无关。
- 规格：「unrealized P&L ... calculated from **the previous day's closing price**, not the original entry price」
  实现（`web/src/lib/format.ts`）：`unrealizedPnl(qty, avgPrice, mark) = qty * (mark - avgPrice)`
  —— 基准是**成本价**，所以"今日盈亏"实际是"持仓以来的盈亏"。

一个卡片同时错三处，而它叫 "Daily P&L"、就挂在下单页的 Dashboard 上
（`web/src/pages/Dashboard.tsx:66`），标题后面还缀着 "(indicative)" 当挡箭牌。

根因不是算错公式，而是**数据不够**：`fills` 行只有
`order_id/account_id/instrument_id/side/quantity/price/ts`，**没有逐笔已实现盈亏**，
所以"今天的已实现"根本无从求和；前端只能拿持仓行上的终身累计凑数。

## What Changes

- `fills.realized_pnl`（迁移 + 模型）：成交时把 `_update_position` 已经算好的那一笔实现盈亏
  落到成交行上。这是"今天"能被求和的前提。
- `GET /api/v1/accounts/{id}/daily-pnl`：服务端算三个数——今日已实现（当日成交行求和）、
  未实现（**以上一交易日收盘为基准** × 持仓量）、合计；返回 `day_start`/`as_of`/`baseline`，
  以及 `gaps`：缺上一交易日收盘、或缺当前报价的标的**逐条点名**，不静默拿成本价顶。
- 前端 `DailyPnl.tsx` 不再自算，改为渲染端点返回值（撤掉客户端的终身算法与 "(indicative)" 措辞）。
- 新增 `pnl_day_start_utc_hour`（默认 0 = 00:00 UTC），把"可配置日界"落成真配置。

## Capabilities

### Modified Capabilities

- `daily-pnl-tracking`：三条需求全部重述——计算口径（今日成交求和 + 以昨日收盘为基准的未实现 +
  缺口逐条点名）、展示（数值由服务端给出，前端只负责格式与配色）、日界（可配置、按 UTC 小时切）。
  六个既有场景名逐字保留，另加三个场景把新暴露的语义钉住。
