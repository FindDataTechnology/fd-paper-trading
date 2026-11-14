#!/usr/bin/env python3
"""一条命令回答：**纸面交易现在能不能交易，如果不能，卡在哪一层？**

## 为什么需要它

2026-11-10 起个股日线断（东财封机房出口），到 11-14 才被发现。四层里每一层都有
自己的判据，而**没有一处能一次答这件事**：

| 层 | 谁答 | 答的是什么 |
|---|---|---|
| 采集 | `check_policy_freshness.py`（在 fd-open-data-mcp 仓） | 派发面还在不在跑 |
| 数据 | `check_universe_price_coverage.py` | 策略要交易的标的现在有没有价 |
| 执行 | `check_strategy_run_liveness.py` | 活着的 run 有没有在推进 |
| 成交 | 下单门 `stale_quote` | 单笔会不会被拒 |

前两条在这条链的下游（数据与执行），本脚本把它们合成一个判定。**采集那一层不在
本仓**（`policy_runs` 属于 fd-open-data-mcp 的控制面），所以本脚本不声称覆盖它——
少说一层比假装覆盖更诚实。

三档结论，取最坏的一档作为退出码：

- `tradable` —— 策略的标的都有价且活着；run 都在推进；
- `degraded` —— 没有硬拒，但有已声明的缺口（如场外基金无交易所价）或未被 cat 覆盖的标的；
- `blocked` —— 至少一个标的下单会被拒，或至少一条 live run 在空转。

## 判据归属（为什么不是又打一遍同样的逻辑）

本脚本**不重新实现任何判据**：它 import 两条闸的 `judge()` 与取数函数，跑同一套
逻辑再合成结论。重新实现一次，就意味着两处判据会各自漂移，而漂移方向恰好是
「合成视图报绿、单条闸报红」——同族于「闸的缺省阈值必须等于下游真实阈值」。

## 用法

    python scripts/check_paper_trading.py
    python scripts/check_paper_trading.py --json
    python scripts/check_paper_trading.py --self-test
    python scripts/check_paper_trading.py --assume-no-quote 510300   # 红证

只读：只发 SELECT，写盘为零。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date
from typing import Any

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "src"))
SCRIPTS = os.path.join(REPO, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

TRADABLE, DEGRADED, BLOCKED = "tradable", "degraded", "blocked"
_RANK = {TRADABLE: 0, DEGRADED: 1, BLOCKED: 2}


def _load(name: str, filename: str):
    """按文件名加载同目录下的闸（scripts/ 不是包，没有 import 路径）。

    查找两处：本脚本所在目录、以及仓里的 `scripts/`。前者是为了让「把三个脚本一起
    拷进 pod 的 /tmp 再跑」这种用法成立（2026-11-15 实测：只认仓结构时，pod 里
    `REPO` 会退成 `/`、去找 `/scripts/...` 而报 FileNotFound）。
    """
    if name in sys.modules:
        return sys.modules[name]
    here = os.path.dirname(os.path.abspath(__file__))
    for base in (here, SCRIPTS):
        path = os.path.join(base, filename)
        if os.path.exists(path):
            break
    else:
        raise FileNotFoundError(
            f"{filename} 不在 {here} 也不在 {SCRIPTS}——把同目录的闸一起拷过来")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@dataclass
class Branch:
    """一层判据的结论。`severity` 取最坏的那档。"""

    layer: str
    severity: str
    hits: int
    lines: list[str] = field(default_factory=list)


def combine(branches: list[Branch]) -> str:
    """取最坏一档。**空输入不得读成 tradable**——那是「什么都没查」。"""
    if not branches:
        raise ValueError("没有可判的分支（判不出，不是可交易）")
    return max(branches, key=lambda b: _RANK[b.severity]).severity


def _coverage_branch(coverage_mod, max_age_days: int, symbols, facts) -> Branch:
    """标的层：命中 ⇒ blocked；只有已声明缺口/超范围 ⇒ degraded。"""
    verdicts = coverage_mod.judge(facts, today=date.today(), max_age_days=max_age_days)
    hits = [v for v in verdicts if v.counts]
    lines = [f"{v.symbol} [{v.state}] {v.detail}" for v in hits]
    if hits:
        return Branch("数据（标的可报价性）", BLOCKED, len(hits), lines)
    soft = [v for v in verdicts if v.state in ("declared_gap", "out_of_scope")]
    if soft:
        return Branch("数据（标的可报价性）", DEGRADED, 0,
                      [f"{v.symbol} [{v.state}]" for v in soft])
    return Branch("数据（标的可报价性）", TRADABLE, 0, [])


def _liveness_branch(liveness_mod, max_age_days: int, facts) -> Branch:
    """执行层：run 卡住或数据停了 ⇒ blocked。"""
    verdicts = liveness_mod.judge(facts, today=date.today(), max_age_days=max_age_days)
    if not verdicts:
        raise ValueError("没有 RUNNING/STARTING 的 run（判不出，不是可交易）")
    hits = [v for v in verdicts if v.counts]
    und = [v for v in verdicts if v.state == "undetermined"]
    if hits:
        return Branch("执行（run 是否在推进）", BLOCKED, len(hits),
                      [f"{v.run_id} {v.strategy} [{v.state}] {v.detail}" for v in hits])
    if und and len(und) == len(verdicts):
        raise ValueError("全部 run 都判不出（取不到 run 的标的或行情源）")
    if und:
        return Branch("执行（run 是否在推进）", DEGRADED, 0,
                      [f"{v.run_id} [undetermined] {v.detail}" for v in und])
    return Branch("执行（run 是否在推进）", TRADABLE, 0, [])


# ---------------------------------------------------------------- self-test

def _self_test() -> int:
    """三档各一次 + 两条边界。绿路必须有，否则「全都判红」与「判据写反」同形。"""
    ok = True

    def check(label: str, got: Any, want: Any) -> None:
        nonlocal ok
        good = got == want
        ok = ok and good
        print(f"  {'✓' if good else '✗'} {label}: got={got} want={want}")

    check("全绿", combine([Branch("a", TRADABLE, 0), Branch("b", TRADABLE, 0)]), TRADABLE)
    check("一层降级", combine([Branch("a", TRADABLE, 0), Branch("b", DEGRADED, 0)]), DEGRADED)
    check("一层封死", combine([Branch("a", DEGRADED, 0), Branch("b", BLOCKED, 2)]), BLOCKED)
    check("封死压过降级", combine([Branch("a", BLOCKED, 1), Branch("b", DEGRADED, 0)]), BLOCKED)
    try:
        combine([])
    except ValueError:
        check("空分支＝判不出（不是可交易）", "raised", "raised")
    else:
        check("空分支＝判不出（不是可交易）", "returned", "raised")
    return 0 if ok else 1


# ---------------------------------------------------------------- main

async def _amain(args: argparse.Namespace) -> int:
    coverage = _load("coverage_gate", "check_universe_price_coverage.py")
    liveness = _load("liveness_gate", "check_strategy_run_liveness.py")
    max_age = args.max_age_days if args.max_age_days is not None else coverage.trading_max_age_days()

    try:
        symbols = await coverage._load_strategy_symbols(
            [s for s in (args.symbols or "").split(",") if s.strip()] or None)
        sym_facts = await coverage._collect_facts(symbols, lookback_days=40)
        run_facts = await liveness._load_facts(max_runs=50)
    except Exception as exc:  # noqa: BLE001 - 判不出必须退 2
        print(f"判不出（EXIT=2）：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.assume_no_quote:
        for f in sym_facts:
            if f.symbol == args.assume_no_quote.upper():
                f.quote = False
                f.last_bar = None
                break

    try:
        branches = [
            _coverage_branch(coverage, max_age, symbols, sym_facts),
            _liveness_branch(liveness, max_age, run_facts),
        ]
    except ValueError as exc:
        print(f"判不出（EXIT=2）：{exc}", file=sys.stderr)
        return 2

    overall = combine(branches)
    if args.json:
        print(json.dumps({
            "overall": overall,
            "max_age_days": max_age,
            "branches": [b.__dict__ for b in branches],
        }, ensure_ascii=False, indent=2))
    else:
        mark = {TRADABLE: "✓", DEGRADED: "·", BLOCKED: "✗"}
        print(f"纸面交易可达性（stale 阈值 {max_age} 天）：\n")
        for b in branches:
            print(f"{mark[b.severity]} {b.layer} [{b.severity}]"
                  + (f" 命中 {b.hits}" if b.hits else ""))
            for line in b.lines:
                print(f"    {line}")
        print(f"\n结论：{overall}")
        if overall == BLOCKED:
            print("下一步：先看上面 blocked 那一层的成因——数据层是采集/封禁，"
                  "执行层就分「runner 卡了」（重启）与「数据停了」（修采集，重启没用）。")
    # blocked 与 degraded 都退 1（"能不能交易"这个问题的答案是"不能全通"），
    # 与另两条闸的 0/1/2 口径一致；判不出已在上面退 2。
    return 0 if overall == TRADABLE else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbols", help="只判这些标的（逗号分隔），跳过策略表")
    ap.add_argument("--max-age-days", type=int, default=None,
                    help="缺省 = 交易侧 stale_quote 的阈值")
    ap.add_argument("--assume-no-quote", metavar="SYMBOL", help="红证：把该标的当作没有报价")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true", help="三档 + 两条边界自证")
    args = ap.parse_args(argv)
    if args.self_test:
        return _self_test()
    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
