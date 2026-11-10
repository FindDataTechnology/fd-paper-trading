"""E2E tests for the backtest API: lifecycle, stats, failures, delete guard."""

import asyncio
from datetime import date, datetime, time, timedelta, timezone

import pytest

import fd_paper_trading.api.routes.strategies as strategies_module
from fd_paper_trading.strategy import EvaluationError

DCA_SPEC = {
    "kind": "template",
    "template": "dca",
    "symbol": "AAPL",
    "params": {"interval_bars": 2, "quantity": 1},
}

WINDOW_DAYS = 21


def _window() -> dict:
    end = date.today()
    start = end - timedelta(days=WINDOW_DAYS - 1)
    return {"start": start.isoformat(), "end": end.isoformat(),
            "initial_cash": "100000"}


async def _make_strategy(client, headers, spec=None):
    r = await client.post(
        "/api/v1/strategies",
        json={"name": "bt", "spec": spec or DCA_SPEC},
        headers=headers,
    )
    assert r.status_code == 201, r.json()
    return r.json()["id"]


async def _start_backtest(client, headers, strategy_id, window=None):
    r = await client.post(
        f"/api/v1/strategies/{strategy_id}/backtests",
        json=window or _window(),
        headers=headers,
    )
    assert r.status_code == 202, r.json()
    return r.json()


async def _wait_terminal(client, headers, run_id, tries=200):
    for _ in range(tries):
        r = await client.get(f"/api/v1/strategy-runs/{run_id}", headers=headers)
        assert r.status_code == 200, r.json()
        body = r.json()
        if body["status"] in ("COMPLETED", "FAILED"):
            return body
        await asyncio.sleep(0.02)
    raise AssertionError(f"run {run_id} did not reach a terminal state")


class _NoBarsProvider:
    async def get_bars(self, symbol, timeframe, limit):
        return []


class _BlockingProvider:
    """Holds get_bars until released, so the run stays active."""

    def __init__(self) -> None:
        self.release = asyncio.Event()

    async def get_bars(self, symbol, timeframe, limit):
        await self.release.wait()
        return []


# ---- happy path ----


@pytest.mark.asyncio
async def test_backtest_completes_with_stats_and_equity(client, auth_headers):
    headers = await auth_headers()
    sid = await _make_strategy(client, headers)
    run = await _start_backtest(client, headers, sid)
    assert run["status"] == "QUEUED"
    assert run["mode"] == "BACKTEST"
    assert run["strategy_version"] == 1

    done = await _wait_terminal(client, headers, run["id"])
    assert done["status"] == "COMPLETED"
    stats = done["stats"]
    # The six original stats must all still be present and unchanged (spec
    # "Request without new optional fields is unchanged"); the five extended
    # stats come from the fd-quant compute plane and are additive.
    assert {
        "total_return", "max_drawdown", "sharpe", "win_rate",
        "trade_count", "final_equity",
    } <= set(stats)
    assert {
        "annual_return", "annual_volatility", "sortino", "calmar", "turnover",
    } <= set(stats), f"extended stats missing from {sorted(stats)}"
    assert stats["trade_count"] >= 1  # dca every 2 bars over ~3 weeks
    points = done["equity_points"]
    assert len(points) >= 1
    dates = [p["date"] for p in points]
    assert dates == sorted(dates)
    assert done["started_at"] is not None
    assert done["stopped_at"] is not None


@pytest.mark.asyncio
async def test_run_listing_filters(client, auth_headers):
    headers = await auth_headers()
    sid = await _make_strategy(client, headers)
    run = await _start_backtest(client, headers, sid)
    await _wait_terminal(client, headers, run["id"])

    r = await client.get("/api/v1/strategy-runs", headers=headers)
    assert [x["id"] for x in r.json()] == [run["id"]]
    r = await client.get(
        f"/api/v1/strategy-runs?strategy_id={sid}&mode=BACKTEST&status=COMPLETED",
        headers=headers,
    )
    assert len(r.json()) == 1
    r = await client.get("/api/v1/strategy-runs?status=FAILED", headers=headers)
    assert r.json() == []
    r = await client.get("/api/v1/strategy-runs?mode=WEIRD", headers=headers)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_run_pinned_to_strategy_version(client, auth_headers):
    headers = await auth_headers()
    sid = await _make_strategy(client, headers)
    run = await _start_backtest(client, headers, sid)
    done = await _wait_terminal(client, headers, run["id"])

    spec = {**DCA_SPEC, "params": {"interval_bars": 3, "quantity": 1}}
    r = await client.patch(
        f"/api/v1/strategies/{sid}", json={"spec": spec}, headers=headers
    )
    assert r.json()["version"] == 2
    assert done["strategy_version"] == 1  # history keeps what it ran


