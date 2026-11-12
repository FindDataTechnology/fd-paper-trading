"""Score-proportional allocation (factor-real-cross-section item 3, score track).

The spec requires **both** weight sources — equal weight and score weight — to
run through the real weight-mode backtester (COMPLETED, fills > 0). The equal
track has evidence in ``openspec/changes/archive/2026-11-06-add-trading-real-data-closeout/reports/``;
this file pins the score track so that it cannot silently rot back into "only
equal weight was ever executed".

Two properties are load-bearing and each has its own test:

1. the allocation sums to the budget *exactly* (a float residue silently becomes
   cash, and the drift then reads as a strategy decision);
2. a score-shaped series of ten names actually executes in the replay engine with
   fills > 0 — the spec's acceptance wording, not a proxy for it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from fd_paper_trading.marketdata.provider import Bar
from fd_paper_trading.strategy.allocation import score_proportional_weights
from fd_paper_trading.strategy.spec import validate_spec
from fd_paper_trading.strategy.weight_backtester import replay_weights

#: 一条真实的分数形状：十个名字，分数从 1.0 递减到 0.1（ROE 排序后的常见形态）。
TAPERED = {f"S{i:02d}": round(1.0 - 0.1 * i, 1) for i in range(10)}


def _bar(day: int, close: str) -> Bar:
    ts = datetime(2026, 1, day, tzinfo=timezone.utc).timestamp()
    price = Decimal(close)
    return Bar(ts=ts, open=price, high=price, low=price, close=price, volume=Decimal(1000))


def _bars_for(symbols: list[str]) -> dict[str, list[Bar]]:
    """三天平价行情：所有名字同价，使"买到了几只"只由权重与现金决定。"""
    return {s: [_bar(1, "10.0"), _bar(2, "10.0"), _bar(3, "10.0")] for s in symbols}


# ---------------------------------------------------------------------------
# 分配算术
# ---------------------------------------------------------------------------


def test_allocation_sums_to_budget_exactly() -> None:
    """Σ 必须**等于**预算，不是"约等于"——浮点余数会静默变成现金。"""
    weights = score_proportional_weights(TAPERED, budget=0.9)
    assert sum(weights.values()) == pytest.approx(0.9, abs=1e-12)
    # 用整数末位核验，避开 approx 的宽容：4 位小数下必须正好 9000 个末位单位。
    assert sum(round(w * 10000) for w in weights.values()) == 9000


def test_allocation_is_monotone_in_score() -> None:
    """分数高的不能拿到比分数低的更少的预算。

    只判"非递增"会退化成空断言——等权（全体相同）也满足非递增。所以这里
    另外要求**严格**递减，并且先证明分配没有塌成等权。这条是注入对照逼出来的：
    把分配改成 1.0/N 后，仅判非递增的版本照样全绿。
    """
    weights = score_proportional_weights(TAPERED, budget=0.9)
    ordered = [weights[f"S{i:02d}"] for i in range(10)]
    assert len(set(ordered)) == len(ordered), f"十个不同分数拿到了重复权重：{ordered}"
    assert ordered == sorted(ordered, reverse=True)
    assert all(a > b for a, b in zip(ordered, ordered[1:])), ordered


def test_higher_score_gets_strictly_more_when_share_is_representable() -> None:
    weights = score_proportional_weights({"A": 3.0, "B": 1.0}, budget=0.8)
    assert weights["A"] == pytest.approx(0.6)
    assert weights["B"] == pytest.approx(0.2)


def test_zero_scores_are_dropped_not_given_a_token_slice() -> None:
    weights = score_proportional_weights({"A": 1.0, "B": 0.0, "C": 1.0}, budget=0.9)
    assert "B" not in weights
    assert sum(weights.values()) == pytest.approx(0.9, abs=1e-12)


def test_negative_score_is_refused_rather_than_clipped() -> None:
    """负分意味着调用方还没决定空头怎么表示；夹到 0 会把它变成静默的多头决定。"""
    with pytest.raises(ValueError, match="negative"):
        score_proportional_weights({"A": 1.0, "B": -0.5})


def test_no_positive_score_is_refused() -> None:
    with pytest.raises(ValueError, match="no positive score"):
        score_proportional_weights({"A": 0.0, "B": 0.0})


def test_budget_and_decimals_are_bounded() -> None:
    with pytest.raises(ValueError, match="budget"):
        score_proportional_weights({"A": 1.0}, budget=0)
    with pytest.raises(ValueError, match="budget"):
        score_proportional_weights({"A": 1.0}, budget=1.5)
    with pytest.raises(ValueError, match="decimals"):
        score_proportional_weights({"A": 1.0}, decimals=-1)


def test_stray_symbol_is_reported_against_the_universe() -> None:
    with pytest.raises(ValueError, match="outside universe"):
        score_proportional_weights({"A": 1.0, "Z": 1.0}, universe=["A", "B"])


def test_full_budget_is_allowed() -> None:
    """budget=1 合法（无现金留量）；契约的 Σ ≤ 1 由 validate_weights 兜底。"""
    weights = score_proportional_weights({"A": 1.0, "B": 1.0}, budget=1.0)
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-12)


# ---------------------------------------------------------------------------
# 端到端：打分权重真的进引擎（spec 的验收原话：COMPLETED 且成交数 > 0）
# ---------------------------------------------------------------------------


def test_score_weighted_series_executes_in_the_real_engine() -> None:
    weights = score_proportional_weights(TAPERED, budget=0.9)
    symbols = list(weights)
    spec = validate_spec(
        {
            "kind": "weight",
            "universe": symbols,
            "rebalance": {"every_n_bars": 1},
            "weights_series": {"2026-01-01": dict(weights)},
        }
    )
    result = replay_weights(spec, _bars_for(symbols), Decimal("100000"))

    filled = int(result.stats.get("trade_count") or 0)
    assert filled > 0, "打分权重没有产生任何成交，spec 2.3 的验收不成立"
    assert filled <= sum(len(s.instructions) for s in result.snapshots), (
        "成交数超过指令数：统计口径不可信"
    )

    # 快照里的目标必须就是分配出来的那一份，而不是引擎重新等权了。
    snapshot = result.snapshots[0]
    for symbol, weight in weights.items():
        assert snapshot.target_weights[symbol] == pytest.approx(weight, abs=1e-9)


def test_score_series_rebalances_to_a_new_ranking() -> None:
    """第二期换了排名：旧第一名降权、新第一名加权，两次都应有成交。"""
    first = score_proportional_weights({"A": 1.0, "B": 0.2}, budget=0.9)
    second = score_proportional_weights({"A": 0.2, "B": 1.0}, budget=0.9)
    spec = validate_spec(
        {
            "kind": "weight",
            "universe": ["A", "B"],
            "rebalance": {"every_n_bars": 1},
            "weights_series": {"2026-01-01": first, "2026-01-03": second},
        }
    )
    result = replay_weights(spec, _bars_for(["A", "B"]), Decimal("100000"))

    assert int(result.stats.get("trade_count") or 0) > 0
    assert result.snapshots[-1].target_weights["B"] == pytest.approx(second["B"], abs=1e-9)


# ---------------------------------------------------------------------------
# 分层规模下限：spec 要求「5 层、每层 ≥5 只」，只断言"5 层都在"是假绿
# ---------------------------------------------------------------------------


def _layer_sizes(common: list[str], fwd: dict[str, float], n_layers: int = 5) -> list[int]:
    """照抄证据脚本里的切片与"有效成员"数法，用来钉住它的语义。

    切片末层吃余数（`common[4*size:]`），而规模按**有前瞻收益的成员数**算——
    两者不是一回事：切片里有名字、但缺前瞻收益时，均值是用更小样本算的。
    """
    n = len(common)
    layer_size = n // n_layers
    sizes = []
    for layer in range(n_layers):
        members = common[layer * layer_size:(layer + 1) * layer_size] \
            if layer < n_layers - 1 else common[(n_layers - 1) * layer_size:]
        sizes.append(sum(1 for e in members if e in fwd))
    return sizes


def test_layer_floor_passes_when_every_layer_has_five() -> None:
    common = [f"E{i:02d}" for i in range(30)]
    fwd = {e: 0.01 for e in common}
    sizes = _layer_sizes(common, fwd)
    assert sizes == [6, 6, 6, 6, 6]
    assert min(sizes) >= 5


def test_layer_floor_fails_when_a_layer_is_starved() -> None:
    """12 只切 5 层 → 每层 2 只。老断言（5 层都有均值）照样 PASS，新门必须红。"""
    common = [f"E{i:02d}" for i in range(12)]
    fwd = {e: 0.01 for e in common}
    sizes = _layer_sizes(common, fwd)
    assert sizes == [2, 2, 2, 2, 4]
    assert min(sizes) < 5, "每层只有 2 只却通过了规模下限：门是假的"


def test_layer_size_counts_only_members_with_forward_returns() -> None:
    """切片里有名字但缺前瞻收益时，规模按有效成员算（不是切片长度）。"""
    common = [f"E{i:02d}" for i in range(30)]
    fwd = {e: 0.01 for e in common[:26]}  # 最后 4 只没有前瞻收益
    sizes = _layer_sizes(common, fwd)
    assert sizes[-1] == 2, sizes  # 末层 6 个名字里只有 2 个有效
    assert min(sizes) < 5
