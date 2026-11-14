## Context

`daily-pnl-tracking` 的三条需求写的都是"日"，而实现是一个纯前端算式，读的是**终身**状态：

```ts
// web/src/components/DailyPnl.tsx（改前）
const realizedPnl = positions?.reduce((acc, p) => acc + Number(p.realized_pnl), 0) ?? 0;
const mark = q ? mid(q.bid, q.ask) : Number(p.avg_price);      // 没报价就拿成本价当市价
acc + unrealizedPnl(p.quantity, p.avg_price, String(mark));     // 基准＝成本价
```

`position.realized_pnl` 是终身累计（`position.realized_pnl += realized`），
`unrealizedPnl` 的基准是成本价 ⇒ 卡片显示的是"开户以来"，不是"今天"。

数据层缺口是根因：`fills` 行没有逐笔已实现盈亏，所以"今日已实现"无从求和；
前端只能退到持仓行的终身字段。

## Goals / Non-Goals

**Goals**

- 三个数（今日已实现 / 未实现 / 合计）由**服务端**按明确口径算出，前端只渲染。
- 未实现以**上一交易日收盘**为基准；拿不到就如实报缺口，不拿成本价冒充。
- 日界可配（默认 00:00 UTC），且日界两侧的"已实现"确实不同。

**Non-Goals**

- 不做盘中 tick 级 P&L（本服务价格本身是日线收盘 + 半价差，"盘中"是假象）。
- 不提供历史区间 P&L / 净值曲线（那是 run/backtest 的 equity，不是账户日盈亏）。
- 不改 `position.realized_pnl` 的语义（终身累计仍是持仓视图要的值，只是不再是"日"的来源）。

## Decisions

### D1 逐笔已实现盈亏落到 `fills.realized_pnl`

`_apply_fill` 里 `_update_position(...)` 已经算好了这一笔的 `realized`
（`(price - old_avg) * closing * (±1)`），只是原先只加到持仓行上。加一列记住它，
"今日已实现"就变成一条带 `ts` 的求和，不再需要重放成本基础。

**备选**：按日重放成交算成本基础。否——同一件事已经有人算过一遍，重放会引入第二套
成本基础口径（这次缺陷的教训正是"同一件事实两处各自推断"）。

**迁移的连带要求**：本仓测试建表走 `create_all`、生产走 `alembic`，
`tests/unit/test_migrations_match_models.py` 会逐列比对——所以加字段必须同笔加迁移，
否则那道闸立刻红（这是它存在的理由，不是障碍）。

### D2 未实现以上一交易日收盘为基准，缺就报缺口

对每个持仓：取该标的日线，**日期严格早于今天**的最后一根的 `close` 作为基准；
`unrealized = qty * (mark - baseline)`，`mark` 取当前报价中价（无报价则用基线，贡献 0）。

缺口分两类逐条点名：`no_previous_close`（拿不到基准 ⇒ 该标的**不计入**未实现，
而不是拿成本价顶）与 `no_quote`（有基准无报价 ⇒ 按基线计 0 并点名）。
`daily_pnl` 仍等于今日已实现 + 已计入的未实现，`gaps` 让读者知道这个数是"能算的都算了"。

### D3 日界是一个 UTC 小时，默认 0

`pnl_day_start_utc_hour: int = 0`。日界之后（含）的成交计入今日已实现。
不引入时区库：全仓时间都是 UTC，把"本地日界"引进来只会制造第二个时间口径。

### D4 端点而不是继续在前端算

前端要"今日已实现"就必须有成交明细，而本仓没有 fills 列表端点；
即便加了，把口径放在浏览器里也意味着**同一个数在不同客户端可以不同**。
端点让口径只有一个实现，也让缺口能作为数据返回给 UI 显示。

## Risks / Trade-offs

- **每个持仓要多取一次日线**：`get_bars(symbol, "1d", 5)`，持仓数是账户级的个位数；
  且 provider 侧对同一 symbol 有缓存/单 SQL。若将来持仓上千，改成一次批量取。
- **历史成交行的 `realized_pnl` 回填**：迁移只加列（默认 0），**不回填**——
  历史行的逐笔实现盈亏在旧数据里根本不存在（只剩持仓行的累计值），
  回填只能靠重放，而那正是 D1 拒绝的第二套口径。后果：**日界之前的"今日已实现"**
  在升级当天偏低（当日已发生的卖出记 0）。这一点写进端点文档与报告，并在 tasks 里
  给出一个可选的补账配方（按 fills 时序重放到今日 00:00，仅一次性）。
- **`no_quote` 时按基线计 0**：这意味着休市/停更标的不制造虚假盈亏，但也意味着
  "今天涨了但没订阅报价"不会体现——`gaps` 会点名，UI 可提示。