# ---- failure paths ----


@pytest.mark.asyncio
async def test_window_over_group_limit_rejected(client, auth_headers):
    headers = await auth_headers()
    sid = await _make_strategy(client, headers)
    end = date.today()
    window = {"start": (end - timedelta(days=800)).isoformat(),
              "end": end.isoformat(), "initial_cash": "10000"}
    r = await client.post(
        f"/api/v1/strategies/{sid}/backtests", json=window, headers=headers
    )
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "window_too_large"
    assert "730" in r.json()["error"]["message"]


@pytest.mark.asyncio
async def test_backtest_fails_without_bar_data(client, auth_headers, app):
    headers = await auth_headers()
    sid = await _make_strategy(client, headers)
    app.state.market_data_provider = _NoBarsProvider()
    run = await _start_backtest(client, headers, sid)
    done = await _wait_terminal(client, headers, run["id"])
    assert done["status"] == "FAILED"
    assert "AAPL" in done["error"]  # names the symbol with no data
    assert done["stats"] is None


@pytest.mark.asyncio
async def test_backtest_fails_on_evaluation_error(
    client, auth_headers, monkeypatch
):
    headers = await auth_headers()
    sid = await _make_strategy(client, headers)

    def boom(spec):
        raise EvaluationError("cannot build evaluator: bang")

    monkeypatch.setattr(strategies_module, "build_evaluator", boom)
    run = await _start_backtest(client, headers, sid)
    done = await _wait_terminal(client, headers, run["id"])
    assert done["status"] == "FAILED"
    assert "bang" in done["error"]


@pytest.mark.asyncio
async def test_runs_are_user_isolated(client, auth_headers):
    h1 = await auth_headers("owner@example.com")
    h2 = await auth_headers("other@example.com")
    sid = await _make_strategy(client, h1)
    run = await _start_backtest(client, h1, sid)
    await _wait_terminal(client, h1, run["id"])

    r = await client.get(f"/api/v1/strategy-runs/{run['id']}", headers=h2)
    assert r.status_code == 404
    r = await client.get("/api/v1/strategy-runs", headers=h2)
    assert r.json() == []


# ---- delete guard ----


@pytest.mark.asyncio
async def test_delete_blocked_while_run_active(client, auth_headers, app):
    headers = await auth_headers()
    sid = await _make_strategy(client, headers)
    provider = _BlockingProvider()
    app.state.market_data_provider = provider
    run = await _start_backtest(client, headers, sid)

    # Wait until the background task owns the run (RUNNING is still active).
    for _ in range(100):
        r = await client.get(f"/api/v1/strategy-runs/{run['id']}", headers=headers)
        if r.json()["status"] == "RUNNING":
            break
        await asyncio.sleep(0.02)

    r = await client.delete(f"/api/v1/strategies/{sid}", headers=headers)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "strategy_active"

    provider.release.set()
    await _wait_terminal(client, headers, run["id"])
    r = await client.delete(f"/api/v1/strategies/{sid}", headers=headers)
    assert r.status_code == 204

async def _wait_terminal_spaced(client, headers, run_id, *, seconds=20.0,
                                step=0.1):
    """稀疏轮询等终态。

    紧轮询（每 20ms 一次）会与后台回测抢同一个 SQLite 连接，把一条 1 秒能跑完的
    回测拖成永远跑不完——这是测试夹具的假象，不是被测代码的问题。所以这里先
    让出时间片（step），再取一次状态。
    """
    waited = 0.0
    while waited < seconds:
        await asyncio.sleep(step)
        waited += step
        r = await client.get(f"/api/v1/strategy-runs/{run_id}", headers=headers)
        assert r.status_code == 200, r.json()
        body = r.json()
        if body["status"] in ("COMPLETED", "FAILED"):
            return body
    raise AssertionError(f"run {run_id} did not reach a terminal state")

