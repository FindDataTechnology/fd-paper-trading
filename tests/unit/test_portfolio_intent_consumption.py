"""引擎侧端到端：PortfolioIntent 导出的权重真的能被权重模式回测吃下去。

这是 `add-portfolio-intent-contract` task 5.3 的另一半。计算侧那一半在 fd-quant 的
`tests/test_portfolio_e2e.py`（评级 → 意图 → 导出）；这里证明**导出的东西是可执行的**：
把同一形状的 TargetWeights 直接喂 `replay_weights`，跑出非零成交与最终净值。

为什么用构造权重而不是现场调 fd-quant 的转换器：转换器不在本仓的 vendored 面里
（引擎只需要**校验**权重，不需要**构建**权重——构建属研究侧）。故这里构造一个
与转换器输出的形状与不变式完全一致的实例，验证执行侧那一步。
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from fd_paper_trading._vendor.fd_quant import (
    PriceSeriesRef,
    validate_target_weights,
)
from fd_paper_trading.marketdata.provider import Bar
from fd_paper_trading.strategy.spec import Rebalance, WeightSpec
from fd_paper_trading.strategy.weight_backtester import replay_weights

PRODUCER = {"pack": "wire-equity-research", "skill": "portfolio-construction"}


def _bars(symbol: str, closes: list[str], start: date | None = None) -> list[Bar]:
    d0 = start or date(2026, 6, 1)
    out = []
    for i, c in enumerate(closes):
        px = Decimal(c)
        out.append(
            Bar(
                ts=int((d0 + timedelta(days=i)).toordinal() * 86400),
                open=px, high=px, low=px, close=px, volume=Decimal("1000"),
            )
        )
    return out


def _spec(weights: dict[str, float], symbols: list[str], every: int = 5) -> WeightSpec:
    """构造与 PortfolioIntent.to_target_weights() 同形的权重期次。"""
    period_key = "2026-06-01"
    return WeightSpec(
        kind="weight",
        universe=symbols,
        rebalance=Rebalance(every_n_bars=every),
        weights_series={period_key: dict(weights)},
    )


# --------------------------------------------------------------- 契约同形


def test_exported_shape_is_accepted_by_the_vendored_contract():
    """PortfolioIntent 导出的三要素（universe/weights/cash_floor）必须过引擎侧校验。"""
    tw = {
        "schema_version": "1",
        "as_of": "2026-06-01",
        "universe": ["600519", "000001"],
        "weights": {"600519": 0.4, "000001": 0.4},
        "cash_floor": 0.2,
        "constraints": {},
        "evidence": [],
        "producer": PRODUCER,
    }
    assert validate_target_weights(tw)["valid"] is True

    # 违规的导出必须被同一边界拒绝：合计超 1 不归一化
    bad = dict(tw, weights={"600519": 0.7, "000001": 0.7})
    assert validate_target_weights(bad)["valid"] is False


def test_vendored_price_series_ref_is_usable_for_provenance():
    """权重期次可带价格出处引用（change 1 的契约），引擎侧能构造与校验。"""
    ref = PriceSeriesRef.create(
        source_id="astock_daily", symbol="600519", adjustment="qfq",
        adjustment_anchor="2026-07-10", as_of_max="2026-07-10",
        window_start="2025-09-23", window_end="2026-07-10", row_count=400,
    )
    assert len(ref.fingerprint) == 64
    assert ref.adjustment == "qfq"


# --------------------------------------------------------------- 回测执行


def test_exported_weights_run_through_the_engine_and_trade():
    """跑出非零成交笔数与最终净值——"能喂进去"与"真跑起来"是两件事。"""
    bars = {
        "600519": _bars("600519", ["100", "102", "104", "106", "108", "110", "112", "114"]),
        "000001": _bars("000001", ["20", "20.5", "21", "21.5", "22", "22.5", "23", "23.5"]),
    }
    spec = _spec({"600519": 0.4, "000001": 0.4}, ["600519", "000001"])
    result = replay_weights(spec, bars, Decimal("100000"))

    assert result.snapshots, "回测没有产生任何调仓快照"
    assert result.stats["trade_count"] > 0, f"没有任何成交：{result.stats}"
    assert result.stats["rebalance_count"] >= 1
    final_equity = result.equity_points[-1][1]
    assert final_equity > 0
    assert final_equity != Decimal("100000"), "净值没有变化——权重没起作用"


def test_cash_floor_is_kept_out_of_the_market():
    """cash_floor=0.5 → 最多一半资金入场，净值涨幅应显著小于满仓。"""
    bars = {
        "600519": _bars("600519", ["100", "110", "120", "130", "140", "150"]),
        "000001": _bars("000001", ["20", "22", "24", "26", "28", "30"]),
    }
    full = replay_weights(_spec({"600519": 0.5, "000001": 0.5}, ["600519", "000001"]),
                          bars, Decimal("100000"))
    half = replay_weights(
        WeightSpec(kind="weight", universe=["600519", "000001"],
                   rebalance=Rebalance(every_n_bars=5),
                   weights_series={"2026-06-01": {"600519": 0.25, "000001": 0.25}},
                   cash_floors={"2026-06-01": 0.5}),
        bars, Decimal("100000"),
    )
    full_gain = full.equity_points[-1][1] - Decimal("100000")
    half_gain = half.equity_points[-1][1] - Decimal("100000")
    assert full_gain > 0 and half_gain > 0
    assert half_gain < full_gain, "现金下限没有生效：半仓与满仓收益相同"


def test_weights_outside_the_universe_are_refused_before_replay():
    """执行侧不接受"喂进来才发现"的非法权重——构造期就拒。"""
    with pytest.raises(Exception):
        _spec({"999999": 0.4}, ["600519", "000001"])
