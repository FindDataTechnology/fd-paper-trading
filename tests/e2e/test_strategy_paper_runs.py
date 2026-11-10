"""E2E tests for the live paper runner (design D8, task 7.6).

Two driving modes:

* **Deterministic** (most tests): the run row is created directly in the DB and
  ``_tick_once`` / ``_finalize_stop`` are called directly. This avoids spawning
  the auto-task, whose cancellation mid-DB-statement corrupts the in-memory
  SQLite StaticPool connection (a test-only artifact - Postgres pool handles it).
* **Real loop** (ERROR-on-crash, full e2e): POST /runs spawns the task with
  ``poll_seconds=0.01``; cleanup happens only once the task is sleeping (after
  ERROR / after a graceful stop), so no mid-tick cancellation occurs.

Bar data is controlled by ``_ControlledProvider`` so new-bar detection is
deterministic: a fresh daily series is primed on the first tick, and appending
a later-dated bar simulates "a new daily bar arrived".
"""

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from fd_paper_trading.execution import PaperExecutionService
from fd_paper_trading.marketdata import SimulatedMarketDataProvider
from fd_paper_trading.marketdata.provider import Bar
from fd_paper_trading.persistence.models import (
    DEFAULT_GROUP_CONFIG,
    DEFAULT_GROUP_ID,
    Order,
    Strategy,
    StrategyRun,
    StrategyRunMode,
    StrategyRunStatus,
    UserGroup,
)
from fd_paper_trading.persistence.repositories import UserRepository
from sqlalchemy import select

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _ControlledProvider(SimulatedMarketDataProvider):
    """SIM provider whose daily-bar series the test fully controls."""

    def __init__(self):
        super().__init__()
        self._bars: list[Bar] = []
        self.fail_fetch = False

    def add_bars(self, n: int, start: date | None = None) -> None:
        base = 100.0 if not self._bars else float(self._bars[-1].close)
        d = start if start is not None else self._next_date()
        for i in range(n):
            open_ = base + i
            ts = int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())
            self._bars.append(
                Bar(
                    ts=ts,
                    open=Decimal(str(open_)),
                    high=Decimal(str(open_ + 1)),
                    low=Decimal(str(open_ - 1)),
                    close=Decimal(str(open_ + 0.5)),
                    volume=Decimal(1000),
                )
            )
            d = d + timedelta(days=1)
        # Keep the series short so the test lookback cap applies.
        self._bars = self._bars[-10:]

    def _next_date(self) -> date:
        if not self._bars:
            return date(2026, 1, 1)
        last = datetime.fromtimestamp(self._bars[-1].ts, tz=UTC).date()
        return last + timedelta(days=1)

    async def get_bars(self, symbol, timeframe, limit):
        if self.fail_fetch:
            raise RuntimeError("provider down")
        return self._bars[-limit:] if limit else list(self._bars)


