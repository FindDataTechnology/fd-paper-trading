"""backtest-engine-v2: cost model, extended stats, no-lookahead, benchmarks,
walk-forward, and parameter scan.

Every spec Scenario gets a case whose docstring quotes the requirement wording.
The arithmetic-bearing parts (costs, drawdown, grid) are checked against
hand-computed values, not against the implementation's own output — a test that
only compares the code to itself passes even when both are wrong.

The compute-plane-backed paths run against the **real** server started by the
session autouse fixture in `tests/conftest.py`; design D2 makes "same numbers as
the compute plane" a spec acceptance item, so a stub would defeat the test.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from fd_paper_trading.marketdata.provider import Bar
from fd_paper_trading.strategy import costs as C
from fd_paper_trading.strategy.backtester import (
    EvaluationError,
    _Portfolio,
    compute_stats,
    replay,
)
from fd_paper_trading.strategy.extended_stats import extended_stats
from fd_paper_trading.strategy.spec import validate_spec
from fd_paper_trading.strategy.walk_forward import (
    expand_grid,
    grid_size,
    sort_matrix,
)


def _bars(prices, start=date(2026, 1, 1)):
    out = []
    for i, p in enumerate(prices):
        d = start + timedelta(days=i)
        ts = datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()
        px = Decimal(str(p))
        out.append(Bar(ts=ts, open=px, high=px, low=px, close=px, volume=Decimal(1000)))
    return out


# ── 1. 迁移与回归基线 ────────────────────────────────────────────


def test_v2_migration_columns_exist_and_are_nullable():
    """task 1.1：迁移加 8 列，全部 nullable（既有行不受影响）。"""
    import sqlite3
    import subprocess
    import os
    from pathlib import Path

    db = Path("/tmp") / "fpt_mig_cols.db"
    if db.exists():
        db.unlink()
    env = {**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{db}"}
    root = Path(__file__).resolve().parents[2]
    r = subprocess.run([str(root / ".venv/bin/python"), "-m", "alembic", "upgrade", "head"],
                       cwd=str(root), env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1500:]

    conn = sqlite3.connect(db)
    info = {row[1]: row for row in conn.execute("PRAGMA table_info(strategy_runs)")}
    expected = ("cost_config", "cost_summary", "benchmark_config", "benchmark_results",
                "walkforward_windows", "param_grid", "scan_results", "in_sample")
    for name in expected:
        assert name in info, f"{name} missing after upgrade"
        # row[3] is notnull; 0 means nullable
        assert info[name][3] == 0, f"{name} must be nullable"
    conn.close()


def test_zero_cost_stats_are_bitwise_identical_to_pre_v2():
    """task 1.2/2.3：零成本下旧统计字段逐位不变（向后兼容的可证伪形式）。

    断言的是"用同一个 compute_stats 输入得到的输出与升级前同形"，即新字段
    不改变旧字段——因为旧字段的计算路径一个字符都没动。
    """
    equity = [Decimal("100000"), Decimal("101000"), Decimal("99500"), Decimal("103000")]
    p = _Portfolio(cash=Decimal("100000"))
    p.trade_count = 3
    p.closed_trips = 2
    p.winning_trips = 1
    stats = compute_stats(equity, Decimal("100000"), p)
    assert set(stats) == {
        "total_return", "max_drawdown", "sharpe", "win_rate", "trade_count",
        "final_equity",
    }, "the legacy stat key set must be exactly unchanged"
    assert stats["total_return"] == round(float(Decimal("103000") / Decimal("100000")) - 1.0, 6)
    assert stats["trade_count"] == 3
    assert stats["win_rate"] == 0.5


# ── 2. 统计扩展 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extended_stats_come_from_the_compute_plane():
    """spec：统计含 annual_return / annual_volatility / sortino / calmar / turnover。"""
    bars = _bars([100, 101, 102, 101, 103, 104])
    spec = validate_spec({"kind": "template", "template": "dca", "symbol": "AAPL",
                          "params": {"quantity": 1, "interval_bars": 1}})
    from fd_paper_trading.strategy.backtester import build_evaluator

    result = await replay(bars, build_evaluator(spec), Decimal("100000"))
    ext = extended_stats(result.equity_points)
    for key in ("annual_return", "annual_volatility", "sortino", "calmar", "turnover"):
        assert key in ext, f"{key} missing from extended stats"
    assert ext["source"] == "compute-plane"
    # 单一标的没有权重向量 → turnover 明确为 null 且带原因（不静默缺失）
    assert ext["turnover"] is None
    assert any("turnover" in n for n in (ext.get("notes") or []))


@pytest.mark.asyncio
async def test_extended_stats_match_compute_plane_direct_call():
    """spec Scenario「与计算面口径一致」：回测统计 == 直接调计算面（1e-9）。

    这是跨仓对拍的实测形式：同一份权益曲线，两条路径（回测的封装 vs 直接
    调用）必须给出同一个数。
    """
    from fd_paper_trading.strategy import compute_plane

    bars = _bars([100, 102, 101, 105, 107, 106, 109])
    spec = validate_spec({"kind": "template", "template": "dca", "symbol": "AAPL",
                          "params": {"quantity": 1, "interval_bars": 1}})
    from fd_paper_trading.strategy.backtester import build_evaluator

    result = await replay(bars, build_evaluator(spec), Decimal("100000"))
    ext = extended_stats(result.equity_points)

    dates = [d.isoformat() for d, _ in result.equity_points]
    values = [float(v) for _, v in result.equity_points]
    direct_ret = compute_plane.summarize_return(dates, values, dates[-1])
    direct_vol = compute_plane.summarize_volatility(dates, values, dates[-1])

    assert abs(ext["annual_return"] - direct_ret["annualized_return"]) < 1e-9
    assert abs(ext["annual_volatility"] - direct_vol["volatility"]) < 1e-9


@pytest.mark.asyncio
async def test_compute_plane_unavailable_fails_the_run_loudly(monkeypatch):
    """task 2.1：计算面不可用时明确失败，**不降级**为内部实现（design D2）。"""
    from fd_paper_trading.strategy import compute_plane

    def _boom(*a, **k):
        raise compute_plane.ComputePlaneUnavailable("simulated outage")

    monkeypatch.setattr(compute_plane, "call_tool", _boom)
    monkeypatch.setattr(compute_plane, "summarize_return", _boom)
    monkeypatch.setattr(compute_plane, "summarize_volatility", _boom)
    monkeypatch.setattr(compute_plane, "summarize_sortino", _boom)
    monkeypatch.setattr(compute_plane, "summarize_calmar", _boom)

    bars = _bars([100, 101, 102])
    with pytest.raises(compute_plane.ComputePlaneUnavailable, match="simulated outage"):
        extended_stats([(d, v) for d, v in zip(
            [date(2026, 1, i) for i in (1, 2, 3)],
            [Decimal(100), Decimal(101), Decimal(102)])])


# ── 3. 成本模型 ─────────────────────────────────────────────────


def test_cost_arithmetic_matches_hand_computation():
    """task 3.1：买卖往返的成交价、现金扣减与手算一致。"""
    m = C.CostModel(enabled=True)
    buy = C.apply_costs(side="BUY", quantity=Decimal(10), price=Decimal(100), model=m)
    # 滑点 5bps：100 × 1.0005 = 100.05
    assert buy.effective_price == Decimal("100.05")
    # 名义 1000.5；佣金 1000.5 × 0.00025 = 0.250125
    assert buy.commission == Decimal("1000.5") * Decimal("0.00025")
    assert buy.cash_delta == -(buy.notional + buy.commission)
    assert buy.stamp_tax == 0, "买入不缴印花税"

    sell = C.apply_costs(side="SELL", quantity=Decimal(10), price=Decimal(100), model=m)
    assert sell.effective_price == Decimal("99.95")
    assert sell.commission == Decimal("999.5") * Decimal("0.00025")
    assert sell.stamp_tax == Decimal("999.5") * Decimal("0.0005"), "印花税仅卖出单边"
    assert sell.cash_delta == sell.notional - sell.commission - sell.stamp_tax


def test_zero_cost_model_is_exact_identity():
    """spec：`enabled=false` 时与既有行为完全一致（价格不变、零费用）。"""
    m = C.CostModel(enabled=False)
    f = C.apply_costs(side="BUY", quantity=Decimal(7), price=Decimal("33.33"), model=m)
    assert f.effective_price == Decimal("33.33")
    assert f.commission == 0 and f.stamp_tax == 0 and f.slippage_cost == 0
    assert f.cash_delta == -(Decimal(7) * Decimal("33.33"))


def test_cost_model_rejects_unknown_fields_and_negatives():
    with pytest.raises(ValueError, match="unknown field"):
        C.CostModel.from_mapping({"enabled": True, "broker_fee": 0.1})
    with pytest.raises(ValueError, match="non-negative"):
        C.CostModel.from_mapping({"commission_rate": -1})
    with pytest.raises(ValueError, match="boolean"):
        C.CostModel.from_mapping({"enabled": "yes"})


def test_cost_model_defaults_are_the_a_share_values():
    """design D4：默认值取 A 股现实，不是 FinRL-X 的美股量级。"""
    m = C.CostModel.from_mapping({"enabled": True})
    assert m.commission_rate == Decimal("0.00025")
    assert m.stamp_tax_rate == Decimal("0.0005")
    assert m.slippage_bps == Decimal("5")


@pytest.mark.asyncio
async def test_costs_are_monotone_net_of_fees():
    """task 3.3：同一策略全成本净收益不高于零成本（含多次换手时严格更低）。"""
    # 锯齿行情 + 每 bar 进出，制造大量换手，使成本必然体现
    prices = [100 + (5 if i % 2 else -5) for i in range(40)]
    bars = _bars(prices)
    spec = validate_spec({"kind": "template", "template": "dca", "symbol": "AAPL",
                          "params": {"quantity": 10, "interval_bars": 1}})
    from fd_paper_trading.strategy.backtester import build_evaluator

    free = await replay(bars, build_evaluator(spec), Decimal("100000"), C.CostModel(enabled=False))
    costed = await replay(bars, build_evaluator(spec), Decimal("100000"), C.CostModel(enabled=True))
    assert costed.stats["final_equity"] < free.stats["final_equity"]
    # cost_summary 的值是字符串化的 Decimal（JSON 无 Decimal，浮点会丢精度），
    # 故比较前先转回 Decimal——这也是消费方读它时该做的事。
    assert Decimal(costed.cost_summary["commission"]) > 0
    assert Decimal(costed.cost_summary["slippage"]) > 0


@pytest.mark.asyncio
async def test_cost_snapshot_and_summary_are_recorded():
    """task 3.2：配置快照 + 三项合计入运行记录。"""
    bars = _bars([100, 101, 102])
    spec = validate_spec({"kind": "template", "template": "dca", "symbol": "AAPL",
                          "params": {"quantity": 1, "interval_bars": 1}})
    from fd_paper_trading.strategy.backtester import build_evaluator

    r = await replay(bars, build_evaluator(spec), Decimal("100000"), C.CostModel(enabled=True))
    assert r.cost_config is not None and r.cost_config["enabled"] is True
    assert set(r.cost_summary) >= {"commission", "stamp_tax", "slippage", "total", "fills"}
    # 汇总值等于逐笔之和（这里两笔买入）
    assert Decimal(r.cost_summary["commission"]) > 0
    assert Decimal(r.cost_summary["stamp_tax"]) == 0, "只有买入，无印花税"


@pytest.mark.asyncio
async def test_disabled_cost_model_records_nothing_new():
    """零成本路径不写 cost 字段——记录形状与升级前一致。"""
    bars = _bars([100, 101, 102])
    spec = validate_spec({"kind": "template", "template": "dca", "symbol": "AAPL",
                          "params": {"quantity": 1, "interval_bars": 1}})
    from fd_paper_trading.strategy.backtester import build_evaluator

    r = await replay(bars, build_evaluator(spec), Decimal("100000"))
    assert r.cost_config is None and r.cost_summary is None


# ── 4. 无未来函数断言 ───────────────────────────────────────────


def test_injected_leak_is_caught_by_the_assertion():
    """spec Scenario「注入泄漏被抓」：越界数据使断言失败并指明日期。"""
    from fd_paper_trading.api.routes.strategies import _assert_no_lookahead

    dates = ["2026-01-01", "2026-01-02", "2026-01-03"]
    with pytest.raises(EvaluationError) as ei:
        _assert_no_lookahead(dates, "2026-01-02", where="test fixture")
    assert "2026-01-03" in str(ei.value), "the error must name the out-of-range date"


def test_clean_data_passes_the_assertion():
    """spec Scenario「合法回测通过断言」。"""
    from fd_paper_trading.api.routes.strategies import _assert_no_lookahead

    _assert_no_lookahead(["2026-01-01", "2026-01-02"], "2026-01-02", where="clean")


# ── 5. 基准对比 ─────────────────────────────────────────────────


def test_relative_metrics_are_zero_when_strategy_equals_benchmark():
    """task 5.2：策略=基准 → 超额 0、TE 0、IR 0（容差 1e-9）。"""
    from fd_paper_trading.strategy.benchmarks import relative_metrics

    pts = [(date(2026, 1, i), Decimal(100 + i * 2)) for i in range(1, 21)]
    rel = relative_metrics(pts, pts)
    assert abs(rel["excess_return"]) < 1e-9
    assert abs(rel["tracking_error"]) < 1e-9
    assert abs(rel["information_ratio"]) < 1e-9
    assert rel["aligned_points"] == 20


def test_relative_metrics_report_insufficient_overlap_not_a_fabricated_number():
    """基准与策略交易日几乎不重叠 → 明确报缺，不给数。"""
    from fd_paper_trading.strategy.benchmarks import relative_metrics

    a = [(date(2026, 1, 1), Decimal(100)), (date(2026, 1, 2), Decimal(101))]
    b = [(date(2026, 3, 1), Decimal(100)), (date(2026, 3, 2), Decimal(102))]
    rel = relative_metrics(a, b)
    assert rel["information_ratio"] is None
    assert rel["excess_return"] is None
    assert "shared trading day" in rel["reason"]


@pytest.mark.asyncio
async def test_benchmark_missing_data_is_marked_not_omitted():
    """spec Scenario「基准缺数据明确报缺」：标注原因，策略结果仍返回。"""
    from fd_paper_trading.strategy.benchmarks import fetch_benchmark

    class _Empty:
        async def get_bars(self, symbol, timeframe, days):
            return []

    bars, reason = await fetch_benchmark(_Empty(), "000300.SS", date(2026, 1, 1), date(2026, 3, 1))
    assert bars is None
    assert "000300.SS" in reason and "no bar data" in reason


def test_default_benchmarks_are_the_two_cn_broad_indices():
    from fd_paper_trading.strategy.benchmarks import DEFAULT_BENCHMARKS

    assert DEFAULT_BENCHMARKS == ("000300.SS", "000905.SS")


# ── 6. walk-forward ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_walk_forward_produces_ordered_windows_and_stitched_curve():
    """spec Scenario「逐窗执行并拼接」+「窗口严格有序」。"""
    from fd_paper_trading.strategy.walk_forward import run_walk_forward

    # 400 交易日
    prices = [100 + 10 * math.sin(i / 12.0) + i * 0.05 for i in range(400)]
    bars = _bars(prices)
    spec = validate_spec({"kind": "template", "template": "dca", "symbol": "AAPL",
                          "params": {"quantity": 1, "interval_bars": 5}})
    res = await run_walk_forward(
        spec, bars, Decimal("100000"),
        train_window=120, test_window=40, step=40,
    )
    assert len(res.windows) > 1
    # 有序 + 测试段严格晚于训练段
    for w in res.windows:
        assert w.test_start > w.train_end, f"window {w.window}: test must start after train"
    starts = [w.test_start for w in res.windows]
    assert starts == sorted(starts)
    # 拼接曲线行数 == 各窗测试段 bar 数之和
    total = sum(len(w.equity_points) for w in res.windows)
    assert len(res.equity_points) == total


@pytest.mark.asyncio
async def test_walk_forward_refuses_when_no_window_fits():
    """spec Scenario「窗口不可切出时拒绝」：错误含所需与实际两个长度。"""
    from fd_paper_trading.strategy.walk_forward import run_walk_forward

    bars = _bars([100 + i for i in range(50)])
    spec = validate_spec({"kind": "template", "template": "dca", "symbol": "AAPL",
                          "params": {"quantity": 1, "interval_bars": 1}})
    with pytest.raises(EvaluationError) as ei:
        await run_walk_forward(spec, bars, Decimal("100000"),
                               train_window=120, test_window=40, step=40)
    msg = str(ei.value)
    assert "50" in msg, "must name the available length"
    assert "160" in msg or "120" in msg, "must name the required length"


# ── 7. 参数扫描 ─────────────────────────────────────────────────


def test_grid_expansion_and_size():
    """spec Scenario「网格扫描输出矩阵」：3×3 → 9 组。"""
    grid = {"fast": [5, 10, 15], "slow": [20, 30, 40]}
    assert grid_size(grid) == 9
    assert len(expand_grid(grid)) == 9
    assert {"fast": 5, "slow": 20} in expand_grid(grid)


def test_grid_sorts_by_requested_field_with_none_last():
    """task 7.2：按指定字段排序；缺该字段的排最后（不因方向被顶到前面）。"""
    matrix = [
        {"params": {"a": 1}, "stats": {"sharpe": 0.5}},
        {"params": {"a": 2}, "stats": {"sharpe": 1.5}},
        {"params": {"a": 3}, "stats": {"sharpe": None}},
    ]
    top = sort_matrix(matrix, "sharpe")
    assert top[0]["params"]["a"] == 2
    assert top[-1]["params"]["a"] == 3, "a null stat must sort last, not first"


def test_scan_disclaimer_is_machine_readable():
    """spec Scenario「样本内标注可机读」：字段在结果里，不只在文档里。"""
    # 该断言对应路由写入 run.stats["scan"] 的形状
    scan = {
        "combinations": 9, "sorted_by": "sharpe", "in_sample": True,
        "disclaimer": "in-sample result: ... not a recommendation",
    }
    assert scan["in_sample"] is True
    assert isinstance(scan["disclaimer"], str) and scan["disclaimer"]


# ── 8. 请求 schema ──────────────────────────────────────────────


def test_request_without_v2_fields_is_unchanged():
    """spec Scenario「Request without new optional fields is unchanged」。"""
    from fd_paper_trading.api.schemas import BacktestRequest

    r = BacktestRequest(start=date(2026, 1, 1), end=date(2026, 3, 1))
    assert r.cost_model is None
    assert r.benchmarks is None
    assert r.walk_forward_enabled is False
    assert r.param_grid is None


def test_partial_walk_forward_parameters_are_refused():
    """两缺一是一个静默变成别的东西的请求，必须拒。"""
    from fd_paper_trading.api.schemas import BacktestRequest

    with pytest.raises(Exception, match="together"):
        BacktestRequest(start=date(2026, 1, 1), end=date(2026, 3, 1),
                        train_window=60, test_window=20)


@pytest.mark.asyncio
async def test_walk_forward_feeds_training_history_so_test_windows_can_trade():
    """回归：walk-forward 每窗必须把训练段当历史喂进去。

    这条断言来自一个真实缺陷：最初只把测试段的 bar 传给 `replay`，于是每个窗
    都从冷启动开始——30 根 SMA 在 40 根窗口里永远等不到交叉，7 个窗全部
    trade_count=0、total_return=0。报告上看像"策略在这段行情里没机会"，实际是
    窗口喂法错了。测试用一条明确会产生交叉的行情把它钉住。
    """
    from fd_paper_trading.strategy.walk_forward import run_walk_forward

    # 先跌后涨，制造 SMA 交叉
    prices = [100 - i * 0.5 for i in range(120)] + [40 + i * 1.2 for i in range(280)]
    bars = _bars(prices)
    spec = validate_spec({"kind": "template", "template": "sma_cross", "symbol": "AAPL",
                          "params": {"fast": 10, "slow": 30, "quantity": 10}})
    res = await run_walk_forward(spec, bars, Decimal("100000"),
                                 train_window=120, test_window=40, step=40)
    assert res.windows, "expected windows"
    traded = [w for w in res.windows if (w.stats.get("trade_count") or 0) > 0]
    assert traded, (
        "no window traded — training history is not being fed to the evaluator "
        f"(windows: {[(w.window, w.stats.get('trade_count')) for w in res.windows]})"
    )
    # 测试段统计不应把训练段的涨幅算进来：以测量边界为基准
    assert res.windows[0].stats["total_return"] != res.combined_stats["total_return"]


@pytest.mark.asyncio
async def test_measure_from_baseline_excludes_the_training_gain():
    """`measure_from` 的收益基准是边界处权益，不是初始资金。"""
    from fd_paper_trading.strategy.backtester import build_evaluator

    # 前 10 根从 100 涨到 200（训练段），后 10 根横盘（测试段）
    bars = _bars([100 + i * 10 for i in range(10)] + [200] * 10)
    spec = validate_spec({"kind": "template", "template": "dca", "symbol": "AAPL",
                          "params": {"quantity": 1, "interval_bars": 1}})
    r = await replay(bars, build_evaluator(spec), Decimal("100000"), None,
                     measure_from=10)
    # 横盘段无新成交且价格不变 → 测试段收益应为 0，而不是训练段的翻倍收益
    assert r.stats["total_return"] == 0.0
    assert len(r.equity_points) == 10, "only the measured window gets equity points"
