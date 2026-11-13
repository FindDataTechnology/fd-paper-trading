#!/usr/bin/env python3
"""Stop-order overlay acceptance evidence (tasks 4.1/4.2).

Two checks, both deterministic and recorded:

1. **Bar-overlay trigger list on a real A-share series** (600036 through the
   astock provider): replay the same window with and without a 5% stop and
   report where the overlay fired — the spec's "two runs must show one had
   stops" provenance, with real gap behaviour (fills at the worse of stop
   level and open).
2. **Backtest/live parity under the tick=bar replay protocol**: the same
   window driven through the quote-side overlay (the live engine's path,
   quoting the bar's low/high as the two quote sides) must fire on exactly
   the same bars as the bar-side overlay (the backtest path).

Writes a byte-stable report. Usage (repo root):

    FD_OPEN_DATA_DSN=$FD_OPEN_DATA_DSN \
      uv run python scripts/overlay_acceptance_astock.py \
      > openspec/changes/stop-order-execution/reports/overlay-acceptance.txt
"""

import asyncio
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

sys.path.insert(0, os.path.dirname(__file__))  # 同目录的 _evidence_source
from _evidence_source import EvidenceSourceError, open_provider  # noqa: E402
from fd_paper_trading.strategy.backtester import replay, build_evaluator  # noqa: E402
from fd_paper_trading.strategy.risk_overlay import (  # noqa: E402
    RiskConfig,
    evaluate_bar_overlay,
    evaluate_quote_overlay,
)
from fd_paper_trading.strategy.spec import TemplateSpec  # noqa: E402


async def main() -> int:
    try:
        p = open_provider()
    except EvidenceSourceError as exc:
        print(f"FAIL: {exc}")
        return 2
    bars = await p.get_daily_bars("600036", "2025-07-01", "2026-07-10")
    print(f"series: 600036 {len(bars)} bars 2025-07-01..2026-07-10 (astock_daily, forward)")
    fails = []

    def check(name, ok, detail=""):
        print(f"{'PASS' if ok else 'FAIL'}: {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            fails.append(name)

    spec = TemplateSpec(kind="template", symbol="600036", template="sma_cross",
                        params={"fast": 10, "slow": 60, "quantity": 100})
    evaluator = build_evaluator(spec)

    # ---- 1. with/without stop: same strategy, attributable difference ----
    plain = await replay(bars, build_evaluator(spec), Decimal("100000"))
    risk = RiskConfig(stop_loss_pct=Decimal("0.05"), trailing_stop_pct=Decimal("0.10"),
                      cooldown_bars=2)
    stopped = await replay(bars, build_evaluator(spec), Decimal("100000"), risk=risk)
    check("no-overlay run records nothing",
          plain.risk_config is None and plain.risk_triggers is None)
    check("overlay config recorded on the stopped run",
          stopped.risk_config == risk.to_dict())
    triggers = stopped.risk_triggers or []
    print(f"   stop-loss/trailing triggers on real series: {len(triggers)}")
    for t in triggers[:10]:
        print(f"   - {t['date']} {t['kind']} level={t['trigger_level']} "
              f"fill={t['fill_price']} ({t['detail']})")
    check("stopped-run stats differ from plain (or triggers empty and stats equal)",
          bool(triggers) != (stopped.stats == plain.stats),
          f"plain final={plain.stats['final_equity']} "
          f"stopped final={stopped.stats['final_equity']}")

    # ---- 2. backtest/live parity under tick=bar replay ----
    # Re-drive the window through the quote-side overlay, using each bar's
    # low/high as the two quote sides (the tick=bar replay protocol: one bar
    # per tick, the quote spans exactly the bar's range).
    from datetime import datetime, timezone

    def _d(bar):
        return datetime.fromtimestamp(bar.ts, tz=timezone.utc).date().isoformat()

    bar_fires, quote_fires = [], []
    held = False
    entry = None
    peak = None
    for bar in bars:
        t_bar = evaluate_bar_overlay(
            risk, "600036", "SELL", entry_price=entry, peak_value=peak,
            bar_open=bar.open, bar_close=bar.close,
            bar_low=bar.low, bar_high=bar.high,
        ) if held else None
        t_quote = evaluate_quote_overlay(
            risk, "600036", "SELL", entry_price=entry, peak_value=peak,
            quote_bid=bar.low, quote_ask=bar.high,
        ) if held else None
        if t_bar is not None:
            bar_fires.append(_d(bar))
        if t_quote is not None:
            quote_fires.append(_d(bar))
        if held and (t_bar is not None or t_quote is not None):
            held, entry, peak = False, None, None  # flat after a stop
        elif not held and bar.close > bar.open:
            held, entry, peak = True, bar.close, bar.close  # toy entry: buy on up bars
        elif held:
            peak = max(peak, bar.close)
    check("tick=bar replay: quote-side fires == bar-side fires",
          bar_fires == quote_fires,
          f"bar={bar_fires} quote={quote_fires}")

    print(f"\n{'ALL PASS' if not fails else f'{len(fails)} FAILED'}")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
