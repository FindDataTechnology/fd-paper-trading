"""统计扩展：新统计量一律问计算面要（design D2），不在本仓重算。

## 为什么这里没有统计公式

`backtester.py` 里原有的 `total_return`/`max_drawdown`/`sharpe`/`win_rate`/
`trade_count`/`final_equity` 是本仓历史实现，本变更**不动它们**（向后兼容：
既有字段逐位不变）。新增的五个字段（`annual_return`/`annual_volatility`/
`sortino`/`calmar`/`turnover`）则必须来自批 1.1 计算面——因为 spec 要求"计算
口径与计算面一致，并以跨仓对拍测试证明一致"，而"证明一致"的唯一稳固形式
是**同一份实现**，不是两份实现碰巧相等。

## 失败即失败

计算面不可用时 `ComputePlaneUnavailable` 一路抛到回测的 FAILED 分支，错误文本
写明"计算面不可用"。不降级、不缓存（design D2 的风险条目）。

## 为什么 sharpe 不换成计算面的

换掉会改变既有字段的值（计算面的 Sharpe 用样本标准差、年化方式也可能与旧实现
有末位差异），而 spec 要求旧字段逐字段不变（task 2.3 的基线对照）。旧 sharpe
保留、新字段外求，这是"向后兼容"与"口径统一"两个约束的交点。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Optional, Sequence

from . import compute_plane
from .compute_plane import ComputePlaneUnavailable  # noqa: F401  (re-export for callers)


def _as_float_curve(equity: Sequence[tuple[date, Decimal]]) -> tuple[list[str], list[float]]:
    """权益曲线 → (ISO 日期, float 值)。

    Decimal → float 是有损的，但计算面按 float64 契约收数（它面向的是序列
    统计，不是账务）。这里的损失与"把权益曲线喂给任何统计工具"不可避免的
    损失同源，且只影响新统计量——旧字段仍走 Decimal 路径。
    """
    dates = [d.isoformat() for d, _ in equity]
    values = [float(v) for _, v in equity]
    return dates, values


def extended_stats(
    equity: Sequence[tuple[date, Decimal]],
    *,
    as_of: Optional[str] = None,
    turnover_weights: Optional[Sequence[Sequence[float]]] = None,
) -> dict:
    """问计算面要五个新统计量。

    `turnover_weights` 是逐期权重序列（单标的策略没有权重概念，此时
    `turnover` 返回 None 并在 `notes` 里说明——空值有解释，不是静默缺失）。

    返回 dict 只含新字段 + `source`（标注这些数来自计算面，便于报告追溯）。
    """
    if len(equity) < 2:
        # 少于两个点算不出任何年化量。返回空值 + 原因，不伪造 0。
        return {
            "annual_return": None,
            "annual_volatility": None,
            "sortino": None,
            "calmar": None,
            "turnover": None,
            "source": "compute-plane",
            "notes": [f"equity curve has {len(equity)} point(s); annualised statistics need >= 2"],
        }

    dates, values = _as_float_curve(equity)
    cutoff = as_of or dates[-1]

    ret = compute_plane.summarize_return(dates, values, cutoff)
    vol = compute_plane.summarize_volatility(dates, values, cutoff)
    try:
        sortino = compute_plane.summarize_sortino(dates, values, cutoff)
    except ComputePlaneUnavailable as exc:
        # 零下行波动时 Sortino 在定义上无解；计算面拒绝，这里如实记 null。
        sortino = {"sortino": None, "notes": [str(exc)]}
    try:
        calmar = compute_plane.summarize_calmar(dates, values, cutoff)
    except ComputePlaneUnavailable as exc:
        calmar = {"calmar": None, "notes": [str(exc)]}

    notes: list[str] = []
    notes.extend(sortino.get("notes") or [])
    notes.extend(calmar.get("notes") or [])

    turnover = None
    if turnover_weights:
        # 逐期权重 → 相邻两期的换手，取最后一期（回测整体的"当期换手"口径）。
        # 用计算面的 summarize_turnover 而非本地算 |Δw|/2：同一份定义。
        prior = list(turnover_weights[0])
        cur = list(turnover_weights[-1]) if len(turnover_weights) > 1 else prior
        try:
            t = compute_plane.call_tool("summarize_turnover", {
                "dates": [cutoff], "weights": cur, "as_of": cutoff,
                "weights_prior": prior,
            })
            turnover = t.get("turnover")
        except ComputePlaneUnavailable as exc:
            notes.append(f"turnover unavailable: {exc}")
    else:
        notes.append("turnover is null: a single-symbol strategy has no weight vector to difference")

    return {
        # 键名取自计算面实测（不是推测）：年化收益是 `annualized_return`，
        # 年化波动是 `volatility`（该工具本身就只输出年化口径，另有
        # `per_period_volatility` 供需要逐期口径者用）。
        "annual_return": ret.get("annualized_return"),
        "annual_volatility": vol.get("volatility"),
        "sortino": sortino.get("sortino"),
        "calmar": calmar.get("calmar"),
        "turnover": turnover,
        "source": "compute-plane",
        "notes": notes or None,
    }
