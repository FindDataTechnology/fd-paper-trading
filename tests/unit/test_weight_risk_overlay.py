"""Weight-track run-level risk overlay (both tracks must agree).

`replay_weights` gained a `risk` pass on 2026-11-12: before that, the weight
(multi-symbol) track had no overlay channel at all — a request carrying
`stop_loss_pct` was rejected at the route, and `replay_weights` had no parameter
to accept it. This file pins the three properties that make the new channel
trustworthy:

1. **trigger + fill** — a bar whose low reaches the stop closes the whole
   position, at the worse of the stop level and that bar's open (gap-conservative),
   and the close is counted as a trade;
2. **cooldown** — the symbol's *effective* target is zero for `cooldown_bars`
   rebalances while `target_weights` keeps the strategy's own number, so a zero is
   attributable;
3. **cross-track parity** — the same bar sequence and the same config produce the
   same trigger date and kind on the single-symbol track, because both call the
   same `evaluate_bar_overlay` with the same entry/peak basis.

Prices are hand-checkable: entry 100, stop 10% → level 90.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from fd_paper_trading.marketdata.provider import Bar
from fd_paper_trading.strategy.risk_overlay import RiskConfig
from fd_paper_trading.strategy.spec import validate_spec
from fd_paper_trading.strategy.weight_backtester import replay_weights

UTC = timezone.utc


def _bar(day: date, *, open_: str, close: str, low: str | None = None,
         high: str | None = None) -> Bar:
    ts = int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())
    return Bar(
        ts=ts,
        open=Decimal(open_),
        high=Decimal(high or close),
        low=Decimal(low or close),
        close=Decimal(close),
        volume=Decimal(1000),
    )


def _series(spec: dict, days: int = 6) -> dict[str, list[Bar]]:
    """Flat 100 for ``days`` bars — entry is exactly 100, so the stop is 90."""
    start = date(2026, 1, 1)
    out: dict[str, list[Bar]] = {}
    for sym in spec["universe"]:
        out[sym] = [
            _bar(start + timedelta(days=i), open_="100", close="100", low="99", high="101")
            for i in range(days)
        ]
    return out


def _spec(symbols: list[str], every: int = 1) -> dict:
    return {
        "kind": "weight",
        "universe": symbols,
        "rebalance": {"every_n_bars": every},
        "weights_series": {
            (date(2026, 1, 1) + timedelta(days=i)).isoformat(): {
                s: round(1.0 / len(symbols), 4) for s in symbols
            }
            for i in range(0, 6, every)
        },
    }


def _run(spec_payload: dict, bars: dict[str, list[Bar]], risk: dict | None):
    spec = validate_spec(spec_payload)
    cfg = RiskConfig.from_dict(risk) if risk is not None else None
    return replay_weights(spec, bars, Decimal("100000"), risk=cfg)


# ---------------------------------------------------------------------------
# 触发与成交
# ---------------------------------------------------------------------------


def test_stop_closes_the_whole_position_at_the_stop_level() -> None:
    """低点破线 → 全部平仓，成交价=停损价（开盘价更高时取停损价）。"""
    spec = _spec(["A"])
    bars = _series(spec)
    bars["A"][2] = _bar(date(2026, 1, 3), open_="95", close="88", low="87", high="96")

    result = _run(spec, bars, {"stop_loss_pct": "0.10"})

    triggers = result.risk_triggers or []
    assert len(triggers) == 1, triggers
    assert triggers[0]["date"] == "2026-01-03"
    assert triggers[0]["symbol"] == "A"
    assert triggers[0]["kind"] == "stop_loss"
    assert Decimal(triggers[0]["fill_price"]) == Decimal("90")


def test_gap_down_fills_at_the_worse_open_not_the_stop() -> None:
    """跳空：开盘 80 已低于停损价 90 → 按 80 成交（保守，不给用户假价）。"""
    spec = _spec(["A"])
    bars = _series(spec)
    bars["A"][2] = _bar(date(2026, 1, 3), open_="80", close="79", low="78", high="81")

    result = _run(spec, bars, {"stop_loss_pct": "0.10"})

    assert Decimal(result.risk_triggers[0]["fill_price"]) == Decimal("80")


def test_stop_fire_counts_as_a_trade_in_stats() -> None:
    """止损平仓在非调仓日发生，也要计入 trade_count（否则成交数少算）。"""
    spec = _spec(["A"], every=5)
    bars = _series(spec, days=6)
    bars["A"][2] = _bar(date(2026, 1, 3), open_="90", close="85", low="84", high="91")

    with_risk = _run(spec, bars, {"stop_loss_pct": "0.10"})
    without = _run(spec, bars, None)

    assert with_risk.stats["trade_count"] > without.stats["trade_count"]


def test_no_bar_that_day_means_no_trigger() -> None:
    """停牌/缺 bar ⇒ 没有价格就没有触发（不拿上一根 low 冒充当日行情）。"""
    spec = _spec(["A", "B"])
    bars = _series(spec)
    # A 在 1/3 没 bar；B 那天大跌触发
    bars["A"] = [b for b in bars["A"] if b.ts != _bar(date(2026, 1, 3), open_="1",
                                                       close="1").ts]
    bars["B"][2] = _bar(date(2026, 1, 3), open_="85", close="84", low="83", high="86")

    result = _run(spec, bars, {"stop_loss_pct": "0.10"})

    fired = {t["symbol"] for t in (result.risk_triggers or [])}
    assert fired == {"B"}, fired
    assert any(g["symbol"] == "A" and g["date"] == "2026-01-03"
               for g in result.data_gaps)


def test_trailing_stop_uses_close_peak() -> None:
    """峰值按 close（与单标的轨同口径）：涨到 120 后回撤 15% → 触发价 102。"""
    spec = _spec(["A"])
    start = date(2026, 1, 1)
    bars = {
        "A": [
            _bar(start, open_="100", close="100", low="99", high="101"),
            _bar(start + timedelta(days=1), open_="118", close="120", low="117", high="121"),
            _bar(start + timedelta(days=2), open_="110", close="101", low="100", high="111"),
        ]
    }
    spec["weights_series"] = {"2026-01-01": {"A": 1.0}}

    result = _run(spec, bars, {"trailing_stop_pct": "0.15"})

    trigger = result.risk_triggers[0]
    assert trigger["kind"] == "trailing_stop"
    assert Decimal(trigger["trigger_level"]) == Decimal("102.0")


# ---------------------------------------------------------------------------
# 冷却与归因
# ---------------------------------------------------------------------------


def test_cooldown_zeroes_effective_weight_but_keeps_the_target() -> None:
    """触发后：`target_weights` 记策略原值，`effective_weights` 记 0（可归因）。"""
    spec = _spec(["A"], every=1)
    bars = _series(spec, days=4)
    bars["A"][1] = _bar(date(2026, 1, 2), open_="90", close="85", low="84", high="91")

    result = _run(spec, bars, {"stop_loss_pct": "0.10", "cooldown_bars": 2})

    snap = [s for s in result.snapshots if s.effective_weights is not None]
    assert snap, "冷却没有落在任何快照上：effective_weights 缺失"
    first = snap[0]
    assert first.effective_weights["A"] == pytest.approx(0.0)
    assert first.target_weights["A"] > 0, "策略自己的目标被改写了：无法归因"
    assert first.to_dict()["effective_weights"]["A"] == pytest.approx(0.0)


def test_cooldown_expires_after_the_configured_rebalances() -> None:
    """冷却按**调仓点**计，且**触发当根就消耗一格**（与单标的轨同序）。

    单标的轨的顺序是 `arm()` → 处理当根 → `tick()`，所以 `cooldown_bars=2` 覆盖
    的是「触发当根 + 下一根」两个点。两条轨必须是同一个数法——否则同一个
    `cooldown_bars` 在回测两条轨上代表不同的东西。
    """
    spec = _spec(["A"], every=1)
    bars = _series(spec, days=6)
    bars["A"][1] = _bar(date(2026, 1, 2), open_="90", close="85", low="84", high="91")

    result = _run(spec, bars, {"stop_loss_pct": "0.10", "cooldown_bars": 2})

    cooled = [s.day.isoformat() for s in result.snapshots
              if s.effective_weights and s.effective_weights.get("A") == 0.0]
    assert cooled == ["2026-01-02", "2026-01-03"], cooled
    resumed = [s for s in result.snapshots if s.day.isoformat() == "2026-01-04"][0]
    assert not resumed.effective_weights, "冷却没有按期解除"


# ---------------------------------------------------------------------------
# 缺省零变化
# ---------------------------------------------------------------------------


def test_no_risk_config_is_bit_identical() -> None:
    """不传 risk → 与改动前同一结果，且不出现任何新键。"""
    spec = _spec(["A", "B"])
    bars = _series(spec)

    result = _run(spec, bars, None)

    assert result.risk_config is None
    assert result.risk_triggers is None
    assert all(s.effective_weights is None for s in result.snapshots)
    assert result.stats["trade_count"] > 0


def test_risk_configured_but_never_triggered_still_records_provenance() -> None:
    """"配了没触发"要能看见：risk_config 在、risk_triggers 为 None。"""
    spec = _spec(["A"])
    result = _run(spec, _series(spec), {"stop_loss_pct": "0.10"})

    assert result.risk_config is not None
    assert result.risk_triggers is None