# DCA buying 1 share every bar (interval_bars=1): each evaluated bar emits one
# BUY market signal - deterministic and easy to reason about.
_DCA = {
    "kind": "template",
    "template": "dca",
    "symbol": "AAPL",
    "params": {"interval_bars": 1, "quantity": 1},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _wire(app, provider=None):
    """Swap in a controlled provider + fast supervisor settings."""
    if provider is None:
        provider = _ControlledProvider()
        provider.add_bars(5, start=date(2026, 1, 1))
    app.state.market_data_provider = provider
    app.state.execution_service = PaperExecutionService(provider=provider)
    app.state.strategy_runner.poll_seconds = 0.01
    app.state.strategy_runner.lookback_bars = 10
    return provider


async def _authed(client):
    await client.post("/api/v1/auth/register", json={"email": "u@x.io", "password": "password123"})
    r = await client.post("/api/v1/auth/login", json={"email": "u@x.io", "password": "password123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _make_account(client, h):
    r = await client.post("/api/v1/accounts", json={"name": "paper"}, headers=h)
    return uuid.UUID(r.json()["id"])


async def _make_strategy(client, h, spec=_DCA, name="dca"):
    r = await client.post("/api/v1/strategies", json={"name": name, "spec": spec}, headers=h)
    return r.json()


async def _make_run_db(app, strategy_id, account_id) -> uuid.UUID:
    """Create a STARTING PAPER run row directly (no spawned task)."""
    async with app.state.db.session_factory() as session:
        strat = await session.get(Strategy, strategy_id)
        run = StrategyRun(
            strategy_id=strat.id,
            strategy_version=strat.version,
            mode=StrategyRunMode.PAPER,
            account_id=account_id,
            status=StrategyRunStatus.STARTING,
            params={"symbol": strat.spec.get("symbol")},
        )
        session.add(run)
        await session.commit()
        return run.id


async def _run_detail(client, h, run_id):
    r = await client.get(f"/api/v1/strategy-runs/{run_id}", headers=h)
    assert r.status_code == 200, r.text
    return r.json()


async def _orders_for_run(client, h, account_id, run_id):
    r = await client.get(
        f"/api/v1/accounts/{account_id}/orders",
        params={"strategy_run_id": str(run_id)},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------------------
# Deterministic tick tests (no spawned task)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tick_noop_without_new_bar(client, app):
    await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)

    sup = app.state.strategy_runner
    await sup._tick_once(run_id)  # first tick: prime + trade latest (Jan 5)
    await sup._tick_once(run_id)  # same bars -> no-op

    orders = await _orders_for_run(client, h, account_id, run_id)
    assert len(orders) == 1
    detail = await _run_detail(client, h, run_id)
    assert detail["params"]["last_bar_date"] == "2026-01-05"


@pytest.mark.asyncio
async def test_evaluation_on_new_bar_orders_attributed(client, app):
    provider = await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)

    sup = app.state.strategy_runner
    await sup._tick_once(run_id)  # 1 order (Jan 5)
    provider.add_bars(1)  # Jan 6
    await sup._tick_once(run_id)  # 1 more order

    orders = await _orders_for_run(client, h, account_id, run_id)
    assert len(orders) == 2
    for o in orders:
        assert o["strategy_run_id"] == str(run_id)
        assert o["side"] == "BUY"
        assert o["status"] == "FILLED"
    detail = await _run_detail(client, h, run_id)
    assert detail["params"]["last_bar_date"] == "2026-01-06"
    assert any(e["type"] == "state_change" for e in detail["events"]) is False  # no _mark_running called
    assert sum(1 for e in detail["events"] if e["type"] == "order") == 2


@pytest.mark.asyncio
async def test_daily_equity_snapshot_after_new_bar(client, app):
    provider = await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)

    sup = app.state.strategy_runner
    await sup._tick_once(run_id)  # snapshot for Jan 5
    provider.add_bars(1)
    await sup._tick_once(run_id)  # snapshot for Jan 6

    detail = await _run_detail(client, h, run_id)
    dates = [p["date"] for p in detail["equity_points"]]
    assert dates == ["2026-01-05", "2026-01-06"]
    for p in detail["equity_points"]:
        assert Decimal(p["equity"]) > Decimal("0")


@pytest.mark.asyncio
async def test_daily_order_cap_skips_and_logs(client, app):
    provider = await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)

    # Seed a group with max_orders_per_day=2 so a single multi-bar tick hits it.
    async with app.state.db.session_factory() as session:
        user = await UserRepository(session).get_by_email("u@x.io")
        group = UserGroup(
            id=DEFAULT_GROUP_ID, name="default",
            config={**DEFAULT_GROUP_CONFIG, "max_orders_per_day": 2},
        )
        session.add(group)
        user.group_id = group.id
        await session.commit()

    sup = app.state.strategy_runner
    await sup._tick_once(run_id)  # 1 order today (Jan 5)

    provider.add_bars(3)  # 3 new bars -> 3 signals, cap allows 1 more today
    await sup._tick_once(run_id)

    orders = await _orders_for_run(client, h, account_id, run_id)
    assert len(orders) == 2  # 1 (first tick) + 1 (cap reached on 2nd signal)
    detail = await _run_detail(client, h, run_id)
    rejections = [e for e in detail["events"] if e["type"] == "rejection"]
    assert len(rejections) >= 1
    assert "cap" in rejections[0]["message"]


