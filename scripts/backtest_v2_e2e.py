#!/usr/bin/env python
"""backtest-engine-v2 端到端验收（task 8.2）。

同一策略跑三种配置——单窗零成本 / 单窗全成本 / walk-forward——把三份结果与
两条断言记录（成本单调性、walk-forward 有序性）落成一份报告。

**为什么要跑真实链路而不是拼装数字**：本变更的核心风险不是某个公式错，而是
"三种模式在同一请求容器里互相干扰"（比如成本泄漏进 walk-forward、窗口统计
被整体统计覆盖）。只有真的各跑一遍并对比，才看得见这类问题。

用法（需 fd-quant 计算面可用）：
    FD_QUANT_REPO=../fd-quant .venv/bin/python scripts/backtest_v2_e2e.py
报告落 `openspec/changes/backtest-engine-v2/reports/e2e-v2.md`。
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import socket
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from fd_paper_trading.marketdata.provider import Bar  # noqa: E402
from fd_paper_trading.strategy import costs as C  # noqa: E402
from fd_paper_trading.strategy.backtester import build_evaluator, replay  # noqa: E402
from fd_paper_trading.strategy.extended_stats import extended_stats  # noqa: E402
from fd_paper_trading.strategy.spec import validate_spec  # noqa: E402
from fd_paper_trading.strategy.walk_forward import run_walk_forward  # noqa: E402

REPORT = ROOT / "openspec" / "changes" / "backtest-engine-v2" / "reports" / "e2e-v2.md"


def _bars(prices, start=date(2025, 1, 1)):
    out = []
    for i, p in enumerate(prices):
        d = start + timedelta(days=i)
        ts = datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()
        px = Decimal(str(round(p, 4)))
        out.append(Bar(ts=ts, open=px, high=px, low=px, close=px, volume=Decimal(1000)))
    return out


def _start_compute_plane():
    """起一个计算面（若调用方已设 endpoint 则复用）。"""
    if os.environ.get("FD_QUANT_COMPUTE_ENDPOINT"):
        return None
    cand = Path(os.environ.get("FD_QUANT_REPO", ROOT.parent / "fd-quant"))
    server = cand / "fd_quant" / "compute" / "server.py"
    if not server.exists():
        print(f"ERROR: compute plane not found at {cand}", file=sys.stderr)
        return None
    python = cand / ".venv" / "bin" / "python"
    if not python.exists():
        python = Path("python3")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [str(python), "-m", "fd_quant.compute.server", "--http", str(port)],
        cwd=str(cand), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    for _ in range(150):
        if proc.poll() is not None:
            print(proc.stdout.read()[-1500:], file=sys.stderr)
            return None
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                os.environ["FD_QUANT_COMPUTE_ENDPOINT"] = f"http://127.0.0.1:{port}/mcp"
                return proc
        except OSError:
            time.sleep(0.2)
    proc.kill()
    return None


async def main() -> int:
    proc = _start_compute_plane()
    if not os.environ.get("FD_QUANT_COMPUTE_ENDPOINT"):
        print("ERROR: no compute plane endpoint available", file=sys.stderr)
        return 1

    try:
        # 一段带趋势与回撤的行情：足以产生换手，也让回撤与波动有内容
        prices = [100 + 12 * math.sin(i / 18.0) + i * 0.08 for i in range(400)]
        bars = _bars(prices)
        spec = validate_spec({
            "kind": "template", "template": "sma_cross", "symbol": "AAPL",
            "params": {"fast": 10, "slow": 30, "quantity": 50},
        })
        initial = Decimal("100000")

        free = await replay(bars, build_evaluator(spec), initial, C.CostModel(enabled=False))
        costed = await replay(bars, build_evaluator(spec), initial, C.CostModel(enabled=True))
        wf = await run_walk_forward(spec, bars, initial,
                                    train_window=120, test_window=40, step=40)

        ext_free = extended_stats(free.equity_points)

        # 断言 1：成本单调性（有成交时严格更低）
        monotone_ok = costed.stats["final_equity"] < free.stats["final_equity"]
        # 断言 2：walk-forward 有序且测试段严格晚于训练段
        order_ok = all(w.test_start > w.train_end for w in wf.windows)
        stitched_ok = len(wf.equity_points) == sum(len(w.equity_points) for w in wf.windows)

        lines: list[str] = []
        lines.append("# backtest-engine-v2 端到端验收报告（task 8.2）")
        lines.append("")
        lines.append("> **本文件由脚本生成，请勿手改**——重跑 `python3 scripts/backtest_v2_e2e.py` 覆盖。")
        lines.append("> 手改会在下次重跑时静默丢失，且报告与代码不再可对账。")
        lines.append("")
        lines.append(f"**生成时间**：{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
        lines.append(f"**行情**：{len(bars)} 个交易日（2025-01-01 起，构造序列：正弦趋势 + 线性漂移）")
        lines.append(f"**策略**：`sma_cross` fast=10 slow=30 quantity=50，初始资金 {initial}")
        lines.append("")
        lines.append("## 一、三种配置的结果")
        lines.append("")
        lines.append("| 配置 | final_equity | total_return | max_drawdown | trade_count | 新统计 |")
        lines.append("|---|---|---|---|---|---|")

        def _row(name, stats, extra=None):
            return (f"| {name} | {stats['final_equity']} | {stats['total_return']} | "
                    f"{stats['max_drawdown']} | {stats['trade_count']} | "
                    f"{'有' if extra else '—'} |")

        lines.append(_row("单窗 · 零成本", free.stats, ext_free))
        lines.append(_row("单窗 · 全成本", costed.stats, None))
        lines.append(_row("walk-forward（120/40/40）", wf.combined_stats, None))
        lines.append("")

        lines.append("## 二、成本明细（单窗 · 全成本）")
        lines.append("")
        lines.append(f"- 配置快照：`{json.dumps(costed.cost_config, ensure_ascii=False)}`")
        lines.append(f"- 费用汇总：`{json.dumps(costed.cost_summary, ensure_ascii=False)}`")
        lines.append(f"- 零成本 final_equity：**{free.stats['final_equity']}**")
        lines.append(f"- 全成本 final_equity：**{costed.stats['final_equity']}**")
        lines.append(f"- 差额：**{free.stats['final_equity'] - costed.stats['final_equity']:.4f}**")
        lines.append("")

        lines.append("## 三、断言记录")
        lines.append("")
        lines.append("| 断言 | 结果 |")
        lines.append("|---|---|")
        lines.append(f"| 成本单调性：全成本净收益 < 零成本（task 3.3） | {'✅ 通过' if monotone_ok else '❌ 失败'} |")
        lines.append(f"| walk-forward 有序性：每窗测试段严格晚于训练段（spec Scenario） | {'✅ 通过' if order_ok else '❌ 失败'} |")
        lines.append(f"| 拼接曲线行数 = 各窗测试段 bar 数之和（task 6.3） | {'✅ 通过' if stitched_ok else '❌ 失败'} |")
        lines.append("")

        lines.append("## 四、walk-forward 窗口列表")
        lines.append("")
        lines.append("| 窗 | 训练段 | 测试段 | total_return | trade_count |")
        lines.append("|---|---|---|---|---|")
        for w in wf.windows:
            lines.append(f"| {w.window} | {w.train_start} → {w.train_end} | "
                         f"{w.test_start} → {w.test_end} | {w.stats['total_return']} | "
                         f"{w.stats['trade_count']} |")
        lines.append("")

        lines.append("## 五、新统计（来自 fd-quant 计算面）")
        lines.append("")
        lines.append(f"- `source`：`{ext_free.get('source')}`")
        for k in ("annual_return", "annual_volatility", "sortino", "calmar", "turnover"):
            lines.append(f"- `{k}`：`{ext_free.get(k)}`")
        if ext_free.get("notes"):
            lines.append(f"- 说明：`{json.dumps(ext_free['notes'], ensure_ascii=False)}`")
        lines.append("")

        lines.append("## 六、复跑")
        lines.append("")
        lines.append("```bash")
        lines.append("cd fd-paper-trading")
        lines.append("FD_QUANT_REPO=../fd-quant .venv/bin/python scripts/backtest_v2_e2e.py")
        lines.append("```")
        lines.append("")
        lines.append("**判读**：三配置的数字来自同一条代码路径（`replay` / `run_walk_forward`），")
        lines.append("成本与窗口逻辑各自独立生效、互不干扰；两条断言若失败，报告会如实写 ❌。")
        lines.append("")

        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text("\n".join(lines), encoding="utf-8")
        print(f"✓ 报告已写：{REPORT.relative_to(ROOT.parent)}")
        print(f"  零成本 final={free.stats['final_equity']} / 全成本 final={costed.stats['final_equity']}")
        print(f"  walk-forward 窗口数={len(wf.windows)}，有序={order_ok}，拼接={stitched_ok}")
        print(f"  成本单调={monotone_ok}")
        return 0 if (monotone_ok and order_ok and stitched_ok) else 1
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
