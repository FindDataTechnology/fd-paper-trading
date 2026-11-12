#!/usr/bin/env python3
"""2.3 real-engine execution: top-N targets through replay_weights.

Two weight sources, because `factor-real-cross-section` item 3 requires both to
actually execute: equal weight, and score-proportional (shares ∝ score — the rule
the factor suite documents; never an optimizer).

    # equal weight (the original acceptance run)
    FD_OPEN_DATA_DSN=$FD_OPEN_DATA_DSN FACTOR_TOP_CODES=000908,002553,... \
      uv run python scripts/weight_backtest_topN.py \
      > ../openspec/changes/add-trading-real-data-closeout/reports/weight-backtest-topN.txt

    # score weight: scores come from the factor cross-section, never invented here
    FD_OPEN_DATA_DSN=... FACTOR_TOP_CODES=... FACTOR_WEIGHT_SCHEME=score \
      FACTOR_SCORES=000908:0.91,002553:0.77,... \
      uv run python scripts/weight_backtest_topN.py > .../weight-backtest-topN-score.txt

`FACTOR_SCORES` is mandatory in score mode: a script that derived scores from
rank order would be producing evidence for a rule nobody actually ran.
"""

import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))  # 同目录的 _evidence_source

from _evidence_source import EvidenceSourceError, open_provider  # noqa: E402
from fd_paper_trading.strategy.allocation import score_proportional_weights  # noqa: E402
from fd_paper_trading.strategy.spec import Rebalance, WeightSpec  # noqa: E402
from fd_paper_trading.strategy.weight_backtester import replay_weights  # noqa: E402

END = date(2026, 7, 10)  # the astock_daily anchor (data ends there)

#: Weight sources the acceptance run can execute. `score` needs explicit scores.
SCHEMES = ("equal", "score")


def resolve_scheme(raw: str | None) -> str:
    """Normalize ``FACTOR_WEIGHT_SCHEME`` or raise on anything unrecognized.

    Fail-fast matters here: a typo must not fall through to equal weight and then
    be filed as "score-weighted evidence". The caller turns the ValueError into a
    non-zero exit, so a bad config never produces a report.
    """
    scheme = (raw or "equal").strip().lower()
    if scheme not in SCHEMES:
        raise ValueError(f"FACTOR_WEIGHT_SCHEME={raw!r} 只接受 {' | '.join(SCHEMES)}")
    return scheme


def parse_scores(raw: str | None) -> dict[str, float]:
    """Parse ``code:score,code:score`` into ``{code: score}``.

    A malformed entry raises instead of being skipped: dropping one name changes
    the universe the evidence claims to have tested.
    """
    scores: dict[str, float] = {}
    for part in (raw or "").split(","):
        if not part.strip():
            continue
        code, _, value = part.partition(":")
        if not code.strip() or not value.strip():
            raise ValueError(f"FACTOR_SCORES 条目要 code:score，收到 {part!r}")
        try:
            scores[code.strip()] = float(value)
        except ValueError as exc:
            raise ValueError(f"FACTOR_SCORES 的分数不是数：{part!r}") from exc
    return scores


async def main() -> int:
    codes = [c for c in os.environ.get("FACTOR_TOP_CODES", "").split(",") if c]
    decision = os.environ.get("FACTOR_DECISION_DATE", "2025-09-30")
    # 配置先解析、再连库：坏配置不该等十个网络往返之后才报。
    try:
        scheme = resolve_scheme(os.environ.get("FACTOR_WEIGHT_SCHEME"))
        scores = parse_scores(os.environ.get("FACTOR_SCORES")) if scheme == "score" else {}
    except ValueError as exc:
        print(f"FAIL: {exc}")
        return 2
    if len(codes) < 5:
        print("FAIL: need >= 5 codes (FACTOR_TOP_CODES)")
        return 2
    try:
        provider = open_provider()
    except EvidenceSourceError as exc:
        print(f"FAIL: {exc}")
        return 2
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
    if scheme == "score":
        missing = [c for c in universe if c not in scores]
        if missing:
            print(f"FAIL: FACTOR_SCORES 缺 {len(missing)} 个在册标的：{missing[:5]}")
            return 2
        weights = score_proportional_weights(scores, budget=0.9, universe=universe)
        label = f"score-proportional top{n} (0.9 预算按分数占比)"
    else:
        w = round(0.9 / n, 4)
        weights = {c: w for c in universe}
        label = f"top{n} equal-weight {w}"
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
    print(f"== weight-mode backtest REAL ENGINE ({label}) ==")
    print(f"   universe: {universe}")
    print(f"   weights: {dict(sorted(weights.items(), key=lambda kv: -kv[1]))}")
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
    try:
        raise SystemExit(asyncio.run(main()))
    except (OSError, ConnectionError) as exc:
        # 连不上就说连不上：裸 traceback 会被读成"脚本坏了/数据有问题"。
        print(f"FAIL: 价格库不可达（DSN 的主机/端口连不上）：{type(exc).__name__}: {exc}")
        raise SystemExit(2) from exc
