#!/usr/bin/env python3
"""策略 universe 的可报价性判据（change: fund-price-channel 的配套闸）。

## 为什么需要它

2026-11-14 的实况：策略 universe 里有场内基金（`510300`/`510050`/`159915`），
库里也有它们的日线（`price.close` 4.083 @ 2026-11-13），而交易面一律
`400 no_quote`——因为行情通道的实体解析只认 `stock`/`index`。整条链上**每一处
单独看都是绿的**：基金策略每天在写库、端点体检 200、派发正常、daily-pnl 只对
**持仓**报 `no_quote`（没持仓的标的不报）。只有真下一单才现形。

这条闸问的是那个没人问的问题：**策略打算交易的那些标的，现在能不能拿到报价？**

## 判据（三档，只有前两档计数）

1. `unpriceable` —— 拿不到报价。可能是通道不覆盖该类实体、也没有可读的价格族。
2. `stale` —— 有报价，但最后一根 bar 已超过阈值（默认 = **交易侧那个阈值**，见下）。
3. `declared_gap` —— 已知且已定性的缺口（见 `DECLARED_GAPS`）。**逐条打印、不计
   命中**——常年红的闸会被无视，而"场外基金只有净值"这种形态不会因为等得久就
   变好。每条必须写清解除条件。

## 阈值为什么默认取交易侧那个

这条闸问的是"**现在能不能下单**"。交易侧的 `stale_quote` 用的是
`execution.service.MAX_QUOTE_AGE_SECONDS`（4 天），且**没有任何节假日宽限**——周五
收盘价在周二就是"4 天前"，照样拒单。所以闸若用更松的阈值（例如 8 天），会在
**已经下不了单的那几天里报绿**：2026-11-14 实况就是 600000 的最后一根 bar 在
11-09（5.9 天），下单门拒它、而这条闸报 ok。

第一版默认 8 天，理由是"别在长周末假红"。那是个真实的取舍，但**取舍要有名字**：
`--max-age-days 8` 保留下来，用于判"通道级断供"（数据管线是不是整体停了）；
默认值与交易侧同源，并在 detail 里同时打印两个阈值。

## 退出码

`0` 无命中；`1` 有命中；`2` 跑不起来（取不到策略表 / 取不到行情源）。
**判不出不许退 0**——一次连不上库被读成"策略都能交易"，比没有这条闸更坏。

## 用法

    python scripts/check_universe_price_coverage.py
    python scripts/check_universe_price_coverage.py --symbols 510300,000001
    python scripts/check_universe_price_coverage.py --max-age-days 5 --json
    python scripts/check_universe_price_coverage.py --self-test

只读：只发 SELECT，写盘为零。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(REPO, "src") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "src"))

#: 交易侧"报价太旧就拒单"的阈值（天）。**唯一来源是执行层那个常量**：
#: 两处各写一个数字迟早漂移，而漂移的方向恰好是这条闸报绿、下单门拒单。
#: 导入失败（脚本在没有 src 的环境里单独跑）退回同一个值，并由单测钉住相等。
_FALLBACK_TRADING_MAX_AGE_DAYS = 4


def trading_max_age_days() -> int:
    try:
        from fd_paper_trading.execution.service import MAX_QUOTE_AGE_SECONDS
    except Exception:  # noqa: BLE001 - 沙箱里没有 src 时退回常量，单测会钉住一致
        return _FALLBACK_TRADING_MAX_AGE_DAYS
    return int(MAX_QUOTE_AGE_SECONDS // 86400)

#: 已知且已定性的缺口：**不算命中**，但每次都打印。
#:
#: 入表条件（三条同时成立，缺一条就走命中）：线上确认拿不到报价 + 仓内记过理由 +
#: 写清解除条件。别为了让闸变绿往里加。
DECLARED_GAPS: dict[str, dict[str, str]] = {
    "007355": {
        "reason": (
            "场外开放式基金（entities.metadata_json.subtype=open）：全库只有净值"
            "（nav.unit / nav.accumulated @ 2026-11-15、nav.daily_growth @ 2026-11-13），"
            "从来没有 price.* 行——它没有交易所价格"
        ),
        "fix": (
            "按净值估值单独立项：场外按 NAV 申赎、T+1 确认、无买卖价差，"
            "不能用 close±5bps 合成（那是编一个价）。该项落地后从此表移除"
        ),
    },
}


@dataclass
class SymbolFact:
    """一个标的的取数结果。判据只看这几项事实，便于注入自证。"""

    symbol: str
    strategy_names: list[str] = field(default_factory=list)
    quote: bool = False
    last_bar: str | None = None
    error: str | None = None


@dataclass
class Verdict:
    symbol: str
    state: str          # ok | stale | unpriceable | declared_gap | out_of_scope
    detail: str
    age_days: int | None = None

    @property
    def counts(self) -> bool:
        return self.state in ("stale", "unpriceable")


def symbols_of_spec(spec: Any) -> set[str]:
    """从策略 spec 里取它打算交易的标的。

    只认**显式**的两处（量过：单标的 kind 放 `spec['symbol']`，组合 kind 放
    `spec['universe']`）。不做"扫全 JSON 找像代码的字符串"——那种启发式会把
    模板名、因子名、基准代码一起抓进来，闸立刻变成噪声。
    """
    out: set[str] = set()
    if not isinstance(spec, dict):
        return out
    sym = spec.get("symbol")
    if isinstance(sym, str) and sym.strip():
        out.add(sym.strip().upper())
    uni = spec.get("universe")
    if isinstance(uni, list):
        out.update(
            s.strip().upper() for s in uni if isinstance(s, str) and s.strip()
        )
    return out


def is_astock_symbol(symbol: str) -> bool:
    """A 股/场内基金代码形状（6 位数字，可带 .SH/.SS/.SZ/.SHE）。

    判据只做形状判断：这条通道的实体解析本来就只认 6 位数字代码，
    拿它去判 AAPL 会得到"没价"——那是对的，但归因错了。
    """
    from fd_paper_trading.marketdata.astock import normalize_symbol
    return normalize_symbol(symbol) is not None


def judge(facts: Iterable[SymbolFact], *, today: date, max_age_days: int,
          declared: dict[str, dict[str, str]] | None = None) -> list[Verdict]:
    """把取数事实判成三档。纯函数：自证与单测都从这里注入。"""
    declared = DECLARED_GAPS if declared is None else declared
    verdicts: list[Verdict] = []
    for f in facts:
        if not is_astock_symbol(f.symbol):
            # 非 A 股形状（AAPL 之类）不归这条通道：其他市场有自己的 provider。
            # 报事实、不计数——把它算成缺口会让人去查一条本就不该管的链。
            verdicts.append(Verdict(f.symbol, "out_of_scope",
                                    "非 A 股代码形状，不归本通道（其他市场另有取源）"))
            continue
        if f.error:
            # 取数本身报错（库/源挂了）不是"这个标的没价"，必须与缺口分开：
            # 混成一档会让人去查标的，而真相是通道没通。
            verdicts.append(Verdict(f.symbol, "unpriceable",
                                    f"取数失败：{f.error}"))
            continue
        age: int | None = None
        if f.last_bar:
            age = (today - date.fromisoformat(f.last_bar)).days
        stale = age is not None and age > max_age_days
        if f.quote and not stale:
            verdicts.append(Verdict(f.symbol, "ok",
                                    f"报价可得，最后一根 {f.last_bar}（{age} 天）", age))
            continue
        if f.symbol in declared:
            d = declared[f.symbol]
            verdicts.append(Verdict(
                f.symbol, "declared_gap",
                f"{d['reason']}｜解除条件：{d['fix']}", age,
            ))
            continue
        if f.quote and stale:
            verdicts.append(Verdict(
                f.symbol, "stale",
                f"有报价但最后一根 bar 是 {f.last_bar}（{age} 天 > {max_age_days}）；"
                f"交易侧阈值 {trading_max_age_days()} 天 ⇒ 下单会被拒（stale_quote）", age,
            ))
        else:
            verdicts.append(Verdict(
                f.symbol, "unpriceable",
                "拿不到报价（provider 解析不出，或该类实体没有可读的价格族）", age,
            ))
    return sorted(verdicts, key=lambda v: (v.state != "stale", v.symbol))


# ---------------------------------------------------------------- 取事实

async def _load_strategy_symbols(symbols_override: list[str] | None) -> dict[str, list[str]]:
    """读策略表 → {symbol: [strategy_name...]}。取不到就抛（调用方退 2）。"""
    if symbols_override:
        return {s.upper(): ["<命令行>"] for s in symbols_override}

    from sqlalchemy import text

    from fd_paper_trading.config import get_settings
    from fd_paper_trading.persistence.base import create_database

    db = create_database(get_settings().database_url)
    try:
        async with db.session_factory() as session:
            rows = (await session.execute(
                text("select name, spec from strategies")
            )).all()
    finally:
        await db.engine.dispose()

    if not rows:
        raise RuntimeError("strategies 表为空——没有可判的对象（判不出，不是全过）")

    mapping: dict[str, list[str]] = {}
    for name, spec in rows:
        if isinstance(spec, str):
            try:
                spec = json.loads(spec)
            except ValueError:
                continue
        for sym in symbols_of_spec(spec):
            mapping.setdefault(sym, []).append(str(name))
    return mapping


async def _collect_facts(symbols: dict[str, list[str]], *, lookback_days: int) -> list[SymbolFact]:
    from _evidence_source import EvidenceSourceError, open_provider

    try:
        p = open_provider()
    except EvidenceSourceError as exc:
        raise RuntimeError(str(exc)) from exc

    today = date.today()
    start = today - timedelta(days=lookback_days)
    facts: list[SymbolFact] = []
    for sym, names in sorted(symbols.items()):
        fact = SymbolFact(symbol=sym, strategy_names=list(names))
        try:
            bars = await p.get_daily_bars(sym, start, today)
            quote = await p.get_quote(sym)
            fact.quote = quote is not None
            if bars:
                fact.last_bar = datetime.fromtimestamp(
                    bars[-1].ts, tz=timezone.utc
                ).date().isoformat()
        except Exception as exc:  # noqa: BLE001 - 取数失败要单独成态，不冒充缺口
            fact.error = f"{type(exc).__name__}: {exc}"
        facts.append(fact)
    return facts


# ---------------------------------------------------------------- self-test

def _self_test() -> int:
    """六条分支各证一次：绿路必须有，否则"全都判红"与"判据写反"同形。"""
    today = date(2026, 11, 14)
    facts = [
        SymbolFact("000001", quote=True, last_bar="2026-11-13"),        # ok
        SymbolFact("600036", quote=True, last_bar="2026-10-01"),        # stale
        SymbolFact("510300", quote=False, last_bar=None),               # unpriceable
        SymbolFact("007355", quote=False, last_bar=None),               # declared
        SymbolFact("600519", error="RuntimeError: store down"),         # error→命中
    ]
    got = {v.symbol: v.state for v in judge(facts, today=today, max_age_days=8)}
    expected = {
        "000001": "ok",
        "600036": "stale",
        "510300": "unpriceable",
        "007355": "declared_gap",
        "600519": "unpriceable",
    }
    # 非 A 股形状：不计数
    extra = judge([SymbolFact("AAPL", quote=False)], today=today, max_age_days=8)
    ok = got == expected
    for sym, want in expected.items():
        mark = "✓" if got.get(sym) == want else "✗"
        print(f"  {mark} {sym}: want={want} got={got.get(sym)}")
    # 计数口径：declared_gap 不计命中
    counted = [v for v in judge(facts, today=today, max_age_days=8) if v.counts]
    print(f"  {'✓' if len(counted) == 3 else '✗'} 命中数={len(counted)}（期望 3：stale/unpriceable/error）")
    # 未声明时同一标的必须落到命中（否则"入表"就成了洗白开关）
    got2 = {v.symbol: v.state for v in judge(
        [SymbolFact("007355", quote=False)], today=today, max_age_days=8, declared={})}
    print(f"  {'✓' if got2['007355'] == 'unpriceable' else '✗'} 未声明时 007355: {got2['007355']}（期望 unpriceable）")
    oos_ok = extra[0].state == "out_of_scope" and not extra[0].counts
    print(f"  {'✓' if oos_ok else '✗'} AAPL: {extra[0].state}（期望 out_of_scope 且不计数）")
    return (0 if ok and len(counted) == 3 and got2["007355"] == "unpriceable"
            and oos_ok else 1)


# ---------------------------------------------------------------- main

def _print_human(verdicts: list[Verdict], symbols: dict[str, list[str]],
                 max_age_days: int | None = None) -> None:
    marks = {"ok": "✓", "stale": "✗", "unpriceable": "✗",
             "declared_gap": "·", "out_of_scope": "·"}
    for v in verdicts:
        names = ", ".join(sorted(set(symbols.get(v.symbol, [])))) or "-"
        print(f"{marks[v.state]} {v.symbol} [{v.state}] {v.detail}")
        print(f"    策略：{names}")
    counted = [v for v in verdicts if v.counts]
    declared = [v for v in verdicts if v.state == "declared_gap"]
    print(
        f"\n共 {len(verdicts)} 个标的（stale 阈值 {max_age_days} 天"
        f"{'，= 交易侧 stale_quote' if max_age_days == trading_max_age_days() else '，命令行指定'}）："
        f"ok={sum(v.state == 'ok' for v in verdicts)} "
        f"stale={sum(v.state == 'stale' for v in verdicts)} "
        f"unpriceable={sum(v.state == 'unpriceable' for v in verdicts)} "
        f"declared_gap={len(declared)} "
        f"out_of_scope={sum(v.state == 'out_of_scope' for v in verdicts)}"
        f"（后两者不计命中）⇒ 命中 {len(counted)}"
    )


async def _amain(args: argparse.Namespace, *, max_age_days: int) -> int:
    override = [s for s in (args.symbols or "").split(",") if s.strip()] or None
    try:
        symbols = await _load_strategy_symbols(override)
        facts = await _collect_facts(symbols, lookback_days=args.lookback_days)
    except Exception as exc:  # noqa: BLE001 - 判不出必须退 2，不许退 0
        print(f"判不出（EXIT=2）：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    verdicts = judge(facts, today=date.today(), max_age_days=max_age_days)
    if args.json:
        print(json.dumps({
            "symbols": len(verdicts),
            "verdicts": [v.__dict__ for v in verdicts],
            "counted": sum(v.counts for v in verdicts),
        }, ensure_ascii=False, indent=2))
    else:
        _print_human(verdicts, symbols, max_age_days)
    return 1 if any(v.counts for v in verdicts) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", help="只判这些标的（逗号分隔），跳过策略表")
    ap.add_argument("--max-age-days", type=int, default=None,
                    help="最后一根 bar 超过多少自然日算 stale；缺省 = 交易侧 "
                         "stale_quote 的阈值（4 天，无节假日宽限）。判通道级断供时"
                         "可放宽到 8")
    ap.add_argument("--lookback-days", type=int, default=40,
                    help="取数窗口（默认 40 天，够覆盖长假）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--self-test", action="store_true", help="六条分支自证")
    args = ap.parse_args(argv)

    if args.self_test:
        return _self_test()
    max_age_days = args.max_age_days if args.max_age_days is not None else trading_max_age_days()
    return asyncio.run(_amain(args, max_age_days=max_age_days))


if __name__ == "__main__":
    raise SystemExit(main())
