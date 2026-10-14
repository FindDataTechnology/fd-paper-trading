"""E2E tests for the backtest API: lifecycle, stats, failures, delete guard."""

import asyncio
from datetime import date, timedelta

import pytest

import fd_trade.api.routes.strategies as strategies_module
from fd_trade.strategy import EvaluationError

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
    assert set(stats) == {
        "total_return", "max_drawdown", "sharpe", "win_rate",
        "trade_count", "final_equity",
    }
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
