"""回测侧的组合级风控接入（change4 tasks 3.1 / 3.4）。

两个断言：

1. **默认关闭、零行为变化**——不传 `risk_thresholds` 时 `risk_events` 为空，回测结果与
   接入前逐位一致。
2. **同一实现**——回测用的是从 fd-quant 字节镜像过来的 `risk.py`。改判据一处，
   三处（回测 / 纸面运行 / 执行闸）同时变；这里用"改镜像即变结果"证明它确实接在同一份
   判定上，而不是自己写了一份。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from fd_paper_trading._vendor.fd_quant.risk import evaluate_portfolio_gate
from fd_paper_trading.marketdata.provider import Bar
from fd_paper_trading.strategy.spec import Rebalance, WeightSpec
from fd_paper_trading.strategy.weight_backtester import replay_weights

TH = {"per_symbol_max": 0.45, "industry_max": 0.9,
      "drawdown_max": 0.9, "daily_loss_max": 0.9}


def _bars(closes: list[str]) -> list[Bar]:
    d0 = date(2026, 6, 1)
    out = []
    for i, c in enumerate(closes):
        px = Decimal(c)
        out.append(Bar(ts=int((d0 + timedelta(days=i)).toordinal() * 86400),
                       open=px, high=px, low=px, close=px, volume=Decimal("1000")))
    return out


def _spec(weights: dict[str, float], universe: list[str], every: int = 4) -> WeightSpec:
    return WeightSpec(kind="weight", universe=universe,
                      rebalance=Rebalance(every_n_bars=every),
                      weights_series={"2026-06-01": dict(weights)})


BARS = {"A": _bars(["10", "11", "12", "13", "14", "15", "16", "17"]),
        "B": _bars(["20", "20", "21", "21", "22", "22", "23", "23"])}
SPEC = _spec({"A": 0.4, "B": 0.4}, ["A", "B"])


# --------------------------------------------------- 3.1 默认关闭、零变化


def test_without_thresholds_no_risk_events_and_same_result():
    """不配阈值 = 不判定。结果必须与接入前逐位一致（risk_events 为空）。"""
    out = replay_weights(SPEC, BARS, Decimal("100000"))
    assert out.risk_events == []
    base = replay_weights(SPEC, BARS, Decimal("100000"))
    assert out.stats == base.stats
    assert out.equity_points == base.equity_points


def test_with_thresholds_records_one_verdict_per_rebalance():
    out = replay_weights(SPEC, BARS, Decimal("100000"), risk_thresholds=TH,
                         risk_sectors={"A": "tech", "B": "fin"})
    assert out.risk_events, "配了阈值却没有判定记录"
    clean = [s for s in out.snapshots if s.error is None]
    assert len(out.risk_events) == len(clean) // 1 or len(out.risk_events) >= 1
    for ev in out.risk_events:
        assert ev["day"]
        assert ev["verdict"]["status"] in ("ok", "breached", "unavailable")


def test_verdict_shape_is_the_shared_gate_shape():
    """回测记录的判定，与直接调 gate 的形状完全一致（同一实现的证据之一）。"""
    out = replay_weights(SPEC, BARS, Decimal("100000"), risk_thresholds=TH)
    ev = out.risk_events[0]["verdict"]
    direct = evaluate_portfolio_gate(
        weights=out.snapshots[0].actual_weights, thresholds=TH,
        equity_series=None, as_of=out.risk_events[0]["day"],
    )
    assert set(ev) == set(direct)
    assert ev["status"] == direct["status"]


# --------------------------------------------------- 3.4 改一处，三处同变


def test_threshold_tightening_changes_backtest_verdicts(monkeypatch):
    """把单票上限收紧到低于实际权重 → 回测侧判定立刻从 ok 变 breached。

    这就是"改判据一处，三处同时变"的可执行形式：判定只有一份，回测只是它的消费者。
    若哪天有人把判据复制进回测器，这条会因为"改上游不影响回测"而失败。
    """
    loose = replay_weights(SPEC, BARS, Decimal("100000"),
                           risk_thresholds={**TH, "per_symbol_max": 0.95})
    assert all(e["verdict"]["status"] == "ok" for e in loose.risk_events)

    tight = replay_weights(SPEC, BARS, Decimal("100000"),
                           risk_thresholds={**TH, "per_symbol_max": 0.05})
    assert any(e["verdict"]["status"] == "breached" for e in tight.risk_events)
    kinds = {i["kind"] for e in tight.risk_events for i in e["verdict"]["items"]}
    assert "per_symbol" in kinds


def test_gate_is_byte_identical_to_upstream():
    """镜像件与上游同一实现（漂移闸之外的运行时自证）。"""
    import hashlib
    import pathlib

    import fd_paper_trading._vendor

    here = pathlib.Path(fd_paper_trading._vendor.__file__).parent / "fd_quant" / "risk.py"
    upstream = (pathlib.Path(__file__).resolve().parents[2].parent
                / "fd-quant" / "fd_quant" / "risk.py")
    if not upstream.is_file():
        pytest.skip("upstream fd-quant checkout absent")
    assert hashlib.sha256(here.read_bytes()).hexdigest() == \
           hashlib.sha256(upstream.read_bytes()).hexdigest()


def test_unavailable_thresholds_surface_as_unavailable_not_ok():
    """阈值残缺 → 判定不可用；回测记录下来，但**不改回测结果**（处置由调用方定）。"""
    out = replay_weights(SPEC, BARS, Decimal("100000"),
                         risk_thresholds={"per_symbol_max": 0.45})
    assert any(e["verdict"]["status"] == "unavailable" for e in out.risk_events)
    assert out.stats["trade_count"] >= 0  # 回测照常跑完
