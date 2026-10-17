# Backtest coverage gate — real-data end-to-end (tasks 6.1 + 6.2)

> **本文件由脚本生成，请勿手改** — 重跑 `python3 fd-paper-trading/scripts/e2e_coverage_gate.py` 覆盖。

**数据**：`000001` 真实日线 485 点（akshare 东财→腾讯通道，经 `astock-price-on-demand` 的补数落库）。
**门禁**：`FdOpenDataMarketDataProvider.get_bars_with_coverage`，
覆盖度判定完全来自 DAAS 层（`missing` / `market_closed` 原样消费）。

## 1. 接受路径（6.1）：完整窗口放行

- bars：485
- missing：无
- market_closed（休市，放行）：6 段
- 指纹：`8ebf8ab43451c6c8597eb62d86fd345ae2d273c0b92600fa9430d7f77625974c`
- **接受：True**

## 2. 拒绝路径（6.2）：部分窗口点名缺失

- 把同一序列放进一个尾部多出一年的窗口（尾段无数据、未探明）
- bars：485
- missing 段数：1
- 首段缺失：{'start': '2026-10-17', 'end': '2027-10-17'}
- **拒绝依据成立：True**

回测侧对这种情况抛 `EvaluationError`（run → FAILED），错误含标的与缺失区间起止——由 `tests/unit/test_coverage_gate.py` 的桩路径验证。

## 3. 结论

- 完整窗口（含真实休市）放行且带指纹；部分窗口被点名拒绝。
- 门禁不自判交易日历：`missing`/`market_closed` 全部来自 DAAS 一层。
