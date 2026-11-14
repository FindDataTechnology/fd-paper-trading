## 1. 先量后改

- [x] 1.1 实测三处与规格相反：`DailyPnl.tsx` 求和的是终身 `position.realized_pnl`；`unrealizedPnl` 基准是 `avg_price`；后端唯一"今天"是下单日限额
- [x] 1.2 确认 `fills` 无逐笔已实现盈亏列（`id/order_id/account_id/instrument_id/side/quantity/price/ts`）⇒ "今日已实现"无从求和
- [x] 1.3 确认卡片确实被挂载（`pages/Dashboard.tsx:66`），不是死组件

## 2. 数据层

- [x] 2.1 迁移：`fills.realized_pnl`（`Numeric(20,8)`，`NOT NULL`，server_default `0`），down_revision 指向当前 head；模型同步加列
- [x] 2.2 `_apply_fill` 把 `_update_position` 返回的 `realized` 落到 `Fill(..., realized_pnl=realized)`
- [x] 2.3 `tests/unit/test_migrations_match_models.py` 保持绿（加字段必须同笔加迁移）

## 3. 端点

- [x] 3.1 `DailyPnlOut`：`day_start`、`as_of`、`baseline`、`realized_today`、`unrealized`、`daily_pnl`、`gaps[]`
- [x] 3.2 `GET /api/v1/accounts/{account_id}/daily-pnl`：今日成交求和 + 逐持仓以「严格早于今天的最后一根日线 close」为基准；无基准 → 排除并记 `no_previous_close`；无报价 → 按基线计 0 并记 `no_quote`
- [x] 3.3 `pnl_day_start_utc_hour`（默认 0）进 `Settings`，日界按它切

## 4. 前端

- [x] 4.1 `types.ts` + `hooks.ts` 接端点；`DailyPnl.tsx` 删掉本地终身算法与 "(indicative)"，改渲染服务端数
- [x] 4.2 有 `gaps` 时明确显示"不完整"并点名校验不到的标的
- [x] 4.3 `tests/unit/test_frontend_types_match_schemas.py` 的 `PAIRS` 增 `DailyPnlOut`

## 5. 判据（每条都能红）

- [x] 5.1 终身制泄漏：昨天实现 400、今天没成交 → 今日已实现 = 0（注入「改成读 position.realized_pnl」即红）
- [x] 5.2 基准是昨日收盘不是成本价：持仓 avg=100、昨日收盘=110、现价=120 → 未实现 = qty×10（注入「换成 avg_price」即红）
- [x] 5.3 无基准不冒充：缺昨日收盘 → 该标的不计入、出现在 `gaps`（注入「回落到 avg_price」即红）
- [x] 5.4 日界：时钟钉在 12:00、`pnl_day_start_utc_hour=9` 时，08:00 那笔不计入今日（注入「固定用 0 点」即红：850≠450）。用真实时钟会在 UTC 0 点整条 skip，那一刻判据等于不存在
- [x] 5.5 逐笔记账：卖出成交行的 `realized_pnl` 等于价差×数量（注入「不落列」即红）
- [x] 5.6 归属边界：窗口内 `realized_pnl IS NULL` 的成交不当 0，笔数走 `unattributed_fills`（注入「写死 0」即红）。归档后才发现规格第三条末句要求「端点须明说」——于是列改为可空（NULL=未记录、0=确实是零），模型/迁移/端点/前端类型同笔改

## 6. 收尾

- [x] 6.1 `pytest tests` 全绿；`cd web && npx tsc --noEmit && npm test`
- [x] 6.2 `openspec validate 2026-11-14-day-scoped-daily-pnl --strict`
- [x] 6.3 归档并核对 `Totals`：`daily-pnl-tracking` ~3（实测 `+ 0, ~ 3, - 0, → 0`）
- [x] 6.4 归档后 `openspec validate daily-pnl-tracking --type spec --strict`（valid）
- [ ] 6.5 提交推送；报告补附五十九
