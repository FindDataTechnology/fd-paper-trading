#!/usr/bin/env python3
"""2.3 real-engine execution: top-N equal-weight targets through replay_weights.

Separate from the probe script so the acceptance evidence is one clean run:

    FD_OPEN_DATA_DSN=$FD_OPEN_DATA_DSN FACTOR_TOP_CODES=000908,002553,... \
      uv run python scripts/weight_backtest_topN.py \
      > ../openspec/changes/add-trading-real-data-closeout/reports/weight-backtest-topN.txt
"""

import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fd_paper_trading.marketdata.astock import AstockMarketDataProvider  # noqa: E402
from fd_paper_trading.strategy.spec import Rebalance, WeightSpec  # noqa: E402
from fd_paper_trading.strategy.weight_backtester import replay_weights  # noqa: E402

END = date(2026, 7, 10)  # the astock_daily anchor (data ends there)


async def main() -> int:
    dsn = os.environ.get("FD_OPEN_DATA_DSN") or os.environ.get("ASTOCK_DSN")
    codes = [c for c in os.environ.get("FACTOR_TOP_CODES", "").split(",") if c]
    decision = os.environ.get("FACTOR_DECISION_DATE", "2025-09-30")
    if not dsn or len(codes) < 5:
        print("FAIL: need DSN and >= 5 codes (FACTOR_TOP_CODES)")
        return 2
    provider = AstockMarketDataProvider(dsn=dsn)
    start = (date.fromisoformat(decision) - timedelta(days=7)).isoformat()
    bars_by_symbol = {}
    for code in codes[:10]:
        b = await provider.get_daily_bars(code, start, END.isoformat())
        if len(b) >= 5:
            bars_by_symbol[code] = b
    universe = [c for c in codes[:10] if c in bars_by_symbol]
    n = len(universe)
    if n < 5:
        print(f"FAIL: only {n} names have bars >= 5")
        return 1
    w = round(0.9 / n, 4)
    weights = {c: w for c in universe}
    axis = sorted({
        datetime.fromtimestamp(b.ts, tz=timezone.utc).date().isoformat()
        for c in universe for b in bars_by_symbol[c]
    })
    rebalance_dates = [axis[i] for i in range(0, len(axis), 10)]
    spec = WeightSpec(
        kind="weight", universe=universe,
        rebalance=Rebalance(every_n_bars=10),
        weights_series={d: dict(weights) for d in rebalance_dates},
    )
    print(f"== weight-mode backtest REAL ENGINE (top{n} equal-weight {w}) ==")
    print(f"   universe: {universe}")
    print(f"   window: {start}..{END} (decision {decision}, rebalances every 10 bars)")
    print(f"   rebalance dates: {len(rebalance_dates)}")
    result = replay_weights(spec, bars_by_symbol, Decimal("100000"))
    # 指令数 ≠ 成交数：`snapshots[*].instructions` 含 `skipped=True`（停牌/无价）
    # 与数量被约束压到 0 的项，把它们算成"成交"会让闸在零成交时照样 PASS。
    # spec 要的是「成交数 > 0」，成交数只有一个权威来源：`stats["trade_count"]`
    # （engine 侧 = 非 skipped 且 quantity > 0 的指令数）。两个数都打印，
    # 闸只认后者。
    instructions = sum(len(s.instructions) for s in result.snapshots)
    skipped = sum(1 for s in result.snapshots for i in s.instructions if i.skipped)
    filled = int(result.stats.get("trade_count") or 0)
    assert filled <= instructions, (
        f"trade_count={filled} 超过指令数 {instructions}：统计口径不可信"
    )
    print(f"   status: COMPLETED, instructions={instructions} "
          f"(skipped={skipped}), filled={filled}, "
          f"final_equity={result.stats.get('final_equity')}")
    print(f"   stats: {result.stats}")
    for s in result.snapshots[:3]:
        print(f"   snapshot {s.day}: target={dict(list(s.target_weights.items())[:3])}"
              f"{' effective=' + str(dict(list(s.effective_weights.items())[:3])) if s.effective_weights else ''}"
              f" instructions={len(s.instructions)}")
    ok = filled > 0
    print(f"{'PASS' if ok else 'FAIL'}: weight backtest COMPLETED with "
          f"filled trades > 0 (filled={filled}, instructions={instructions})")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