@pytest.mark.asyncio
async def test_stop_cancels_resting_limits_keeps_position(client, app):
    await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)

    sup = app.state.strategy_runner
    await sup._tick_once(run_id)  # 1 BUY filled -> position long 1

    # Place a resting LIMIT (strategy signals are market-only in v1, so simulate
    # the attribution + cancel path directly).
    r = await client.post(
        "/api/v1/orders",
        json={
            "account_id": str(account_id), "symbol": "AAPL", "side": "BUY",
            "order_type": "LIMIT", "quantity": "1", "limit_price": "1.00",
        },
        headers=h,
    )
    resting_id = uuid.UUID(r.json()["id"])
    async with app.state.db.session_factory() as session:
        order = (
            await session.execute(select(Order).where(Order.id == resting_id))
        ).scalar_one()
        order.strategy_run_id = run_id
        await session.commit()

    await sup._finalize_stop(run_id)

    detail = await _run_detail(client, h, run_id)
    assert detail["status"] == "STOPPED"
    # Position kept.
    r = await client.get(f"/api/v1/accounts/{account_id}/positions", headers=h)
    pos = [p for p in r.json() if p["symbol"] == "AAPL"]
    assert pos and Decimal(pos[0]["quantity"]) == Decimal("1")
    # Resting limit cancelled.
    r = await client.get(f"/api/v1/orders/{resting_id}", headers=h)
    assert r.json()["status"] == "CANCELLED"


@pytest.mark.asyncio
async def test_active_limit_reached_409(client, app):
    await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    # Create 3 active paper runs directly (default group fallback: max=3).
    for i in range(3):
        strat = await _make_strategy(client, h, name=f"dca{i}")
        await _make_run_db(app, uuid.UUID(strat["id"]), account_id)
    strat = await _make_strategy(client, h, name="dca4")
    r = await client.post(
        f"/api/v1/strategies/{strat['id']}/runs",
        json={"account_id": str(account_id)},
        headers=h,
    )
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "active_limit_reached"


@pytest.mark.asyncio
async def test_stop_backtest_run_rejected(client, app):
    await _wire(app)
    h = await _authed(client)
    strat = await _make_strategy(client, h)
    r = await client.post(
        f"/api/v1/strategies/{strat['id']}/backtests",
        json={"start": "2026-01-01", "end": "2026-01-03", "initial_cash": "100000"},
        headers=h,
    )
    bt_id = r.json()["id"]
    for _ in range(50):
        d = await _run_detail(client, h, bt_id)
        if d["status"] in ("COMPLETED", "FAILED"):
            break
        await asyncio.sleep(0.02)
    r = await client.post(f"/api/v1/strategy-runs/{bt_id}/stop", headers=h)
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "invalid_mode"


# ---------------------------------------------------------------------------
# Real-loop tests (spawned task; cleanup only while sleeping)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_on_account_deleted(client, app):
    provider = await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    r = await client.post(
        f"/api/v1/strategies/{strat['id']}/runs",
        json={"account_id": str(account_id)},
        headers=h,
    )
    run_id = r.json()["id"]

    # Wait until RUNNING, then delete the account directly.
    for _ in range(50):
        d = await _run_detail(client, h, run_id)
        if d["status"] == "RUNNING":
            break
        await asyncio.sleep(0.02)

    from fd_paper_trading.persistence.models import Account

    async with app.state.db.session_factory() as session:
        acc = await session.get(Account, account_id)
        await session.delete(acc)
        await session.commit()

    # Next tick sees account None -> EvaluationError -> loop marks ERROR.
    for _ in range(100):
        d = await _run_detail(client, h, run_id)
        if d["status"] == "ERROR":
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail("run did not reach ERROR")
    assert d["error"]
    # Task is now looping no-op ticks + sleeping; safe to cancel.
    await app.state.strategy_runner.shutdown()


