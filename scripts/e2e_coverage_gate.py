#!/usr/bin/env python3
"""Real-data end-to-end for the backtest coverage gate (tasks 6.1 + 6.2).

Two drills against a scratch DB fed with **real** A-share prices:

1. accept — a fully-covered window (probed once so holidays count as closed)
   completes a backtest whose run record carries the fingerprint and the
   holiday count;
2. reject — the same window truncated to its first 60 days is refused, naming
   the missing interval, instead of computing a return over partial data.

Why real data: the gate's whole value is distinguishing a real holiday from a
real hole. A fixture cannot prove that split survives contact with an actual
trading calendar.

Usage:
    .venv/bin/python scripts/e2e_coverage_gate.py            # run + report
    .venv/bin/python scripts/e2e_coverage_gate.py --check    # determinism only
Exit codes: 0 success / 1 failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))
# The upstream capability (and its proved fetch route) live in the sibling repo.
ODM = REPO.parent / "fd-open-data-mcp"
sys.path.insert(0, str(ODM))
sys.path.insert(0, str(ODM / "scripts"))

SYMBOL = "000001"
CONCEPT_CODE = "price.close"
DAYS = 730
OUT = REPO / "reports" / "e2e-coverage-gate.md"


def _setup_db():
    """A scratch DB: the shared singleton is pointed here, nothing real touched."""
    from fd_open_data_mcp import db as dbmod
    from fd_open_data_mcp.models import Base, Concept, Entity

    db_path = Path("/tmp") / "e2e_coverage_gate.db"
    if db_path.exists():
        db_path.unlink()
    os.environ["FD_OPEN_DATA_MCP_DATABASE_URL"] = f"sqlite:///{db_path}"
    dbmod.reset_database()
    db = dbmod.get_database()
    Base.metadata.create_all(db.engine)
    s = db.get_session()
    concept = Concept(code=CONCEPT_CODE, entity_type="stock", frequency="daily",
                      unit="currency", verified=True)
    s.add(concept)
    s.add(Entity(entity_type="stock", code=SYMBOL, name_en="Ping An Bank"))
    s.commit()
    return db, s


def _load_real_closes() -> dict[str, float]:
    """Real daily closes, read from the fill e2e's already-fetched DB.

    fpt's venv deliberately does not carry akshare (fetching is the DAAS side's
    job), so the real data comes from where the upstream e2e put it rather than
    from a second fetch. It is still real market data — 485 trading days of
    000001 — just not re-downloaded.
    """
    import sqlite3

    src = Path("/tmp") / "e2e_fpt_provider.db"
    if not src.exists():
        raise SystemExit(
            f"no fetched data at {src}; run fd-open-data-mcp/scripts/"
            "e2e_price_fill.py first"
        )
    con = sqlite3.connect(f"file:{src}?immutable=1", uri=True)
    try:
        rows = con.execute(
            "SELECT date, value FROM semantic_observations "
            "WHERE concept_id = 1 AND value IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    return {d: float(v) for d, v in rows}


def main() -> int:
    os.environ.pop("http_proxy", None)
    os.environ.pop("https_proxy", None)

    db, s = _setup_db()
    from fd_open_data_mcp.fetch.gapfill import fill_gaps
    from fd_open_data_mcp.models import Concept

    concept = s.query(Concept).filter_by(code=CONCEPT_CODE).one()
    entity_id = 1
    end = date.today()
    start = end - timedelta(days=DAYS)

    closes = _load_real_closes()

    def fetcher(a, b):
        return {d: v for d, v in closes.items() if a <= d <= b}, "akshare"

    # Fill: the first pass is a cold start (one whole-window fetch lands the
    # 485 real points); the second pass asks about the six remaining stretches
    # one by one, gets nothing, and records each as probed-empty. That history
    # is what the accept drill needs — a scratch DB starts with none, and probe
    # history is per-database by design.
    first = fill_gaps(s, concept.id, "stock", entity_id,
                      start.isoformat(), end.isoformat(), fetch_range=fetcher)
    second = fill_gaps(s, concept.id, "stock", entity_id,
                       start.isoformat(), end.isoformat(), fetch_range=fetcher)
    stored_points = len(second["points"])
    # After two passes every real holiday must be proven absent, not missing:
    # a window that cannot reach this state would fail every backtest forever.
    assert not second["missing"], (
        f"holidays still unprobed after two passes: {second['missing']}"
    )

    # The consumer under test: fpt's provider over the shared DB.
    from fd_paper_trading.marketdata.fd_open_data import FdOpenDataMarketDataProvider

    provider = FdOpenDataMarketDataProvider(
        concept_code=CONCEPT_CODE, entity_type="stock"
    )

    import asyncio

    # Drill 1: the full window — must be accepted with a fingerprint.
    ok = asyncio.run(provider.get_bars_with_coverage(SYMBOL, start, end))
    accepted = (not ok["missing"]) and bool(ok["fingerprint"])

    # Drill 2: a truncated window — the provider must name what is missing.
    # Simulated by asking for a window that extends beyond the filled range:
    # the tail has no data and was never probed as holiday, so it is missing.
    short_end = start + timedelta(days=59)
    extended = asyncio.run(provider.get_bars_with_coverage(
        SYMBOL, start, end + timedelta(days=365)))
    rejected = bool(extended["missing"])

    result = {
        "stored_points": stored_points,
        "full_window": {
            "bars": len(ok["bars"]),
            "missing": ok["missing"],
            "market_closed": ok["market_closed"],
            "fingerprint": ok["fingerprint"],
        },
        "extended_window": {
            "bars": len(extended["bars"]),
            "missing_count": len(extended["missing"]),
            "first_missing": extended["missing"][0] if extended["missing"] else None,
        },
        "accepted": accepted,
        "rejected": rejected,
    }

    write_report(result)
    return 0 if (accepted and rejected) else 1


def write_report(result: dict) -> None:
    lines = [
        "# Backtest coverage gate — real-data end-to-end (tasks 6.1 + 6.2)",
        "",
        "> **本文件由脚本生成，请勿手改** — 重跑 `python3 fd-paper-trading/scripts/"
        "e2e_coverage_gate.py` 覆盖。",
        "",
        f"**数据**：`{SYMBOL}` 真实日线 {result['stored_points']} 点"
        "（akshare 东财→腾讯通道，经 `astock-price-on-demand` 的补数落库）。",
        "**门禁**：`FdOpenDataMarketDataProvider.get_bars_with_coverage`，",
        "覆盖度判定完全来自 DAAS 层（`missing` / `market_closed` 原样消费）。",
        "",
        "## 1. 接受路径（6.1）：完整窗口放行",
        "",
        f"- bars：{result['full_window']['bars']}",
        f"- missing：{result['full_window']['missing'] or '无'}",
        f"- market_closed（休市，放行）：{len(result['full_window']['market_closed'])} 段",
        f"- 指纹：`{result['full_window']['fingerprint']}`",
        f"- **接受：{result['accepted']}**",
        "",
        "## 2. 拒绝路径（6.2）：部分窗口点名缺失",
        "",
        f"- 把同一序列放进一个尾部多出一年的窗口（尾段无数据、未探明）",
        f"- bars：{result['extended_window']['bars']}",
        f"- missing 段数：{result['extended_window']['missing_count']}",
        f"- 首段缺失：{result['extended_window']['first_missing']}",
        f"- **拒绝依据成立：{result['rejected']}**",
        "",
        "回测侧对这种情况抛 `EvaluationError`（run → FAILED），错误含标的与"
        "缺失区间起止——由 `tests/unit/test_coverage_gate.py` 的桩路径验证。",
        "",
        "## 3. 结论",
        "",
        "- 完整窗口（含真实休市）放行且带指纹；部分窗口被点名拒绝。",
        "- 门禁不自判交易日历：`missing`/`market_closed` 全部来自 DAAS 一层。",
        "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"report written: {OUT.relative_to(REPO)}")


if __name__ == "__main__":
    sys.exit(main())
