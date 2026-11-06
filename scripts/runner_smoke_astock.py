#!/usr/bin/env python3
"""Live-runner contract smoke on the astock provider (task 2.5 evidence).

Feeds a real A-share daily series (600036 through the astock provider) into
the SAME ``on_bar(ctx)`` evaluation contract the backtester and the live paper
runner share, and checks the runner's window/staleness helpers are untouched:

1. the template evaluator consumes the real series and emits signals;
2. ``_first_new_bar`` finds newer bars and returns None when nothing is newer
   (the existing tick-skip path);
3. the processed-day anchor advances to the last real bar.

Writes a deterministic, byte-stable report. Usage (repo root):

    FD_OPEN_DATA_DSN=$FD_OPEN_DATA_DSN \
      uv run python scripts/runner_smoke_astock.py \
      > openspec/changes/astock-price-channel/reports/runner-smoke.txt
"""

import asyncio
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from fd_paper_trading.marketdata.astock import AstockMarketDataProvider  # noqa: E402
from fd_paper_trading.strategy.backtester import build_evaluator  # noqa: E402
from fd_paper_trading.strategy.ctx import StrategyContext  # noqa: E402
from fd_paper_trading.strategy.spec import TemplateSpec  # noqa: E402


async def main() -> int:
    dsn = os.environ.get("FD_OPEN_DATA_DSN") or os.environ.get("ASTOCK_DSN")
    if not dsn:
        print("FAIL: no read-only DSN")
        return 2
    p = AstockMarketDataProvider(dsn=dsn)
    bars = await p.get_daily_bars("600036", "2025-07-01", "2026-07-10")
    fails = []

    def check(name, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}: {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            fails.append(name)

    check("real series served", len(bars) > 200, f"{len(bars)} bars")

    # 1) shared on_bar contract over the real window (sma_cross fast/slow)
    spec = TemplateSpec(kind="template", symbol="600036", template="sma_cross",
                        params={"fast": 10, "slow": 60, "quantity": 100})
    evaluator = build_evaluator(spec)

    from datetime import datetime, timezone

    def _bar_date_iso(bar) -> str:
        return datetime.fromtimestamp(bar.ts, tz=timezone.utc).date().isoformat()

    signals = []
    for i, bar in enumerate(bars):
        ctx = StrategyContext(bars[: i + 1], position=Decimal(0),
                              cash=Decimal("100000"))
        await evaluator.evaluate(ctx)
        if ctx.signals:
            signals.append((i, _bar_date_iso(bar), [str(s) for s in ctx.signals]))
    check("sma_cross emits signals on real data", len(signals) > 0,
          f"{len(signals)} signal bars from {len(bars)} bars")

    # 2) runner window helpers against provider-shaped bars
    from fd_paper_trading.strategy.runner import _bar_date, _first_new_bar, _latest_close

    idx = _first_new_bar(bars, "2026-06-30")
    check("_first_new_bar finds newer bar", idx is not None, f"index={idx}")
    check("_first_new_bar with nothing newer returns None (tick skip)",
          _first_new_bar(bars, "2099-01-01") is None)
    lc = _latest_close(bars)
    check("_latest_close reads the real last close", lc == bars[-1].close,
          f"close={lc}")

    # 3) processed-day anchor advances
    check("last processed day anchors at the final real bar",
          _bar_date(bars[-1]).isoformat() == "2026-07-10",
          f"last day={_bar_date(bars[-1]).isoformat()}")

    print()
    print("== signal sample (first 5) ==")
    for s in signals[:5]:
        print(s)
    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILED'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
