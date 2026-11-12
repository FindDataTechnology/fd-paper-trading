"""Cross-track parity: one `risk` config must mean one thing.

The weight track and the single-symbol track call the *same* overlay functions
(`evaluate_bar_overlay`, `effective_target_weight`, `CooldownState`), but they
feed them from different places:

- entry price: `_WeightPortfolio.cost` (weighted average) vs `BacktestPortfolio.avg_cost`
- peak: per-symbol `close` running max vs a single `peak_value` on `close`

If either basis drifts, the same `stop_loss_pct` fires on different dates — and
nothing else in the suite would notice, because each track's tests only compare
against its own expectations. These tests feed **identical bars and identical
config** to both and require the same trigger date and kind.

They also pin the cost basis itself: a second buy at a different price must move
the average to the arithmetic mean, because that average is the only thing
standing between "stop at -10%" and "-10% of some other price".
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from fd_paper_trading.marketdata.provider import Bar
from fd_paper_trading.strategy import backtester as single
from fd_paper_trading.strategy.risk_overlay import RiskConfig
from fd_paper_trading.strategy.spec import validate_spec
from fd_paper_trading.strategy.weight_backtester import replay_weights

UTC = timezone.utc
START = date(2026, 1, 1)


def _bar(day: date, open_: str, close: str, low: str, high: str) -> Bar:
    ts = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
    return Bar(ts=ts, open=Decimal(open_), high=Decimal(high),
               low=Decimal(low), close=Decimal(close), volume=Decimal(1000))


#: 100 → 100 → 破位（low 89 < 90）→ 反弹。两轨喂同一串。
SCRIPT = [
    ("100", "100", "99", "101"),
    ("100", "100", "99", "101"),
    ("95", "88", "89", "96"),
    ("100", "100", "99", "101"),
]


def _bars(script=SCRIPT) -> list[Bar]:
    return [
        _bar(START + timedelta(days=i), o, c, lo, hi)
        for i, (o, c, lo, hi) in enumerate(script)
    ]


def _weight_result(risk: dict):
    spec = validate_spec({
        "kind": "weight",
        "universe": ["A"],
        "rebalance": {"every_n_bars": 1},
        "weights_series": {START.isoformat(): {"A": 1.0}},
    })
    return replay_weights(spec, {"A": _bars()}, Decimal("100000"),
                          risk=RiskConfig.from_dict(risk))


def _single_result(risk: dict):
    """单标的轨：同序 bar、同一配置，走 backtester 的 replay。"""
    spec = validate_spec({
        "kind": "template", "template": "dca", "symbol": "A",
        "params": {"interval_bars": 1, "quantity": 1},
    })
    return asyncio.run(
        single.replay(
            _bars(),
            single.build_evaluator(spec),
            Decimal("100000"),
            None,
            risk=RiskConfig.from_dict(risk),
        )
    )


def test_same_config_agrees_on_trigger_date_and_kind() -> None:
    """同 bar、同配置 ⇒ 两轨触发日期与 kind 必须一致。"""
    risk = {"stop_loss_pct": "0.10"}

    weight = _weight_result(risk)
    one = _single_result(risk)

    wt = [(t["date"], t["kind"]) for t in (weight.risk_triggers or [])]
    st = [(t["date"], t["kind"]) for t in (one.risk_triggers or [])]
    assert wt == st, f"两轨触发不一致：weight={wt} single={st}"
    assert wt, "脚本没有触发任何止损，这条用例什么也没验证"


def test_trailing_stop_agrees_across_tracks() -> None:
    """移动止损同口径（峰值都用 close）：两轨同日触发。

    **这条脚本是刻意挑的**：第二天 high 131 远高于 close 110，使得
    「high 峰值 → 第三天触发」与「close 峰值 → 第四天触发」落在不同日期。
    上一版脚本两者同日触发，把峰值基准从 close 换成 high 也照样绿——那正是
    空断言：它测了"两轨都触发"，没测"两轨用同一个基准"。
    """
    script = [
        ("100", "100", "99", "101"),
        ("130", "110", "109", "131"),
        ("108", "101", "100", "109"),
        ("95", "91", "90", "96"),
    ]
    risk = {"trailing_stop_pct": "0.15"}

    spec = validate_spec({
        "kind": "weight", "universe": ["A"],
        "rebalance": {"every_n_bars": 1},
        "weights_series": {START.isoformat(): {"A": 1.0}},
    })
    weight = replay_weights(spec, {"A": _bars(script)}, Decimal("100000"),
                            risk=RiskConfig.from_dict(risk))

    spec2 = validate_spec({
        "kind": "template", "template": "dca", "symbol": "A",
        "params": {"interval_bars": 1, "quantity": 1},
    })
    one = asyncio.run(single.replay(
        _bars(script), single.build_evaluator(spec2),
        Decimal("100000"), None, risk=RiskConfig.from_dict(risk),
    ))

    wt = [(t["date"], t["kind"]) for t in (weight.risk_triggers or [])]
    st = [(t["date"], t["kind"]) for t in (one.risk_triggers or [])]
    assert wt == st, f"移动止损两轨不一致：weight={wt} single={st}"
    assert wt and wt[0][1] == "trailing_stop"
    # close 峰值 110 × (1−0.15) = 93.5 → 第四天 low 90 才破；若换成 high 峰值
    # （131 × 0.85 = 111.35）会在第三天就触发——日期断言把基准钉死。
    assert wt[0][0] == "2026-01-04", f"峰值基准不是 close：{wt}"


def test_cost_basis_is_the_weighted_average_of_two_buys() -> None:
    """两次建仓 100 股@10 + 100 股@12 ⇒ 均价 11（止损价随之从 9 变 9.9）。"""
    from fd_paper_trading.strategy.weight_backtester import _WeightPortfolio

    pf = _WeightPortfolio(Decimal("10000"))
    pf.apply_fill("A", Decimal(100), Decimal("10"))
    pf.apply_fill("A", Decimal(100), Decimal("12"))

    assert pf.position("A") == Decimal(200)
    assert pf.cost_of("A") == Decimal("11")


def test_cost_basis_resets_when_position_returns_to_zero() -> None:
    """平掉再建仓不能继承上一轮的 entry（否则第二次止损线会锚在旧价上）。"""
    from fd_paper_trading.strategy.weight_backtester import _WeightPortfolio

    pf = _WeightPortfolio(Decimal("10000"))
    pf.apply_fill("A", Decimal(100), Decimal("10"))
    pf.apply_fill("A", Decimal(-100), Decimal("20"))
    assert pf.cost_of("A") is None

    pf.apply_fill("A", Decimal(50), Decimal("30"))
    assert pf.cost_of("A") == Decimal("30")
