#!/usr/bin/env python3
"""One-shot real-store smoke for the astock provider (task 1.1 evidence).

Reads the platform federated view through the provider's production reader
with the read-only DSN, then exercises the full read surface: entity
resolution, pivot bars, quote derivation, coverage report, search. Prints a
compact report; exits non-zero on any assertion failure. Writes nothing.

Usage (from the fd-paper-trading repo root):

    FD_PAPER_TRADING_ASTOCK_DSN=$FD_OPEN_DATA_DSN \
      uv run python scripts/smoke_astock_provider.py

(The DSN lives in ~/.config/finddata/dashboards.env — same read-only
credential the dashboards line uses.)
"""

import asyncio
import os
import sys
import time
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

sys.path.insert(0, os.path.dirname(__file__))  # 同目录的 _evidence_source
from _evidence_source import EvidenceSourceError, open_provider  # noqa: E402

from fd_paper_trading._vendor.fd_quant.contracts.models import (  # noqa: E402
    PRICE_ADJUSTMENTS,
)


async def main() -> int:
    try:
        p = open_provider()
    except EvidenceSourceError as exc:
        print(f"FAIL: {exc}")
        return 2
    fails = 0

    def check(name, ok, detail=""):
        nonlocal fails
        print(f"{'PASS' if ok else 'FAIL'}: {name}{(' — ' + detail) if detail else ''}")
        if not ok:
            fails += 1

    # 1. entity resolution + pivot bars (dual spelling)
    bars = await p.get_daily_bars("600036", "2026-06-01", "2026-07-31")
    check("get_daily_bars(600036) returns real OHLC", len(bars) > 20,
          f"{len(bars)} bars, last close={bars[-1].close if bars else '-'}")
    check("OHLC internally consistent",
          all(b.high >= max(b.open, b.close, b.low) for b in bars))
    bars2 = await p.get_daily_bars("600036.SH", "2026-06-01", "2026-07-31")
    check("dual spelling 600036.SH same", len(bars2) == len(bars))

    # 2. source pin: every bar came from astock_daily (probe the raw view too)
    pin_rows = await p._fetch(
        "select distinct source_used from semantic_observations_all "
        "where entity_id = (select id from entities where code='600036' "
        "and entity_type='stock' limit 1) and concept_id=234 "
        "and granularity='day' and date>='2026-06-01'",
    )
    check("store holds multiple sources for the symbol (pin matters)",
          len(pin_rows) >= 1, f"sources seen raw: {[r['source_used'] for r in pin_rows]}")
    check("provider source arg is pinned", p._source == "astock_daily")

    # 3. coverage with adjusted caliber
    end = date(2026, 7, 10)
    start = end - timedelta(days=120)
    cov = await p.get_bars_with_coverage("600036", start.isoformat(), end.isoformat())
    # 口径跟**声明**比，不自带一份字面量。`8ada860` 把 coverage 的口径改成读
    # `declarations.yaml`（astock_daily 声明是 qfq），这条断言原本写死 "forward"
    # （旧的模块常量），于是 2026-11-13 修好取源后第一次真跑就假红。
    # 检查器自己抄一份真值，就是下一个"两套口径"。
    declared = p._declared_adjustment()
    check("coverage caliber matches the declaration",
          cov.get("adjusted") == declared,
          f"adjusted={cov.get('adjusted')}, declared={declared}")
    check("coverage caliber is a contract word",
          cov.get("adjusted") in PRICE_ADJUSTMENTS,
          f"adjusted={cov.get('adjusted')}")
    check("coverage has fingerprint", bool(cov.get("fingerprint")))
    print(f"   coverage: {len(cov['bars'])} bars, missing={len(cov['missing'])}, "
          f"closed={len(cov['market_closed'])}, fill_failed={cov['fill_failed']}")
    check("clean window not falsely rejected", not cov["fill_failed"]
          and cov["missing"] == [],
          f"missing intervals: {cov['missing'][:3]}")

    # 4. search + instrument
    hits = await p.search_instruments("招商")
    check("name search finds entity", any(h.symbol == "600036" for h in hits),
          f"{len(hits)} hits: {[h.symbol for h in hits[:5]]}")
    hits2 = await p.search_instruments("000333")
    check("code search finds entity", any(h.symbol == "000333" for h in hits2))
    inst = p.get_instrument("600519.SH")
    check("instrument syntactic", inst is not None and inst.venue == "XSHG")

    # 5. quote (staleness is expected until the crawl restarts: data ends
    #    2026-07-10, today is later) → None on a stale series is CORRECT.
    q = await p.get_quote("600036")
    check("quote honesty (None when no close in lookback)", q is None or (q.bid < q.ask),
          f"quote={None if q is None else (str(q.bid), str(q.ask))}")

    # 6. 730-day coverage must complete in a bounded time (basket calendar,
    #    design D4): a whole-market scan timed out (>200 s) in the spike.
    end730 = date(2026, 7, 10)
    start730 = end730 - timedelta(days=730)
    t0 = time.time()
    cov730 = await p.get_bars_with_coverage("600036", start730.isoformat(), end730.isoformat())
    el = time.time() - t0
    check("730d coverage in bounded time", el < 60,
          f"{el:.1f}s bars={len(cov730['bars'])} missing={len(cov730['missing'])} "
          f"closed={len(cov730['market_closed'])}")
    check("730d dense symbol not falsely rejected", cov730["missing"] == [])

    # 7. a stale symbol (astock_daily ends 2015) reads as an uncovered
    #    window, not as "all holidays".
    cov_stale = await p.get_bars_with_coverage("600519", "2025-01-01", "2026-07-10")
    check("stale symbol honesty: whole window missing",
          len(cov_stale["bars"]) == 0 and len(cov_stale["missing"]) == 1,
          f"bars={len(cov_stale['bars'])} missing={cov_stale['missing'][:1]}")

    # 8. index family reality check (spike evidence for task 3.1)
    idx_rows = await p._fetch(
        "select count(*) as n from semantic_observations_all "
        "where entity_id in (9676, 10263) and granularity='day'")
    check("index entities currently carry zero observations (spike evidence)",
          int(idx_rows[0]["n"]) == 0, f"rows={idx_rows[0]['n']}")

    print(f"\n{'ALL PASS' if fails == 0 else f'{fails} CHECK(S) FAILED'}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
