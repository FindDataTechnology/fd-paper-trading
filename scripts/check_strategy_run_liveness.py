#!/usr/bin/env python3
"""活着的策略 run 到底在不在推进？——分清「runner 卡了」与「数据停了」。

## 为什么需要它

2026-11-10 起个股日线停在 11-09（东财封机房出口，policy 2659 每天失败），而
`fd-paper-trading` 里有 **3 条 `RUNNING` 的 run**（策略 `batch6-c2-sma`，`symbol=600000`）：

- `strategy_runs.status = RUNNING`，租约每秒在续（`runner_lease_until` 一直是当前时刻附近）；
- `error = NULL`；
- `params.last_bar_date = 2026-11-09` —— 四个交易日没动过一格。

因为 runner 的每个 tick 都是「拿到最后一根 bar，若与上次处理的一样就 return」：

```python
latest_date = _bar_date(bars[-1])
last_processed = (run.params or {}).get("last_bar_date")
if last_processed == latest_date.isoformat():
    return  # no new bar - nothing to do this poll
```

数据不再前进 ⇒ 每个 tick 都早退 ⇒ **没有订单、没有跳过记录、没有 error、没有告警**。
所有「服务健康吗」的判据都是绿的：pod ready、`/health` 200、租约在续、run 是 RUNNING。
这与本仓反复出现的形态同族：**进程活着，什么也没发生**。

## 它答的不是「数据新不新」

「数据新不新」由 `check_universe_price_coverage.py` 答（它问「能不能下单」）。这条闸问的是
**run 与数据之间的关系**，因为两种成因的处置完全不同：

- `runner_lag` —— 库里该标的**有更新的 bar**，而 run 还没处理 ⇒ runner 卡了（重启/看日志）；
- `data_stalled` —— run 处理到的就是该标的的最新 bar，但这条 bar 已经太旧 ⇒ **数据停了**，
  修 runner 没有用，要去修采集（本例：policy 2659 / 上游封禁）。

把这两种混成一句「策略没在交易」，就会让人去重启一个完全健康的 runner。

## 判据与退出码

`ok` — run 已处理到该标的的最新 bar，且没超过交易侧新鲜度阈值；
`runner_lag` — 有更新的 bar 却没处理（命中）；
`data_stalled` — 处理到的就是最新，但已超过阈值（命中）；
`undetermined` — 取不到 run 的标的（无 universe/params）或行情源读不到（退 2，不许退 0）。

    python scripts/check_strategy_run_liveness.py
    python scripts/check_strategy_run_liveness.py --json
    python scripts/check_strategy_run_liveness.py --self-test
    python scripts/check_strategy_run_liveness.py --assume-runner-lag <run_id 前缀>   # 红证
    python scripts/check_strategy_run_liveness.py --assume-data-stalled <run_id 前缀>  # 红证

只读：只发 SELECT。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "src"))

_FALLBACK_TRADING_MAX_AGE_DAYS = 4


def trading_max_age_days() -> int:
    """与下单门同源的阈值（唯一来源是执行层常量）。"""
    try:
        from fd_paper_trading.execution.service import MAX_QUOTE_AGE_SECONDS
    except Exception:  # noqa: BLE001 - 沙箱里没有 src 时退回常量，单测钉住相等
        return _FALLBACK_TRADING_MAX_AGE_DAYS
    return int(MAX_QUOTE_AGE_SECONDS // 86400)


LIVE_STATUSES = ("RUNNING", "STARTING")


@dataclass
class RunFact:
    """一条活着的 run + 它该看的那个标的在库里的最新 bar。"""

    run_id: str
    strategy: str
    symbol: str | None
    last_bar_processed: str | None
    symbol_latest_bar: str | None
    error: str | None = None


@dataclass
class Verdict:
    run_id: str
    strategy: str
    symbol: str | None
    state: str          # ok | runner_lag | data_stalled | undetermined
    detail: str

    @property
    def counts(self) -> bool:
        return self.state in ("runner_lag", "data_stalled")


def judge(facts: Iterable[RunFact], *, today: date, max_age_days: int) -> list[Verdict]:
    """纯函数。`symbol_latest_bar` 是行情源对该标的最新 bar（取不到 ⇒ None）。"""
    out: list[Verdict] = []
    for f in facts:
        if f.error:
            out.append(Verdict(f.run_id, f.strategy, f.symbol, "undetermined",
                               f"取数失败，判不出：{f.error}"))
            continue
        if not f.symbol:
            out.append(Verdict(f.run_id, f.strategy, None, "undetermined",
                               "这条 run 找不到主标的（universe/params 都空），判不出"))
            continue
        if f.last_bar_processed is None:
            out.append(Verdict(f.run_id, f.strategy, f.symbol, "undetermined",
                               "run 从未记录过 last_bar_date，判不出它推进到哪里"))
            continue
        if f.symbol_latest_bar is None:
            out.append(Verdict(f.run_id, f.strategy, f.symbol, "undetermined",
                               f"行情源对 {f.symbol} 一根 bar 都没有，判不出是数据停了还是源不通"))
            continue

        processed = date.fromisoformat(f.last_bar_processed)
        latest = date.fromisoformat(f.symbol_latest_bar)

        if latest > processed:
            behind = (latest - processed).days
            out.append(Verdict(
                f.run_id, f.strategy, f.symbol, "runner_lag",
                f"库里 {f.symbol} 已有 {f.symbol_latest_bar} 的 bar，而 run 停在 "
                f"{f.last_bar_processed}（落后 {behind} 天）⇒ runner 没在推进，"
                f"重启/看 runner 日志，不是采集问题",
            ))
            continue

        age = (today - processed).days
        if age > max_age_days:
            out.append(Verdict(
                f.run_id, f.strategy, f.symbol, "data_stalled",
                f"run 已处理到 {f.symbol} 的最新 bar（{f.last_bar_processed}），"
                f"但它已 {age} 天 > {max_age_days} ⇒ **数据停了**："
                f"重启 runner 没有用，要去修该标的的采集链",
            ))
            continue

        out.append(Verdict(f.run_id, f.strategy, f.symbol, "ok",
                           f"已处理到最新 bar {f.last_bar_processed}（{age} 天，阈值 {max_age_days}）"))
    return out


# ---------------------------------------------------------------- 取数

def _primary_symbol(spec: dict, params: dict) -> str | None:
    """template/单个 kind 用 `spec.symbol`；权重 kind 用它 universe 的第一个。

    与 runner 的判定轴一致：权重轨的 `primary_bars = bars_by_symbol[universe[0]]`，
    所以"这条 run 会不会动"取决于那一个标的有没有新 bar。
    """
    sym = spec.get("symbol")
    if isinstance(sym, str) and sym.strip():
        return sym.strip().upper()
    uni = params.get("universe") or spec.get("universe") or []
    if isinstance(uni, list) and uni:
        first = uni[0]
        if isinstance(first, str) and first.strip():
            return first.strip().upper()
    return None


async def _load_facts(max_runs: int) -> list[RunFact]:
    from sqlalchemy import text

    from _evidence_source import EvidenceSourceError, open_provider
    from fd_paper_trading.config import get_settings
    from fd_paper_trading.persistence.base import create_database

    db = create_database(get_settings().database_url)
    try:
        async with db.session_factory() as session:
            rows = (await session.execute(text(
                "select r.id, s.name, s.spec, r.params, r.error, "
                "       r.params->>'last_bar_date' as last_bar "
                "  from strategy_runs r join strategies s on s.id = r.strategy_id "
                " where upper(r.status) = any(:live) order by r.created_at desc "
                f" limit {int(max_runs)}"
            ), {"live": list(LIVE_STATUSES)})).all()
    finally:
        await db.engine.dispose()

    try:
        provider = open_provider()
    except EvidenceSourceError as exc:
        raise RuntimeError(str(exc)) from exc

    today = date.today()
    start = today - timedelta(days=40)
    facts: list[RunFact] = []
    for r in rows:
        spec = r[2] if isinstance(r[2], dict) else json.loads(r[2] or "{}")
        params = r[3] if isinstance(r[3], dict) else json.loads(r[3] or "{}")
        symbol = _primary_symbol(spec, params)
        latest: str | None = None
        err: str | None = r[4]
        if symbol and not err:
            try:
                bars = await provider.get_daily_bars(symbol, start, today)
                if bars:
                    latest = datetime.fromtimestamp(bars[-1].ts, tz=timezone.utc).date().isoformat()
            except Exception as exc:  # noqa: BLE001 - 取数失败要单独成态
                err = f"{type(exc).__name__}: {exc}"
        facts.append(RunFact(
            run_id=str(r[0])[:8], strategy=str(r[1]), symbol=symbol,
            last_bar_processed=r[5], symbol_latest_bar=latest, error=err,
        ))
    return facts


# ---------------------------------------------------------------- self-test

def _self_test() -> int:
    """四档各证一次 + 一条绿路。省掉绿路，「全都判红」与「判据写反」同形。"""
    today = date(2026, 11, 14)
    facts = [
        RunFact("a1", "pushing", "600000", "2026-11-13", "2026-11-13"),   # ok
        RunFact("b2", "stuck", "600000", "2026-11-06", "2026-11-13"),     # runner_lag
        RunFact("c3", "frozen", "600000", "2026-11-09", "2026-11-09"),    # data_stalled
        RunFact("d4", "blind", None, None, None),                         # undetermined
    ]
    got = {v.run_id: v.state for v in judge(facts, today=today, max_age_days=4)}
    expected = {"a1": "ok", "b2": "runner_lag", "c3": "data_stalled", "d4": "undetermined"}
    ok = got == expected
    for k, want in expected.items():
        print(f"  {'✓' if got.get(k) == want else '✗'} {k}: want={want} got={got.get(k)}")
    counted = [v for v in judge(facts, today=today, max_age_days=4) if v.counts]
    print(f"  {'✓' if len(counted) == 2 else '✗'} 命中数={len(counted)}（期望 2）")
    # 两种成因必须能分开：处置完全不同（重启 runner vs 修采集）
    reasons = {v.run_id: v.detail for v in judge(facts, today=today, max_age_days=4)}
    split = ("runner 没在推进" in reasons["b2"]) and ("数据停了" in reasons["c3"])
    print(f"  {'✓' if split else '✗'} 两种成因各自点名（runner_lag ≠ data_stalled）")
    return 0 if ok and len(counted) == 2 and split else 1


# ---------------------------------------------------------------- main

def _inject(facts: list[RunFact], *, lag: str | None, stalled: str | None,
            today: date) -> list[str]:
    notes: list[str] = []
    for target, kind in ((lag, "runner-lag"), (stalled, "data-stalled")):
        if not target:
            continue
        hit = [f for f in facts if f.run_id.startswith(target) or target in f.strategy]
        if not hit:
            notes.append(f"--assume-{kind}: 没有匹配的 run {target!r}（注入未生效）")
            continue
        for f in hit:
            if kind == "runner-lag":
                f.last_bar_processed = (today - timedelta(days=7)).isoformat()
                f.symbol_latest_bar = today.isoformat()
            else:
                old = (today - timedelta(days=7)).isoformat()
                f.last_bar_processed = old
                f.symbol_latest_bar = old
            notes.append(f"--assume-{kind}: 已注入到 {f.run_id} {f.strategy}")
    return notes


def _print_human(verdicts: list[Verdict], max_age_days: int) -> None:
    marks = {"ok": "✓", "runner_lag": "✗", "data_stalled": "✗", "undetermined": "?"}
    for v in verdicts:
        print(f"{marks[v.state]} {v.run_id} {v.strategy}"
              f"{' [' + v.symbol + ']' if v.symbol else ''} [{v.state}] {v.detail}")
    counted = [v for v in verdicts if v.counts]
    und = [v for v in verdicts if v.state == "undetermined"]
    print(f"\n活着且需要判断的 run {len(verdicts)} 条（阈值 {max_age_days} 天，"
          f"{'= 交易侧 stale_quote' if max_age_days == trading_max_age_days() else '命令行指定'}）："
          f"ok={sum(v.state == 'ok' for v in verdicts)} "
          f"runner_lag={sum(v.state == 'runner_lag' for v in verdicts)} "
          f"data_stalled={sum(v.state == 'data_stalled' for v in verdicts)} "
          f"undetermined={len(und)} ⇒ 命中 {len(counted)}")


async def _amain(args: argparse.Namespace, *, max_age_days: int) -> int:
    try:
        facts = await _load_facts(max_runs=args.max_runs)
    except Exception as exc:  # noqa: BLE001 - 判不出必须退 2
        print(f"判不出（EXIT=2）：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    today = date.today()
    notes = _inject(facts, lag=args.assume_runner_lag,
                    stalled=args.assume_data_stalled, today=today)
    verdicts = judge(facts, today=today, max_age_days=max_age_days)

    if args.json:
        print(json.dumps({"injection": notes,
                          "verdicts": [v.__dict__ for v in verdicts],
                          "counted": sum(v.counts for v in verdicts)},
                         ensure_ascii=False, indent=2))
    else:
        for line in notes:
            print(f"⚠ {line}")
        if not verdicts:
            print("没有 RUNNING/STARTING 的 run —— 这条闸此刻无对象（判不出，不是全过）")
            return 2
        _print_human(verdicts, max_age_days)

    if not verdicts:
        return 2
    if any(v.state == "undetermined" for v in verdicts) and not any(v.counts for v in verdicts):
        return 2      # 一条都判不出时不许退 0
    return 1 if any(v.counts for v in verdicts) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-age-days", type=int, default=None,
                    help="已处理到的最新 bar 超过多少自然日算数据停了；缺省 = 交易侧阈值")
    ap.add_argument("--max-runs", type=int, default=50, help="最多判多少条 live run")
    ap.add_argument("--assume-runner-lag", metavar="RUN_ID", help="红证：把该 run 当作落后于数据")
    ap.add_argument("--assume-data-stalled", metavar="RUN_ID", help="红证：把该 run 当作数据停了")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true", help="四档各证一次")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()
    max_age_days = args.max_age_days if args.max_age_days is not None else trading_max_age_days()
    return asyncio.run(_amain(args, max_age_days=max_age_days))


if __name__ == "__main__":
    raise SystemExit(main())