# ---------------------------------------------------------------------------
# 回测输入附带 PriceSeriesRef（change1 task 4.3）
# ---------------------------------------------------------------------------


class _RefProvider:
    """带覆盖能力 + 序列出处的 provider（模拟注册表已接线的 astock 源）。

    ``series_ref`` 刻意由真实的 ``build_series_ref`` 造——测试要证明的是
    "provider 报的 ref 会落到 run.params"，不是"路由能存一个 dict"。
    """

    def __init__(self, *, with_ref: bool = True,
                 unavailable: str | None = None) -> None:
        from fd_paper_trading.marketdata.series_ref import build_series_ref

        self._with_ref = with_ref
        self._unavailable = unavailable
        self._build = build_series_ref

    async def get_bars(self, symbol, timeframe, limit):
        from decimal import Decimal
        from fd_paper_trading.marketdata.provider import Bar

        today = date.today()
        out = []
        for i in range(limit):
            d = today - timedelta(days=limit - 1 - i)
            px = Decimal("100") + Decimal(i)
            # ts 必须是真 epoch 秒：toordinal*86400 会落到窗口外，被下游读成"没数据"
            ts = int(datetime.combine(d, time.min, tzinfo=timezone.utc).timestamp())
            out.append(Bar(ts=ts, open=px, high=px, low=px, close=px,
                           volume=Decimal("1000")))
        return out

    async def get_bars_with_coverage(self, symbol, start, end):
        if not self._with_ref:
            return {"missing": [], "market_closed": [], "fill_failed": False,
                    "fingerprint": "", "adjusted": "forward",
                    "source": "astock_daily",
                    "series_ref_unavailable": self._unavailable}
        ref, _reason = self._build(
            source_id="astock_daily", symbol=symbol, adjusted="forward",
            as_of_max="2026-07-10", window_start=str(start), window_end=str(end),
            row_count=10, adjustment_anchor="2026-07-10",
            db_source="astock_daily",
        )
        return {"missing": [], "market_closed": [], "fill_failed": False,
                "fingerprint": "", "adjusted": "forward",
                "source": "astock_daily", "series_ref": ref}


@pytest.mark.asyncio
async def test_backtest_records_the_price_series_ref(client, auth_headers, app):
    """判据就是这条：`run_backtest` 落库含四个字段。

    删掉 ``_coverage_params`` 里的 ``params.update(_series_ref_params(...))``
    接线，这条会红——那正是"可选参数被重构整段删掉、全绿也拦不住"的形态。
    """
    headers = await auth_headers()
    sid = await _make_strategy(client, headers)
    app.state.market_data_provider = _RefProvider()

    run = await _start_backtest(client, headers, sid)
    done = await _wait_terminal_spaced(client, headers, run["id"])
    assert done["status"] == "COMPLETED", done.get("error")

    ref = done["params"]["price_series_ref"]
    for key in ("source_id", "adjustment", "as_of_max", "fingerprint"):
        assert ref[key], f"{key} 缺失或为空"
    assert ref["symbol"] == "AAPL"
    assert "price_series_ref_unavailable" not in done["params"]


@pytest.mark.asyncio
async def test_backtest_records_why_there_is_no_ref(client, auth_headers, app):
    """出不来时留原因，不是留空——"问过、没有"要能查到。"""
    headers = await auth_headers()
    sid = await _make_strategy(client, headers)
    app.state.market_data_provider = _RefProvider(with_ref=False,
                                                 unavailable="caliber_unknown")

    run = await _start_backtest(client, headers, sid)
    done = await _wait_terminal_spaced(client, headers, run["id"])
    assert done["status"] == "COMPLETED", done.get("error")
    assert "price_series_ref" not in done["params"]
    assert done["params"]["price_series_ref_unavailable"] == "caliber_unknown"