@pytest.mark.asyncio
async def test_e2e_start_tick_order_stop(client, app):
    await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    r = await client.post(
        f"/api/v1/strategies/{strat['id']}/runs",
        json={"account_id": str(account_id)},
        headers=h,
    )
    run_id = r.json()["id"]

    # The auto-task ticks immediately (poll_seconds=0.01): wait for an order.
    for _ in range(100):
        orders = await _orders_for_run(client, h, account_id, run_id)
        if orders:
            break
        await asyncio.sleep(0.02)
    else:
        pytest.fail("no order placed")
    assert orders[0]["strategy_run_id"] == run_id

    # Stop via the API; the real task finalizes STOPPED gracefully.
    r = await client.post(f"/api/v1/strategy-runs/{run_id}/stop", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "STOPPED"


# ---------------------------------------------------------------------------
# Caliber recording (spec: strategy-live-execution; 2026-11-07 three-way check)
# ---------------------------------------------------------------------------


class _CoverageProvider(_ControlledProvider):
    """SIM provider that declares an adjustment caliber + source.

    Mirrors a lagging real source: a same-day window is entirely missing and
    fails closed (adjusted=unknown); a trailing window resolves the caliber.
    The 35-day window contract is asserted, not assumed.
    """

    async def get_bars_with_coverage(self, symbol, start, end):
        window_days = (end - start).days
        return {
            "adjusted": "forward" if 30 <= window_days <= 500 else "unknown",
            "source": "astock_daily",
        }


@pytest.mark.asyncio
async def test_mark_running_records_caliber_for_template_run(client, app):
    """Template runs carry the symbol on the spec (not params.universe);
    the caliber must still be recorded from it."""
    provider = await _wire(app, provider=_CoverageProvider())
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)

    sup = app.state.strategy_runner
    await sup._mark_running(run_id)

    async with app.state.db.session_factory() as session:
        run = await session.get(StrategyRun, run_id)
        assert run.status == StrategyRunStatus.RUNNING
        assert run.params["price_caliber"] == "forward"
        assert run.params["price_source"] == "astock_daily"
    _ = provider  # keep linters calm about the unused-looking wire helper


