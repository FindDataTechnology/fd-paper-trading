#!/usr/bin/env python3
"""按 fingerprint 判定两次回测"可不可比"（change1 task 6.2）。

## 判据

同一策略、同一窗口、同一份数据跑两次 → `fingerprint` 相同 → 统计必须**逐位一致**。
数据变了（水位前移、补洞、换源）→ `fingerprint` 变 → 该区间标**不可比**，
不拿新旧数字做任何比较。

这不是"提醒"，是**不许比**：拿两批不同输入的结果算"提升了多少"，是在测量噪声。

## 为什么 fingerprint 能担这个判据

契约里的指纹 = sha256 over（源 / 标的 / 口径 / 锚点 / 窗口 / 水位 / 行数）。
所以同一个指纹等价于"同一批输入"，而不是"看起来差不多"。反过来，指纹不同必然
是输入不同——**不需要再去猜哪一段变了**。

用法：
    python scripts/compare_backtest_fingerprints.py --before <run_id> --after <run_id>
    python scripts/compare_backtest_fingerprints.py --run <run_id>   # 自比，应全可比
退出码：0 = 全部可比且统计一致；1 = 存在不可比区间（已标注）或未标区间数字不一致。
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

#: 参与"逐位一致"比较的统计字段。只比这些——其余（如时间戳）本就会变，
#: 拿它们判等会让每次都不一致，等于没有判据。
STATS_KEYS = ("total_return", "max_drawdown", "sharpe", "win_rate",
              "trade_count", "final_equity")


def _coverage_by_symbol(params: dict[str, Any]) -> dict[str, dict]:
    """取出每个标的的覆盖记录。

    两种形态并存（都对）：
    - 单标的回测：覆盖记录平铺在 params 里（`fingerprint`/`missing_count`/…）；
    - 组合（weight）回测：按标的收在 `params["data_coverage"][symbol]` 下。
    """
    dc = params.get("data_coverage")
    if isinstance(dc, dict) and dc:
        return {str(k): v for k, v in dc.items() if isinstance(v, dict)}
    if "fingerprint" in params or "coverage_checked" in params:
        return {"(single)": params}
    return {}


#: 决定"同一实验"的参数。指纹只覆盖**数据**；这些覆盖**计算**。
#: 任一不同 → 统计本就该不同，不是缺陷（本轮实测：同策略两次，一次带 cost_model、
#: 一次不带，final_equity 差 10.60 —— 若只比指纹就会把正常差异误报成实现缺陷）。
PARAM_IDENTITY_KEYS = ("start", "end", "initial_cash", "cost_model", "risk",
                       "benchmarks", "walkforward", "param_grid")


def _param_identity(run: dict[str, Any]) -> dict[str, Any]:
    p = run.get("params") or {}
    return {k: p.get(k) for k in PARAM_IDENTITY_KEYS}


def _strategy_identity(run: dict[str, Any]) -> tuple:
    """策略身份 = (strategy_id, version)。

    **指纹只证明数据相同，不证明策略相同**——这是本轮实测踩到的坑：
    两个不同策略（03849c8c vs 741671d1）在同一 universe、同一窗口上跑，
    指纹一模一样（覆盖只依赖数据），统计却天差地别（6 笔 vs 0 笔）。
    拿它们算"差异"等于把一个策略的收益读成另一个策略的退化。
    """
    return (str(run.get("strategy_id") or ""), run.get("strategy_version"))


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """比较两次运行的覆盖记录与统计。

    返回 ``{comparable: bool, symbols: {...}, stats_identical: bool|None, notes: [...]}``。
    ``stats_identical`` 为 ``None`` 表示"没有可比区间，因此不比较统计"——
    **不是**"比较了且一致"（两者在报告上必须不同形）。
    """
    b = _coverage_by_symbol(before.get("params") or {})
    a = _coverage_by_symbol(after.get("params") or {})
    notes: list[str] = []

    shared = sorted(set(b) & set(a))
    if not shared:
        notes.append("两次运行没有共同的标的覆盖记录：无法判定可比性（不是'可比'）")

    symbols: dict[str, dict] = {}
    for sym in shared:
        fb = (b[sym] or {}).get("fingerprint") or ""
        fa = (a[sym] or {}).get("fingerprint") or ""
        has_b = bool(fb)
        has_a = bool(fa)
        if not has_b or not has_a:
            # 指纹缺失 ≠ 指纹相同。缺指纹的一侧根本没被检查过。
            symbols[sym] = {
                "comparable": False,
                "reason": "fingerprint_missing",
                "before_fp": fb or None, "after_fp": fa or None,
            }
            continue
        if fb == fa:
            symbols[sym] = {"comparable": True, "fingerprint": fb}
        else:
            bo = (b[sym] or {}).get("as_of_max")
            ao = (a[sym] or {}).get("as_of_max")
            symbols[sym] = {
                "comparable": False,
                "reason": "fingerprint_changed",
                "before_fp": fb, "after_fp": fa,
                # 只报两侧记录到的水位，不推断"差多少天"——那要另一次查询，
                # 而这里只需要回答"能不能比"。
                "before_as_of_max": bo, "after_as_of_max": ao,
            }

    comparable_syms = [s for s, v in symbols.items() if v.get("comparable")]
    noncomparable = [s for s, v in symbols.items() if not v.get("comparable")]

    stats_identical: bool | None = None
    same_strategy = (
        _strategy_identity(before) == _strategy_identity(after)
        and bool(_strategy_identity(before)[0])
    )
    pb, pa = _param_identity(before), _param_identity(after)
    param_diffs = {k: {"before": pb.get(k), "after": pa.get(k)}
                   for k in PARAM_IDENTITY_KEYS if pb.get(k) != pa.get(k)}
    if same_strategy and param_diffs:
        notes.append(
            "同一策略但**计算参数不同**（统计本就该不同，不是缺陷）："
            + json.dumps(param_diffs, ensure_ascii=False)
        )
    if not same_strategy:
        # 不同策略 / 策略身份未知 → 统计不可比。仍可回答"数据变没变"（那是指纹的事），
        # 但**不许**把两次统计放一起算差值。
        notes.append(
            "两次运行不是同一个策略（或策略身份缺失）："
            f"before={_strategy_identity(before)} after={_strategy_identity(after)}；"
            "只判断了数据可比性，统计未比较"
        )
    elif comparable_syms and not param_diffs:
        diffs = {}
        sb = before.get("stats") or {}
        sa = after.get("stats") or {}
        for k in STATS_KEYS:
            if k in sb or k in sa:
                if sb.get(k) != sa.get(k):
                    diffs[k] = {"before": sb.get(k), "after": sa.get(k)}
        stats_identical = not diffs
        if diffs:
            notes.append(
                "指纹相同但统计不等 → 输入相同而结果不同，这是实现缺陷（不是数据变化）："
                + json.dumps(diffs, ensure_ascii=False)
            )

    return {
        "comparable": bool(shared) and not noncomparable,
        "symbols": symbols,
        "comparable_symbols": comparable_syms,
        "not_comparable_symbols": noncomparable,
        "stats_identical": stats_identical,
        "notes": notes,
    }


def _fail(msg: str) -> int:
    print(f"✗ {msg}", file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--before", help="改前那次 run 的 id")
    ap.add_argument("--after", help="改后那次 run 的 id")
    ap.add_argument("--run", help="单次运行（自比，验证'同输入必同指纹'）")
    ap.add_argument("--db", default=None, help="直接给定 run 记录的 JSON 文件（离线用）")
    args = ap.parse_args(argv)

    if args.db:
        payload = json.loads(open(args.db, encoding="utf-8").read())
        before, after = payload["before"], payload["after"]
    elif args.run:
        payload = json.loads(open(args.run, encoding="utf-8").read()) \
            if args.run.endswith(".json") else None
        if payload is None:
            return _fail("--run 需要 run 记录的 JSON 文件（本机无生产库访问）")
        before = after = payload
    elif args.before and args.after:
        return _fail("本机不能直连生产库；请用 --db 传两份 run 记录的 JSON")
    else:
        return _fail("需要 --before/--after，或 --run，或 --db")

    out = compare(before, after)
    print(json.dumps(out, ensure_ascii=False, indent=2))

    if out["notes"]:
        for n in out["notes"]:
            print(f"  注：{n}", file=sys.stderr)
    if out["stats_identical"] is False:
        return _fail("可比区间的统计不逐位一致——输入相同而结果不同，是实现缺陷")
    if out["not_comparable_symbols"]:
        print(f"✗ 已标注不可比区间：{out['not_comparable_symbols']}", file=sys.stderr)
        return 1
    if out["stats_identical"] is None:
        return _fail("没有任何可比区间：统计未被比较（这不等于'一致'）")
    print("✓ 全部可比，且统计逐位一致")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
