"""基准对比：默认双宽基、缺数据明确报缺、相对指标三件套。

## 缺数据是"标注"不是"省略"

spec 与 design D5 都要求：基准不可用时**明确报缺**（指明符号与原因），不得静默
省略、不得伪造曲线。故 `fetch_benchmark` 永远返回一条记录，`available` 字段说明
它是否拿到了数据——这样"请求了 3 个基准但只回来 2 条"这种信息丢失不会发生。

## 相对指标问计算面要

超额收益、跟踪误差、信息比三者同源于 `summarize_information_ratio`（批 1.1）。
本模块只负责把两条曲线按日期对齐后交出去——对齐本身是必须做的（基准与策略的
交易日集合可能不同），但**指标的算术不在本仓**。

## 为什么对齐要"取交集"而不是"前值填充"

前值填充会发明不存在的交易日：若基准在某个策略交易日休市，填充等于假装基准
当天走平，跟踪误差因此被低估。取交集是唯一不引入虚构数据的做法，代价是两端
各丢几个点——这个代价明示在返回的 `aligned_points` 里。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from ..marketdata.provider import Bar
from . import compute_plane

#: 默认基准（design D5）：双宽基是 CN 语境下的合理起点
DEFAULT_BENCHMARKS = ("000300.SS", "000905.SS")


def _bar_date(bar: Bar) -> date:
    return datetime.fromtimestamp(bar.ts, tz=timezone.utc).date()


@dataclass
class BenchmarkResult:
    symbol: str
    available: bool
    reason: Optional[str] = None
    points: list[tuple[date, Decimal]] | None = None
    stats: dict | None = None
    relative: dict | None = None

    def to_dict(self) -> dict:
        """JSON 形态。`points` 是 [(date, Decimal)]，这里转成可序列化的对。"""
        return {
            "symbol": self.symbol,
            "available": self.available,
            "reason": self.reason,
            "points": (
                [[d.isoformat(), str(v)] for d, v in self.points]
                if self.points is not None else None
            ),
            "stats": self.stats,
            "relative": self.relative,
        }


async def fetch_benchmark(
    provider, symbol: str, start: date, end: date
) -> tuple[Optional[list[Bar]], Optional[str]]:
    """取一个基准的日线。返回 `(bars, reason)`；失败时 bars 为 None 且 reason 说明原因。

    单个基准取数失败**不抛异常**：spec 要求"策略自身结果仍正常返回"，故失败
    降级为该基准的一条 `available: false` 记录。这与计算面不可用时整轮 FAILED
    不同——计算面缺失是口径问题（算不出来），基准缺失只是少一个参照物。
    """
    days = (end - start).days + 1
    try:
        bars = await provider.get_bars(symbol, "1d", days)
    except Exception as exc:
        return None, f"provider error for {symbol}: {type(exc).__name__}: {exc}"
    window = [b for b in bars if start <= _bar_date(b) <= end]
    if not window:
        return None, f"no bar data for benchmark {symbol} in {start}..{end}"
    return window, None


def relative_metrics(
    strategy_points: Sequence[tuple[date, Decimal]],
    benchmark_points: Sequence[tuple[date, Decimal]],
) -> dict:
    """按日期交集对齐两条曲线，交计算面算相对指标。"""
    strat = {d: v for d, v in strategy_points}
    bench = {d: v for d, v in benchmark_points}
    shared = sorted(set(strat) & set(bench))
    if len(shared) < 2:
        return {
            "excess_return": None,
            "tracking_error": None,
            "information_ratio": None,
            "aligned_points": len(shared),
            "reason": (
                f"only {len(shared)} shared trading day(s) between strategy and "
                f"benchmark; relative metrics need at least 2"
            ),
        }
    dates = [d.isoformat() for d in shared]
    s_vals = [float(strat[d]) for d in shared]
    b_vals = [float(bench[d]) for d in shared]
    try:
        r = compute_plane.summarize_information_ratio(
            dates, s_vals, b_vals, as_of=dates[-1]
        )
    except compute_plane.ComputePlaneUnavailable as exc:
        return {
            "excess_return": None,
            "tracking_error": None,
            "information_ratio": None,
            "aligned_points": len(shared),
            "reason": f"compute plane unavailable for relative metrics: {exc}",
        }
    return {
        # 计算面的 IR 工具同时给出 active return 的均值与离散度；超额收益取
        # 两条曲线首末的总收益差（"策略这段时间比基准多赚了多少"是报告口径）。
        "excess_return": (s_vals[-1] / s_vals[0] - 1.0) - (b_vals[-1] / b_vals[0] - 1.0),
        "tracking_error": r.get("tracking_error"),
        "information_ratio": r.get("information_ratio"),
        "aligned_points": len(shared),
        "source": "compute-plane",
    }


def benchmark_stats(points: Sequence[tuple[date, Decimal]]) -> dict:
    """基准自身的收益/波动/回撤（问计算面要，同一口径）。"""
    dates = [d.isoformat() for d, _ in points]
    values = [float(v) for _, v in points]
    as_of = dates[-1]
    out: dict[str, Any] = {}
    for key, fn in (("total_return", compute_plane.summarize_return),
                    ("volatility", compute_plane.summarize_volatility),
                    ("max_drawdown", compute_plane.summarize_drawdown)):
        try:
            r = fn(dates, values, as_of)
        except compute_plane.ComputePlaneUnavailable as exc:
            out[key] = None
            out.setdefault("notes", []).append(f"{key}: {exc}")
            continue
        if key == "total_return":
            out[key] = r.get("total_return")
        elif key == "volatility":
            out[key] = r.get("volatility")
        else:
            out[key] = r.get("max_drawdown")
    return out