@pytest.mark.asyncio
async def test_mark_running_records_caliber_for_weight_run(client, app):
    """Weight runs carry the universe in params — the original path stays."""
    provider = await _wire(app, provider=_CoverageProvider())
    h = await _authed(client)
    account_id = await _make_account(client, h)
    weight_spec = {
        "kind": "weight",
        "universe": ["600000", "600036"],
        "rebalance": {"every_n_bars": 1},
        "weights_series": {"2026-01-01": {"600000": 0.5, "600036": 0.5}},
    }
    strat = await _make_strategy(client, h, spec=weight_spec, name="w")
    sid = uuid.UUID(strat["id"])
    async with app.state.db.session_factory() as session:
        run = StrategyRun(
            strategy_id=sid,
            strategy_version=1,
            mode=StrategyRunMode.PAPER,
            account_id=account_id,
            status=StrategyRunStatus.STARTING,
            params={"universe": ["600000", "600036"]},
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    sup = app.state.strategy_runner
    await sup._mark_running(run_id)

    async with app.state.db.session_factory() as session:
        run = await session.get(StrategyRun, run_id)
        assert run.params["price_caliber"] == "forward"
        assert run.params["price_source"] == "astock_daily"


# ---------------------------------------------------------------------------
# 探针窗锚在源水位上（change1 task 6.5：卸载 400 天盲窗）
# ---------------------------------------------------------------------------


class _WatermarkProvider(_ControlledProvider):
    """带水位能力的 provider：探针窗必须钉在水位右端，而不是 400 天盲窗。

    ``calls`` 记下每次探针的 ``(start, end)``——判据不是"caliber 记上了"（那条
    400 天窗也能过），而是"窗口真的收窄并以水位为右端"。
    """

    def __init__(self, watermark: dict | None):
        super().__init__()
        self._watermark = watermark
        self.calls: list[tuple[date, date]] = []

    def source_watermark(self) -> dict | None:
        return self._watermark

    async def get_bars_with_coverage(self, symbol, start, end):
        self.calls.append((start, end))
        return {"adjusted": "forward", "source": "astock_daily"}


@pytest.mark.asyncio
async def test_mark_running_anchors_probe_window_on_watermark(client, app):
    """水位说这个源只到 2026-07-10 → 窗口右端就是它，宽度回到 35 天。

    若有人把 ``caliber_probe_window`` 的接线删掉（400 天盲窗回来），
    ``end`` 会变成"今天"而不是水位，这条直接红。
    """
    from fd_paper_trading.strategy.runner import CALIBER_PROBE_DAYS

    provider = _WatermarkProvider(
        {"source_id": "astock_daily", "as_of": "2026-07-10", "status": "ok"})
    await _wire(app, provider=provider)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)

    await app.state.strategy_runner._mark_running(run_id)

    assert provider.calls, "没有发生判据探针"
    start, end = provider.calls[0]
    assert end == date(2026, 7, 10), f"窗口右端应钉在水位上，实际 {end}"
    assert (end - start).days == CALIBER_PROBE_DAYS

    async with app.state.db.session_factory() as session:
        run = await session.get(StrategyRun, run_id)
        assert run.params["price_caliber"] == "forward"
        # 水位留痕：判据是"那一刻这个源的哪段数据"，事后要能复现
        assert run.params["price_as_of_max"] == "2026-07-10"
        assert run.params["price_watermark_status"] == "ok"


@pytest.mark.asyncio
async def test_mark_running_keeps_fixed_window_when_watermark_unavailable(client, app):
    """问不到水位（no_data）→ 退回 400 天固定窗，行为与卸载前逐位一致。

    这是防"卸载补丁顺手把兜底也删了"：没有水位要让判据照常可评，而不是
    把"问不到"当成"判据未知"。
    """
    from fd_paper_trading.strategy.runner import CALIBER_PROBE_FALLBACK_DAYS

    provider = _WatermarkProvider({"status": "no_data", "as_of": None})
    await _wire(app, provider=provider)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)

    await app.state.strategy_runner._mark_running(run_id)

    start, end = provider.calls[0]
    assert (end - start).days == CALIBER_PROBE_FALLBACK_DAYS
    assert end == datetime.now(timezone.utc).date()

    async with app.state.db.session_factory() as session:
        run = await session.get(StrategyRun, run_id)
        assert run.params["price_caliber"] == "forward"
        # **问过且答"没有"** 与 **没能力问** 是两件事，前者要留痕：
        # as_of=None + status=no_data 说的是"查了，这个源没数据"。
        assert run.params["price_as_of_max"] is None
        assert run.params["price_watermark_status"] == "no_data"


@pytest.mark.asyncio
async def test_mark_running_records_no_watermark_keys_without_the_capability(client, app):
    """provider 没有 ``source_watermark`` 能力 → 一个字都不留。

    这条与上一条成对：留 ``None`` 和留空键是两种语义。若有人把两者合并成
    "统一写 None"，这条会红——那等于把"没问过"伪装成"问过、没有"。
    """
    provider = _CoverageProvider()   # 只有覆盖能力，没有水位能力
    await _wire(app, provider=provider)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)

    await app.state.strategy_runner._mark_running(run_id)

    async with app.state.db.session_factory() as session:
        run = await session.get(StrategyRun, run_id)
        assert run.params["price_caliber"] == "forward"
        assert "price_as_of_max" not in run.params
        assert "price_watermark_status" not in run.params
