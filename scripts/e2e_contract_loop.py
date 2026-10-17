#!/usr/bin/env python3
"""Contract closed loop: a pack's TargetWeights instance drives a real backtest
(strategy-weight-mode task 6.4).

Chain: a ``TargetWeights`` instance built the way a pack would publish it
       -> the API's spec validation (which routes weights through the contract)
       -> a real multi-symbol backtest on real prices
       -> a readable result: snapshots, instruction detail, stats

Why this is a separate drill from task 6.2: 6.2 exercises the engine, this
exercises the *handoff*. The whole point of the contract layer is that a pack can
emit a weight vector and the engine can consume it without either side
re-implementing the other's rules — so the instance here is validated exactly as
a pack's output would be (schema_version, universe, cash_floor and producer all
present), never reduced to a bare mapping before it reaches the engine.

The `cash_floor` is the interesting field: it is contract-only (a bare mapping
cannot express it), so this drill also shows the floor actually binding.

Usage:
    python3 scripts/e2e_contract_loop.py            # run and write the report
    python3 scripts/e2e_contract_loop.py --check    # verify rerun determinism
Exit codes: 0 success / 1 failure.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

PRICE_DB = Path("/Users/chengsishi/code/DAAS/daas.db")
UNIVERSE = ["AAPL", "MSFT", "AMZN"]
INITIAL_CASH = Decimal("200000")
EVERY_N_BARS = 20
WINDOW = 120
OUT = REPO / "reports" / "e2e-contract-loop.md"

#: A pack's published output. `producer` names the pack and skill, as a real one
#: would, and `cash_floor` states the residual the pack wants left in cash.
PACK_INSTANCE = {
    "schema_version": "1",
    "as_of": "2000-01-01",
    "universe": UNIVERSE,
    "weights": {"AAPL": 0.4, "MSFT": 0.3, "AMZN": 0.1},
    "cash_floor": 0.2,
    "constraints": {"max_weight_per_symbol": 0.5},
    "evidence": [
        {
            "evidence_id": "ev-1",
            "label": "trailing 6m momentum ranking",
            "value": "AAPL>MSFT>AMZN",
            "unit": None,
            "as_of": "2000-01-01",
            "source": "pack-strategy-lab/momentum",
        }
    ],
    "producer": {"pack": "pack-strategy-lab", "skill": "portfolio-weights"},
}


def load_bars() -> dict:
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
                (WINDOW,),
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


def run() -> dict:
    from fd_paper_trading._vendor.fd_quant import validate_target_weights
    from fd_paper_trading.strategy.spec import validate_spec
    from fd_paper_trading.strategy.weight_backtester import replay_weights

    # 1. The instance is validated as published (the contract layer judges it).
    contract_result = validate_target_weights(PACK_INSTANCE)
    if not contract_result["valid"]:
        raise SystemExit(f"the pack instance is invalid: {contract_result['errors']}")

    # 2. The spec carries the instance verbatim as its single period.
    spec = validate_spec(
        {
            "kind": "weight",
            "universe": UNIVERSE,
            "rebalance": {"every_n_bars": EVERY_N_BARS},
            "weights_series": {PACK_INSTANCE["as_of"]: PACK_INSTANCE},
        }
    )
    # 3. The engine consumes it: weights and cash floor both survive the handoff.
    assert spec.weights_series[PACK_INSTANCE["as_of"]] == PACK_INSTANCE["weights"]
    assert spec.cash_floors[PACK_INSTANCE["as_of"]] == PACK_INSTANCE["cash_floor"]

    bars = load_bars()
    result = replay_weights(spec, bars, INITIAL_CASH)
    return {
        "contract_result": contract_result,
        "spec": spec,
        "result": result,
        "bars": {
            sym: [(datetime.fromtimestamp(b.ts, tz=timezone.utc).date().isoformat(),
                   str(b.close)) for b in series]
            for sym, series in bars.items()
        },
    }


def digest(run_data: dict) -> str:
    res = run_data["result"]
    return json.dumps(
        {
            "stats": res.stats,
            "equity_last": str(res.equity_points[-1][1]),
            "snapshots": [
                {
                    "date": s.day.isoformat(),
                    "target": s.target_weights,
                    "actual": {k: round(v, 8) for k, v in s.actual_weights.items()},
                    "instructions": [
                        (i.symbol, i.side, str(i.quantity), str(i.price))
                        for i in s.instructions
                    ],
                }
                for s in res.snapshots
            ],
        },
        sort_keys=True,
    )


def write_report(run_data: dict) -> None:
    res = run_data["result"]
    spec = run_data["spec"]
    lines = [
        "# Contract closed-loop report (task 6.4)",
        "",
        "> **本文件由脚本生成，请勿手改** — 重跑 `python3 fd-paper-trading/scripts/"
        "e2e_contract_loop.py` 覆盖。",
        "> 手改会在下次重跑时静默丢失，且报告与代码不再可对账。",
        "",
        "**链路**：pack 形态的 `TargetWeights` 实例 → 契约层校验 → 引擎消费"
        "（spec 校验 + 真实价格回测）→ 可解读的结果。",
        "",
        "## 1. 输入：pack 形态的契约实例",
        "",
        "```json",
        json.dumps(PACK_INSTANCE, indent=2, ensure_ascii=False),
        "```",
        "",
        "## 2. 契约层校验",
        "",
        f"- 结论：`valid = {run_data['contract_result']['valid']}`",
        f"- 错误数：{len(run_data['contract_result']['errors'])}",
        "",
        "实例按**发布态**校验（`schema_version` / `producer` / `evidence` 一并管），"
        "不是先压成裸映射再看权重——否则实例的声明部分会被静默忽略。",
        "",
        "## 3. 引擎消费（权重与 cash_floor 都穿过来了）",
        "",
        "| 字段 | 契约实例 | 引擎 spec | 一致 |",
        "|---|---|---|---|",
    ]
    period = PACK_INSTANCE["as_of"]
    engine_weights = spec.weights_series[period]
    for sym, w in sorted(PACK_INSTANCE["weights"].items()):
        got = engine_weights.get(sym)
        lines.append(f"| weights.{sym} | {w} | {got} | {'是' if got == w else '否'} |")
    floor = spec.cash_floors.get(period)
    lines.append(
        f"| cash_floor | {PACK_INSTANCE['cash_floor']} | {floor} | "
        f"{'是' if floor == PACK_INSTANCE['cash_floor'] else '否'} |"
    )
    lines += [
        "",
        "`cash_floor` 是契约独有的字段（裸映射表达不了），所以它穿过来这件事本身"
        "就是\"实例被当作实例消费\"的证据。",
        "",
        "## 4. 回测结果",
        "",
        "| 指标 | 值 |",
        "|---|---|",
    ]
    for key in (
        "total_return", "max_drawdown", "sharpe", "trade_count",
        "final_equity", "rebalance_count", "scaled_instruction_count",
    ):
        lines.append(f"| `{key}` | {res.stats.get(key)} |")
    lines += [
        "",
        f"窗口：{res.equity_points[0][0].isoformat()} → "
        f"{res.equity_points[-1][0].isoformat()}，共 {len(res.equity_points)} 个权益点，"
        f"{len(res.snapshots)} 次调仓。",
        "",
        "## 5. 每期快照（目标来自 pack 的实例）",
        "",
    ]
    for s in res.snapshots:
        target = ", ".join(f"{k}={v}" for k, v in sorted(s.target_weights.items()))
        actual = ", ".join(
            f"{k}={round(v, 6)}" for k, v in sorted(s.actual_weights.items())
        )
        lines.append(f"### {s.day.isoformat()}")
        lines.append("")
        lines.append(f"- 目标（pack 给的）：{target or '（空仓）'}")
        lines.append(f"- 实际（引擎做到）：{actual or '（空仓）'}")
        lines.append(f"- 目标现金下限：{s.cash_floor}")
        if s.error:
            lines.append(f"- **该期被拒**：{s.error}")
        if s.instructions:
            lines.append("")
            lines.append("| 标的 | 方向 | 股数 | 价格 | 缩量 |")
            lines.append("|---|---|---|---|---|")
            for i in s.instructions:
                lines.append(
                    f"| {i.symbol} | {i.side} | {i.quantity} | {i.price} | "
                    f"{'是' if i.scaled else '否'} |"
                )
        lines.append("")

    lines += [
        "## 6. 结论",
        "",
        "- pack 产出的契约实例**直接**进入引擎：没有转写、没有重算权重、没有归一化。",
        "- 权重合法性由契约层判定，引擎不持有第二套规则（改契约即改引擎行为，"
        "由 `tests/unit/test_weight_contract_gate_selfcheck.py` 打桩钉住）。",
        "- `cash_floor` 作为契约独有字段被引擎消费，说明实例是作为实例处理的。",
        "- 结果可解读：每期目标 vs 实际并列，取整残差留痕。",
        "",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"report written: {OUT.relative_to(REPO)}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    run_data = run()
    if args.check:
        again = run()
        if digest(run_data) != digest(again):
            print("FAIL: rerun produced a different result")
            return 1
        print(
            f"OK: rerun identical ({len(run_data['result'].equity_points)} equity "
            f"points, {len(run_data['result'].snapshots)} rebalances)"
        )
        return 0

    write_report(run_data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
