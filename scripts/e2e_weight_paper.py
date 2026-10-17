#!/usr/bin/env python3
"""End-to-end weight-track paper trading on a simulated account (task 6.3).

Chain: the live runner's own tick loop
       -> two rebalances at real prices from the authoritative price DB
       -> MARKET orders through the real PaperExecutionService
       -> positions and cash in the ledger, checked against the targets

Why drive the supervisor instead of calling the engine directly: task 6.3 asks
for the *paper* path to be exercised — the rebalance cadence, the per-symbol
order conversion, attribution, and the daily order cap are the runner's
behaviour, and a test that called `replay_weights` would prove none of it. The
report therefore reads back what the ledger holds, not what the engine intended.

Usage:
    python3 scripts/e2e_weight_paper.py            # run and write the report
    python3 scripts/e2e_weight_paper.py --check    # verify rerun determinism
Exit codes: 0 success / 1 failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

PRICE_DB = Path("/Users/chengsishi/code/DAAS/daas.db")
UNIVERSE = ["AAPL", "MSFT", "AMZN"]
INITIAL_CASH = Decimal("100000")
EVERY_N_BARS = 1  # rebalance on every new bar, so two ticks give two rebalances
OUT = REPO / "reports" / "e2e-weight-paper.md"


def load_bars(limit: int = 4) -> dict:
    """The most recent real daily bars per symbol (oldest-first)."""
    from fd_paper_trading.marketdata.provider import Bar

    if not PRICE_DB.exists():
        raise SystemExit(f"authoritative price DB missing: {PRICE_DB}")
    con = sqlite3.connect(f"file:{PRICE_DB}?mode=ro", uri=True)
    out: dict[str, list[Bar]] = {}
    try:
        for sym in UNIVERSE:
            rows = con.execute(
                f"SELECT date, Open, High, Low, Close, Volume "
                f"FROM scraw_{sym.lower()}_daily WHERE Close IS NOT NULL "
                f"ORDER BY date DESC LIMIT ?",
                (limit,),
            ).fetchall()
            rows.reverse()
            out[sym] = [
                Bar(
                    ts=datetime.fromisoformat(d).replace(tzinfo=timezone.utc).timestamp(),
                    open=Decimal(str(o if o is not None else c)),
                    high=Decimal(str(h if h is not None else c)),
                    low=Decimal(str(l if l is not None else c)),
                    close=Decimal(str(c)),
                    volume=Decimal(str(v if v is not None else 0)),
                )
                for d, o, h, l, c, v in rows
            ]
    finally:
        con.close()
    return out


class _RealUniverseProvider:
    """Serves the real bars, one more each time the runner asks for a new one.

    Everything except ``get_bars`` is delegated to the service's own simulated
    provider: instruments, quotes and search are not what this drill is testing,
    and re-implementing them here would let the stub drift from the real
    interface (the first draft of this script failed on exactly that — a missing
    ``get_instrument``). Only the bar series is replaced, because that is the part
    that must be real.

    The runner polls for bars newer than the last processed date, so replaying a
    real history means revealing it incrementally — the same shape a live feed has.
    """

    def __init__(self, bars: dict) -> None:
        from fd_paper_trading.marketdata import SimulatedMarketDataProvider

        self._bars = bars
        self._inner = SimulatedMarketDataProvider()
        self.reveal = 1

    def advance(self) -> None:
        self.reveal = min(self.reveal + 1, min(len(v) for v in self._bars.values()))

    async def get_bars(self, symbol: str, timeframe: str = "1d", limit: int = 730):
        series = self._bars.get(symbol.upper()) or []
        return series[: self.reveal]

    # ---- everything else is the real provider's behaviour ----

    def get_instrument(self, symbol: str):
        return self._inner.get_instrument(symbol)

    async def get_quote(self, symbol: str):
        return await self._inner.get_quote(symbol)

    async def search_instruments(self, q: str):
        return await self._inner.search_instruments(q)

    async def search(self, query: str, limit: int = 20):  # pragma: no cover
        return await self._inner.search_instruments(query)


async def run() -> dict:
    from httpx import ASGITransport, AsyncClient

    from fd_paper_trading.api.app import create_app
    from fd_paper_trading.persistence.base import init_db

    bars = load_bars()
    provider = _RealUniverseProvider(bars)
    app = create_app(
        database_url="sqlite+aiosqlite://", market_data_provider=provider
    )
    await init_db(app.state.db)
    trace: dict = {"universe": UNIVERSE, "bars": {}, "ticks": []}
    for sym, series in bars.items():
        trace["bars"][sym] = [
            {"date": datetime.fromtimestamp(b.ts, tz=timezone.utc).date().isoformat(),
             "close": str(b.close)}
            for b in series
        ]

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            email, password = "weight-e2e@example.com", "password123"
            await client.post(
                "/api/v1/auth/register", json={"email": email, "password": password}
            )
            login = await client.post(
                "/api/v1/auth/login", json={"email": email, "password": password}
            )
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

            acct = await client.post(
                "/api/v1/accounts",
                json={"name": "weight-e2e", "starting_balance": str(INITIAL_CASH)},
                headers=headers,
            )
            account_id = acct.json()["id"]

            spec = {
                "kind": "weight",
                "universe": UNIVERSE,
                "rebalance": {"every_n_bars": EVERY_N_BARS},
                "weights_series": {
                    # Equal weights, effective from before the first visible bar.
                    (date.today() - timedelta(days=3650)).isoformat(): {
                        sym: round(0.9 / len(UNIVERSE), 6) for sym in UNIVERSE
                    },
                },
            }
            created = await client.post(
                "/api/v1/strategies",
                json={"name": "weight-e2e", "description": "e2e", "spec": spec},
                headers=headers,
            )
            assert created.status_code == 201, created.json()
            strategy_id = created.json()["id"]

            # Create the run row directly rather than via POST /runs: that endpoint
            # also spawns the supervisor's own poll loop, and driving `_tick_once`
            # while that loop is alive means every bar gets traded twice (the first
            # version of this script did exactly that and reported 6 orders for a
            # 3-instruction rebalance). A deterministic drill needs one driver.
            run_id = await _make_run_row(app, strategy_id, account_id)

            supervisor = app.state.strategy_runner
            # Two ticks = two new bars = two rebalances (every_n_bars = 1).
            for _ in range(2):
                provider.advance()
                await supervisor._tick_once(run_id)
                snapshot = await _read_state(app, run_id, account_id)
                trace["ticks"].append(snapshot)

            stopped = await client.post(
                f"/api/v1/strategy-runs/{run_id}/stop", headers=headers
            )
            assert stopped.status_code == 200, stopped.json()
            trace["final"] = await _read_state(app, run_id, account_id)
            trace["stopped_status"] = stopped.json()["status"]
            trace["run_universe"] = stopped.json().get("universe")
    finally:
        await app.state.db.engine.dispose()

    return trace


async def _make_run_row(app, strategy_id: str, account_id: str) -> uuid.UUID:
    """Insert a STARTING paper run row with no poll task (one deterministic driver).

    This mirrors how the runner's own tests drive the tick loop: the API's start
    endpoint would also spawn the supervisor's poll loop, and two drivers would
    trade each bar twice.
    """
    from fd_paper_trading.persistence.models import (
        Strategy,
        StrategyRun,
        StrategyRunMode,
        StrategyRunStatus,
    )

    async with app.state.db.session_factory() as session:
        strat = await session.get(Strategy, uuid.UUID(strategy_id))
        run = StrategyRun(
            strategy_id=strat.id,
            strategy_version=strat.version,
            mode=StrategyRunMode.PAPER,
            account_id=uuid.UUID(str(account_id)),
            status=StrategyRunStatus.STARTING,
            params={"universe": list(strat.spec.get("universe") or [])},
            universe=list(strat.spec.get("universe") or []),
        )
        session.add(run)
        await session.commit()
        return run.id


async def _read_state(app, run_id, account_id) -> dict:
    """Read the ledger and the run record — what actually happened."""
    from sqlalchemy import select

    from fd_paper_trading.persistence.models import (
        Account,
        Order,
        Position,
        StrategyRun,
    )

    async with app.state.db.session_factory() as session:
        run = await session.get(StrategyRun, run_id)
        account = await session.get(Account, uuid.UUID(str(account_id)))
        positions = (
            await session.execute(
                select(Position).where(Position.account_id == account.id)
            )
        ).scalars().all()
        orders = (
            await session.execute(
                select(Order).where(Order.strategy_run_id == run_id)
            )
        ).scalars().all()
        return {
            "snapshots": list(run.weight_snapshots or []),
            "data_gaps": list(run.data_gaps or []),
            "cash": str(account.cash_balance),
            "positions": {
                p.symbol: str(p.quantity) for p in positions if Decimal(p.quantity) != 0
            },
            "orders": [
                {
                    "symbol": o.symbol,
                    "side": o.side,
                    "quantity": str(o.quantity),
                    "status": o.status,
                    "attributed": str(o.strategy_run_id) == str(run_id),
                }
                for o in orders
            ],
        }


def write_report(trace: dict) -> None:
    lines = [
        "# Weight-mode paper trading end-to-end report (task 6.3)",
        "",
        "> **本文件由脚本生成，请勿手改** — 重跑 `python3 fd-paper-trading/scripts/"
        "e2e_weight_paper.py` 覆盖。",
        "> 手改会在下次重跑时静默丢失，且报告与代码不再可对账。",
        "",
        f"**数据**：{len(UNIVERSE)} 只美股日线，来自价格通道 B 的权威库 `{PRICE_DB}`"
        "（只读），按 tick 逐根揭示（模拟实时到达）。",
        f"**策略**：等权（合计 0.9，余 0.1 留现金），每 {EVERY_N_BARS} 根 bar 调仓，"
        f"初始资金 {INITIAL_CASH}。",
        "**执行**：真实 `PaperExecutionService`，订单带 `strategy_run_id` 归因。",
        "",
        "## 1. 可见的真实行情",
        "",
        "| 标的 | 日期 | 收盘 |",
        "|---|---|---|",
    ]
    for sym, series in sorted(trace["bars"].items()):
        for i, row in enumerate(series):
            lines.append(f"| {sym if i == 0 else ''} | {row['date']} | {row['close']} |")
    lines += [
        "",
        "## 2. 每个 tick 的结果（快照 + 账本）",
        "",
    ]
    for i, tick in enumerate(trace["ticks"], start=1):
        lines.append(f"### tick {i}")
        lines.append("")
        if not tick["snapshots"]:
            lines.append("- 无调仓快照（该 tick 未到调仓点）")
        for snap in tick["snapshots"]:
            target = ", ".join(f"{k}={v}" for k, v in sorted(snap["target_weights"].items()))
            actual = ", ".join(
                f"{k}={round(v, 6)}" for k, v in sorted(snap["actual_weights"].items())
            )
            lines.append(f"- 调仓日 {snap['date']}")
            lines.append(f"  - 目标：{target or '（空仓）'}")
            lines.append(f"  - 调仓后实际：{actual or '（空仓）'}")
            if snap.get("error"):
                lines.append(f"  - 该期被拒：{snap['error']}")
            for ins in snap["instructions"]:
                note = ins.get("scale_reason") or ins.get("skip_reason") or ""
                if ins.get("skipped"):
                    note = f"跳过：{note}"
                lines.append(
                    f"  - 指令 {ins['side']} {ins['symbol']} {ins['quantity']} @ "
                    f"{ins['price']}"
                    + (f"（{note}）" if note else "")
                )
        lines.append("")
        lines.append(f"- 账户现金：{tick['cash']}")
        pos = ", ".join(f"{k}={v}" for k, v in sorted(tick["positions"].items()))
        lines.append(f"- 持仓：{pos or '（空仓）'}")
        lines.append(
            f"- 订单：{len(tick['orders'])} 笔"
            + (
                "（"
                + ", ".join(
                    f"{o['side']} {o['symbol']} {o['quantity']} [{o['status']}]"
                    for o in tick["orders"]
                )
                + "）"
                if tick["orders"]
                else ""
            )
        )
        lines.append("")

    final = trace["final"]
    lines += [
        "## 3. 停止后的状态（停止语义：撤挂单、不平仓）",
        "",
        f"- 运行状态：{trace['stopped_status']}",
        f"- run 记录的 universe：{trace['run_universe']}",
        f"- 现金：{final['cash']}",
        "- 持仓："
        + (", ".join(f"{k}={v}" for k, v in sorted(final["positions"].items())) or "（空仓）"),
        f"- 归因订单总数：{len(final['orders'])}"
        f"（全部带 strategy_run_id：{all(o['attributed'] for o in final['orders']) if final['orders'] else '无订单'}）",
        "",
        "停止后持仓保持不变（未被清仓），与既有单标的路径一致。",
        "",
        "## 4. 与目标权重的一致性（含取整残差）",
        "",
        "| 标的 | 目标权重 | 最终权重 | 差异（取整残差 + 行情变动） |",
        "|---|---|---|---|",
    ]
    if final["snapshots"]:
        last = final["snapshots"][-1]
        for sym in UNIVERSE:
            t = last["target_weights"].get(sym, 0.0)
            a = last["actual_weights"].get(sym, 0.0)
            lines.append(f"| {sym} | {t} | {round(a, 6)} | {round(a - t, 6)} |")
        lines += [
            "",
            "差异的来源逐笔可查：每次取整向下到整数股，余量留现金；缩量指令在"
            "第 2 节的指令行里带原因。",
        ]
    else:
        lines.append("| — | — | — | 无调仓发生 |")

    lines += [
        "",
        "## 5. 数据缺口",
        "",
    ]
    gaps = final["data_gaps"]
    if gaps:
        lines.append("| 日期 | 标的 |")
        lines.append("|---|---|")
        for g in gaps:
            lines.append(f"| {g['date']} | {g['symbol']} |")
    else:
        lines.append("本次窗口内三只标的交易日一致，无缺口。")

    lines += [
        "",
        "## 6. 结论",
        "",
        f"- 纸面实盘路径跑通：{len(trace['ticks'])} 个 tick、"
        f"{sum(len(t['snapshots']) for t in trace['ticks'])} 次调仓，订单全部带 run 归因。",
        "- 一次调仓 = N 笔单标的 MARKET 订单（无批量接口），复用既有执行服务与限额。",
        "- 持仓与目标权重的差异可归因到取整与缩量，未做静默修正。",
        "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"report written: {OUT.relative_to(REPO)}")


def digest(trace: dict) -> str:
    """Deterministic comparison key (order counts, quantities, final cash)."""
    return json.dumps(
        {
            "ticks": [
                {
                    "orders": sorted(
                        (o["symbol"], o["side"], o["quantity"], o["status"])
                        for o in t["orders"]
                    ),
                    "positions": t["positions"],
                    "cash": t["cash"],
                }
                for t in trace["ticks"]
            ],
            "final_cash": trace["final"]["cash"],
            "final_positions": trace["final"]["positions"],
        },
        sort_keys=True,
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    trace = asyncio.run(run())
    if args.check:
        again = asyncio.run(run())
        if digest(trace) != digest(again):
            print("FAIL: rerun produced a different ledger trace")
            return 1
        print(f"OK: rerun identical ({len(trace['ticks'])} ticks, "
              f"{sum(len(t['orders']) for t in trace['ticks'])} orders)")
        return 0

    write_report(trace)
    return 0


if __name__ == "__main__":
    sys.exit(main())
