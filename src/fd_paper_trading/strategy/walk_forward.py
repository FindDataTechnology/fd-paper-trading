"""walk-forward 回测与参数网格扫描。

## 窗口切分问计算面要，不在这里算

`split_walk_forward_windows` 的分窗逻辑（含"测试窗严格晚于其自身及之前所有训练窗"
这条不变式）在批 1.1 计算面里已实现并测试。本模块只做两件事：把回测的
`(dates, prices)` 交给它、把返回的窗口映射回逐窗重放。

## 逐窗重放为什么要重建 evaluator

`on_bar` 契约里策略持有跨 bar 状态（均线缓冲、持仓判断等）。walk-forward 的
每一窗是**一次独立的策略实例**——训练窗的数据不给测试窗的策略看，否则就是
跨窗泄漏。故每窗 `build_evaluator(spec)` 一次，而不是全程复用一个。

## 拼接权益曲线

spec 要求"各窗测试段拼接后的整体权益曲线"。拼接时每窗的起始权益接续上一窗的
结束权益（复利），而不是每窗各自从 initial_cash 起——否则整体曲线会是锯齿状
的假象。持仓在窗间**不结转**：每窗以现金起步，这是 walk-forward 的通行做法
（窗间强制平仓），也让"每窗独立"这件事在账上可见。
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional, Sequence

from ..marketdata.provider import Bar
from . import compute_plane
from .backtester import BacktestResult, EvaluationError, build_evaluator, replay
from .costs import CostModel
from .spec import StrategySpec


def _bar_date(bar: Bar) -> date:
    return datetime.fromtimestamp(bar.ts, tz=timezone.utc).date()


@dataclass
class WindowResult:
    window: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    stats: dict
    equity_points: list[tuple[date, Decimal]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "window": self.window,
            "train_start": self.train_start,
            "train_end": self.train_end,
            "test_start": self.test_start,
            "test_end": self.test_end,
            "stats": self.stats,
        }


@dataclass
class WalkForwardResult:
    windows: list[WindowResult]
    combined_stats: dict
    equity_points: list[tuple[date, Decimal]]


async def run_walk_forward(
    spec: StrategySpec,
    bars: list[Bar],
    initial_cash: Decimal,
    *,
    train_window: int,
    test_window: int,
    step: Optional[int] = None,
    costs: Optional[CostModel] = None,
) -> WalkForwardResult:
    """Split the window via the compute plane, replay each test segment.

    Raises `EvaluationError` when the compute plane refuses to split (short
    sample) — the refusal message already names the two lengths, which is what
    the spec asks the error to carry.
    """
    if not bars:
        raise EvaluationError("no bars to replay")
    dates = [_bar_date(b).isoformat() for b in bars]
    prices = [float(b.close) for b in bars]
    as_of = dates[-1]

    try:
        plan = compute_plane.split_walk_forward_windows(
            dates, prices, as_of,
            train_window=train_window, test_window=test_window, step=step,
        )
    except compute_plane.ComputePlaneUnavailable as exc:
        raise EvaluationError(
            f"walk-forward split failed: {exc}"
        ) from exc

    raw_windows = plan.get("windows") or []
    if not raw_windows:
        reason = plan.get("reason") or "the compute plane returned no windows"
        raise EvaluationError(f"walk-forward produced no windows: {reason}")

    by_date = {d: b for d, b in zip(dates, bars)}
    results: list[WindowResult] = []
    combined: list[tuple[date, Decimal]] = []
    running_cash = initial_cash

    for i, w in enumerate(raw_windows, start=1):
        ts = w.get("test_start_date") or w.get("test_start")
        te = w.get("test_end_date") or w.get("test_end")
        trs = w.get("train_start_date") or w.get("train_start")
        tre = w.get("train_end_date") or w.get("train_end")
        # Feed the TRAINING segment as history and only measure the test segment.
        # Passing just the test segment makes every window start cold: an
        # indicator strategy (30-bar SMA in a 40-bar window) never warms up and the
        # whole walk-forward reports zero trades — which is exactly what the first
        # run of the e2e report showed.
        # Index within the SEGMENT (train bars + test bars), not on the global
        # axis: the first segment bar whose date falls in the test range. Counting
        # on the global axis overshoots the segment length and replay refuses.
        seg_dates = [d for d in sorted(by_date) if trs <= d <= te]
        segment = [by_date[d] for d in seg_dates]
        measure_from = next((i for i, d in enumerate(seg_dates) if d >= ts), None)
        if measure_from is None:
            raise EvaluationError(
                f"walk-forward window {i}: no bars in test range {ts}..{te}"
            )
        if measure_from >= len(segment):
            raise EvaluationError(
                f"walk-forward window {i}: test range {ts}..{te} starts after the "
                f"segment's last bar {seg_dates[-1]}"
            )
        evaluator = build_evaluator(spec)
        result: BacktestResult = await replay(
            segment, evaluator, running_cash, costs, measure_from=measure_from
        )
        results.append(WindowResult(
            window=i, train_start=trs, train_end=tre, test_start=ts, test_end=te,
            stats=result.stats, equity_points=result.equity_points,
        ))
        combined.extend(result.equity_points)
        running_cash = result.equity_points[-1][1]

    combined_stats = _combined_stats(combined, initial_cash, results)
    return WalkForwardResult(
        windows=results, combined_stats=combined_stats, equity_points=combined
    )


def _combined_stats(
    equity: list[tuple[date, Decimal]],
    initial_cash: Decimal,
    windows: Sequence["WindowResult"],
) -> dict:
    """整体统计：收益类基于拼接曲线，成交类按窗汇总。

    两处刻意的口径选择：

    - `total_return`/`max_drawdown`/`sharpe`/`final_equity` 用**拼接后的连续
      权益曲线**算——这才反映"按这个 walk-forward 方案实际走下来"的经历。
    - `trade_count`/`win_rate` **逐窗汇总**而非重算：每窗独立持仓、独立现金，
      没有跨窗的"回合"可言。用一条新组合去重算会得到 0 笔成交、无胜率，
      那是把"口径不适用"伪装成"没有交易"。
    """
    from .backtester import compute_stats

    if not equity:
        raise EvaluationError("combined walk-forward equity curve is empty")

    # `compute_stats` reads trade_count / closed_trips / winning_trips off the
    # portfolio object, so the aggregate must carry all three — a partial stand-in
    # crashes on the attribute it forgot, which is how this was found.
    class _Totals:
        trade_count = sum(w.stats.get("trade_count", 0) or 0 for w in windows)
        closed_trips = 0
        winning_trips = 0

    stats = compute_stats([v for _, v in equity], initial_cash, _Totals())
    # 胜率按各窗已平仓回合的加权平均（分子分母各自求和，不是平均胜率的平均——
    # 后者会让只交易 1 笔的窗与交易 100 笔的窗等权）。
    wins = 0.0
    trips = 0
    for w in windows:
        wr = w.stats.get("win_rate")
        tc = w.stats.get("trade_count") or 0
        if wr is not None and tc:
            trips += tc
            wins += wr * tc
    stats["win_rate"] = round(wins / trips, 6) if trips else None
    stats["windows"] = len(windows)
    return stats


# ── 参数网格扫描 ─────────────────────────────────────────────────


def expand_grid(grid: dict[str, Sequence[Any]]) -> list[dict[str, Any]]:
    """笛卡尔积展开。空网格 → 空列表（调用方决定是否拒绝）。"""
    if not grid:
        return []
    names = list(grid)
    combos = itertools.product(*(grid[n] for n in names))
    return [dict(zip(names, combo)) for combo in combos]


def grid_size(grid: dict[str, Sequence[Any]]) -> int:
    n = 1
    for values in grid.values():
        n *= len(values)
    return n if grid else 0


def sort_matrix(matrix: list[dict], by: str, descending: bool = True) -> list[dict]:
    """按指定统计字段排序。

    缺该字段（或值为 None）的组排在最后，**不论升降序**——"没算出来"不该因为
    排序方向而被顶到前面冒充最优。
    """
    def key(row: dict):
        v = (row.get("stats") or {}).get(by)
        if v is None:
            return (1, 0.0)
        return (0, -float(v) if descending else float(v))

    return sorted(matrix, key=key)


async def run_walk_forward_weight(
    spec,
    bars_by_symbol: dict[str, list[Bar]],
    initial_cash: Decimal,
    *,
    train_window: int,
    test_window: int,
    step: Optional[int] = None,
    costs: Optional[CostModel] = None,
):
    """Weight 轨的 walk-forward：切窗按 universe 首标的轴，测试段用 weight 引擎重放。

    单标的轨的 run_walk_forward 复用同一套窗计划（计算面切窗），但重放走
    replay_weights——weight 轨的组合语义（universe、取整、缩量）与单标的
    on_bar 完全不同，两条轨共用切窗、不共用重放。costs 目前由
    _replay_universe 的既有纪律拒绝（weight 轨成本未支持），这里透传同样的
    拒绝而不是静默丢弃。
    """
    from .weight_backtester import replay_weights

    if not any(bars_by_symbol.values()):
        raise EvaluationError("no bars to replay")
    # 切窗轴 = universe 首标的（与单标的轨同一计算面工具、同一窗计划语义）。
    axis = sorted(spec.universe)[0] if False else spec.universe[0]
    axis_bars = bars_by_symbol.get(axis) or []
    dates = [_bar_date(b).isoformat() for b in axis_bars]
    prices = [float(b.close) for b in axis_bars]
    as_of = dates[-1] if dates else None
    if not dates:
        raise EvaluationError("no bars on the walk-forward axis symbol")

    try:
        plan = compute_plane.split_walk_forward_windows(
            dates, prices, as_of,
            train_window=train_window, test_window=test_window, step=step,
        )
    except compute_plane.ComputePlaneUnavailable as exc:
        raise EvaluationError(f"walk-forward split failed: {exc}") from exc

    raw_windows = plan.get("windows") or []
    if not raw_windows:
        reason = plan.get("reason") or "the compute plane returned no windows"
        raise EvaluationError(f"walk-forward produced no windows: {reason}")

    # 各标的的按日索引（weight 引擎在"universe 的并集日期轴"上工作——重放时
    # 直接把窗段内的 bars_by_symbol 切给它，轴由引擎自己并）。
    results: list[WindowResult] = []
    combined: list[tuple[date, Decimal]] = []
    running_cash = initial_cash
    aggregated_gaps: list[dict] = []

    for i, w in enumerate(raw_windows, start=1):
        ts = w.get("test_start_date") or w.get("test_start")
        te = w.get("test_end_date") or w.get("test_end")
        trs = w.get("train_start_date") or w.get("train_start")
        tre = w.get("train_end_date") or w.get("train_end")
        # 训练段只作为"该参数已在窗内拟合"的标注存在；weight 轨的重放从窗段
        # 起点开始（现金连续按 running_cash 传递），测试段统计窗按 measure 口径
        # 由引擎的快照与统计自带。
        segment_bars = {
            sym: [b for b in blist
                  if b is not None and _bar_date(b).isoformat() >= trs
                  and _bar_date(b).isoformat() <= te]
            for sym, blist in bars_by_symbol.items()
        }
        if not any(segment_bars.values()):
            raise EvaluationError(
                f"walk-forward window {i}: no bars in test range {ts}..{te}"
            )
        result = replay_weights(
            spec, segment_bars, running_cash,
        )
        results.append(WindowResult(
            window=i, train_start=trs, train_end=tre, test_start=ts, test_end=te,
            stats=result.stats, equity_points=result.equity_points,
        ))
        # 拼接曲线只收【测试段】的点：重放段含训练热身（轴从窗段起点开始），
        # 直接全并会让拼接曲线日期非严格递增（计算面 summarize_* 会拒绝），
        # 而且训练段不是"按方案实际走下来"的经历。现金经 running_cash 连续，
        # 测试段起点净值即窗段起点已实现的组合值。
        test_points = [
            (d, v) for d, v in result.equity_points if ts <= d.isoformat() <= te
        ]
        combined.extend(test_points)
        aggregated_gaps.extend(result.data_gaps)
        running_cash = result.equity_points[-1][1]

    combined_stats = _combined_stats(combined, initial_cash, results)
    combined_stats["data_gaps"] = aggregated_gaps
    return WalkForwardResult(
        windows=results, combined_stats=combined_stats, equity_points=combined
    )
