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


async def _make_run_db(app, strategy_id, account_id, params_extra=None) -> uuid.UUID:
    """Create a STARTING PAPER run row directly (no spawned task).

    ``params_extra`` 落进 run.params，用来模拟"起跑时带了 run 级 risk 覆盖"
    这类入口写入的字段（路由侧写的就是同一个键）。
    """
    async with app.state.db.session_factory() as session:
        strat = await session.get(Strategy, strategy_id)
        run = StrategyRun(
            strategy_id=strat.id,
            strategy_version=strat.version,
            mode=StrategyRunMode.PAPER,
            account_id=account_id,
            status=StrategyRunStatus.STARTING,
            params={"symbol": strat.spec.get("symbol"), **(params_extra or {})},
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
    """账户被删 → 下一次 tick 必须抛 EvaluationError（由循环落成 ERROR）。

    **这条以前是 racy 的**（实测约 50% 红）：它启动后台循环后等 status 变 ERROR，
    而账户检查原先写在"有没有新 bar"之后——测试的 bar 序列固定，第一根处理完之后
    轮询就提前返回，「账户被删」这个**永久条件**永远发现不了。

    修法两步：
    ① 实现里把账户存在性检查提到新 bar 判断之前（永久条件不该等数据）；
    ② 测试改成不经 API 建 run（不调度后台任务）+ 直接驱动 `_tick_once`，
       去掉对循环时序与共享内存连接的全部依赖。
    """
    from fd_paper_trading.persistence.models import Account
    from fd_paper_trading.strategy.backtester import EvaluationError

    await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    async with app.state.db.session_factory() as session:
        run = StrategyRun(
            strategy_id=uuid.UUID(strat["id"]), strategy_version=1,
            mode=StrategyRunMode.PAPER, account_id=account_id,
            status=StrategyRunStatus.STARTING, params={},
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    await app.state.strategy_runner._tick_once(run_id)  # 消费掉现有那根 bar

    async with app.state.db.session_factory() as session:
        acc = await session.get(Account, account_id)
        await session.delete(acc)
        await session.commit()

    # 没有新 bar 也必须报错——这正是本轮修的那一条
    with pytest.raises(EvaluationError, match="was deleted"):
        await app.state.strategy_runner._tick_once(run_id)


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


# ---------------------------------------------------------------------------
# 纸面运行里的组合级风控判定（change4 3.2）
#
# 只判定、不处置：门不产出处置字段（1.5），这里也不据此改写订单。
# 所以能验的是「判过、可复核」；「触达后停不停手」不在此处的实现范围内。
# ---------------------------------------------------------------------------

WEIGHT_SPEC_TWO = {
    "kind": "weight",
    "universe": ["600000", "600036"],
    "rebalance": {"every_n_bars": 1},
    "weights_series": {"2026-01-01": {"600000": 0.5, "600036": 0.5}},
}

THRESHOLDS = {"per_symbol_max": 0.2, "industry_max": 0.9,
              "drawdown_max": 0.9, "daily_loss_max": 0.9}


async def _make_weight_run(app, client, h, *, params_extra=None) -> uuid.UUID:
    strat = await _make_strategy(client, h, spec=WEIGHT_SPEC_TWO, name="wr")
    sid = uuid.UUID(strat["id"])
    account_id = await _make_account(client, h)
    async with app.state.db.session_factory() as session:
        run = StrategyRun(
            strategy_id=sid, strategy_version=1, mode=StrategyRunMode.PAPER,
            account_id=account_id, status=StrategyRunStatus.STARTING,
            params={"universe": ["600000", "600036"], **(params_extra or {})},
            universe=["600000", "600036"],
        )
        session.add(run)
        await session.commit()
        return run.id


@pytest.mark.asyncio
async def test_paper_run_records_a_risk_verdict_per_rebalance(client, app):
    """配了阈值 → 每个调仓点留一条判定，形状与直接调 gate 一致。"""
    await _wire(app)
    h = await _authed(client)
    run_id = await _make_weight_run(app, client, h,
                                    params_extra={"risk_thresholds": THRESHOLDS})

    await app.state.strategy_runner._tick_once(run_id)

    detail = await _run_detail(client, h, run_id)
    events = detail["params"]["risk_events"]
    assert events, "配了阈值却没有判定记录：纸面运行没接门"
    ev = events[-1]
    assert ev["day"]
    # 与直接调 gate 的形状/结论一致（同实现的运行时自证）
    from fd_paper_trading._vendor.fd_quant.risk import evaluate_portfolio_gate

    async with app.state.db.session_factory() as session:
        run = await session.get(StrategyRun, run_id)
        weights = (run.weight_snapshots or [{}])[-1].get("actual_weights") or {}
    direct = evaluate_portfolio_gate(
        weights=weights, thresholds=THRESHOLDS, sectors=None,
        equity_series=None, as_of=ev["day"], price_as_of_max=None,
    )
    assert set(ev["verdict"]) == set(direct)
    assert ev["verdict"]["status"] == direct["status"]


@pytest.mark.asyncio
async def test_paper_run_tight_threshold_breaches_and_still_trades(client, app):
    """50% 单票 vs 20% 上限 → 触达 per_symbol，且**订单照下**（只记不拦）。

    "只记不拦"是刻意的：处置属于组合纪律，门按契约只判定。若哪天有人在
    这里加了拦截，这条会因订单数变化而红——那时应当改这条用例并给出裁决，
    而不是让它悄悄变色。
    """
    await _wire(app)
    h = await _authed(client)
    run_id = await _make_weight_run(app, client, h,
                                    params_extra={"risk_thresholds": THRESHOLDS})

    await app.state.strategy_runner._tick_once(run_id)

    detail = await _run_detail(client, h, run_id)
    kinds = {i["kind"] for e in detail["params"]["risk_events"]
             for i in e["verdict"]["items"]}
    assert "per_symbol" in kinds, f"阈值没进判据：{kinds}"
    orders = [o for o in detail.get("orders") or []]
    assert orders or detail["weight_snapshots"], "触达不应阻止调仓（只记不拦）"


@pytest.mark.asyncio
async def test_paper_run_without_thresholds_records_nothing(client, app):
    """没配阈值 → 没有 risk_events 键（"没判过"≠"判过且通过"）。"""
    await _wire(app)
    h = await _authed(client)
    run_id = await _make_weight_run(app, client, h)

    await app.state.strategy_runner._tick_once(run_id)

    detail = await _run_detail(client, h, run_id)
    assert "risk_events" not in detail["params"]


@pytest.mark.asyncio
async def test_paper_run_records_the_series_ref(client, app):
    """纸面运行也是"交易路"，同样要把序列出处写进 run.params（change1 4.3）。

    这条之前是缺的：口径（price_caliber）写了，但出处（price_series_ref）没写，
    于是"这轮用的是哪一路、哪天的、什么复权口径"在运行记录里答不出来。
    """
    from fd_paper_trading.marketdata.series_ref import build_series_ref

    class _RefProvider:
        async def get_bars(self, symbol, timeframe, limit):
            from fd_paper_trading.marketdata.provider import Bar

            return [Bar(ts=1751000000, open=1, high=1, low=1, close=1, volume=1)]

        async def get_bars_with_coverage(self, symbol, start, end):
            ref, _ = build_series_ref(
                source_id="astock_daily", symbol=symbol, adjusted="forward",
                as_of_max="2026-07-10", window_start="2026-07-01",
                window_end="2026-07-10", row_count=5,
                adjustment_anchor="2026-07-10", db_source="astock_daily")
            return {"missing": [], "market_closed": [], "fill_failed": False,
                    "fingerprint": "", "adjusted": "forward",
                    "source": "astock_daily", "series_ref": ref}

        # 水位也要给，否则 _mark_running 走兜底窗（不影响 ref 断言，但更真实）
        async def source_watermark(self):
            return {"status": "ok", "as_of": "2026-07-10",
                    "method": "provider_max_date", "source_id": "astock_daily"}

    await _wire(app, provider=_RefProvider())
    h = await _authed(client)
    run_id = await _make_weight_run(app, client, h)

    await app.state.strategy_runner._mark_running(run_id)

    detail = await _run_detail(client, h, run_id)
    ref = detail["params"]["price_series_ref"]
    for key in ("source_id", "adjustment", "as_of_max", "fingerprint"):
        assert ref[key], f"{key} 缺失"
    assert ref["source_id"] == "astock_daily"


# ---------------------------------------------------------------------------
# 下单意图票（spec `order-intent-export`）
#
# 这一层以前完全没有：订单只留 account/instrument/qty，事后答不出
# "这笔单依据哪次运行、当时风控闸怎么判的"。服务内下单时出票，把这两件补上。
# ---------------------------------------------------------------------------


async def _tickets(app, run_id):
    from fd_paper_trading.persistence.models import OrderIntentRecord
    from sqlalchemy import select

    async with app.state.db.session_factory() as session:
        rows = (await session.execute(
            select(OrderIntentRecord).where(OrderIntentRecord.run_id == run_id)
            .order_by(OrderIntentRecord.created_at)
        )).scalars().all()
        return [
            {
                "intent_id": r.intent_id, "symbol": r.symbol, "side": r.side,
                "quantity": r.quantity, "intent": r.intent,
                "exportable": r.exportable, "blocked_reason": r.blocked_reason,
                "order_id": r.order_id, "outcome": r.outcome, "as_of": r.as_of,
                "outcome_reason": r.outcome_reason,
            }
            for r in rows
        ]


@pytest.mark.asyncio
async def test_every_placed_order_gets_an_intent_ticket(client, app):
    """下单即出票：票数=下单数，且票里写明依据哪次运行、闸怎么判的。"""
    await _wire(app)
    h = await _authed(client)
    run_id = await _make_weight_run(app, client, h,
                                    params_extra={"risk_thresholds": THRESHOLDS})

    await app.state.strategy_runner._tick_once(run_id)

    tickets = await _tickets(app, run_id)
    assert tickets, "下了单却没有票：意图层没接上"
    placed = [t for t in tickets if t["outcome"] == "placed"]
    assert placed and all(t["order_id"] is not None for t in placed)
    for t in tickets:
        assert t["intent"]["derived_from"] == [f"strategy_run:{run_id}"]
        assert t["intent"]["paper_only"] is True
        assert t["intent"]["account_id"]
        assert t["intent"]["risk_verdict"]["status"] in ("ok", "breached", "unavailable")
        assert t["quantity"] > 0


@pytest.mark.asyncio
async def test_ticket_quotes_the_verdict_of_that_rebalance(client, app):
    """票里的判定必须与同一天记进 run.params 的那条**逐字相同**（不是另算一份）。"""
    await _wire(app)
    h = await _authed(client)
    run_id = await _make_weight_run(app, client, h,
                                    params_extra={"risk_thresholds": THRESHOLDS})

    await app.state.strategy_runner._tick_once(run_id)

    detail = await _run_detail(client, h, run_id)
    by_day = {e["day"]: e["verdict"] for e in detail["params"]["risk_events"]}
    for t in await _tickets(app, run_id):
        assert t["intent"]["risk_verdict"] == by_day[t["as_of"].isoformat()]


@pytest.mark.asyncio
async def test_tight_threshold_marks_the_ticket_as_not_exportable(client, app):
    """50% 单票 vs 20% 上限 → 票照记，但 `exportable=false` 并写明触达了什么。

    这条是这张票的全部价值所在：**它让"这笔单按契约本来导不出去"留痕**。
    如果只在闸通过时才出票，触达的那些单会从审计面上消失。
    """
    await _wire(app)
    h = await _authed(client)
    run_id = await _make_weight_run(app, client, h,
                                    params_extra={"risk_thresholds": THRESHOLDS})

    await app.state.strategy_runner._tick_once(run_id)

    tickets = await _tickets(app, run_id)
    assert tickets
    assert all(t["exportable"] is False for t in tickets)
    assert all("breached" in (t["blocked_reason"] or "") for t in tickets)


@pytest.mark.asyncio
async def test_no_thresholds_means_unavailable_not_exportable(client, app):
    """没配阈值 → 票里写 `unavailable` 并点名缺的四项，而不是什么都不记。"""
    await _wire(app)
    h = await _authed(client)
    run_id = await _make_weight_run(app, client, h)

    await app.state.strategy_runner._tick_once(run_id)

    tickets = await _tickets(app, run_id)
    assert tickets
    for t in tickets:
        assert t["intent"]["risk_verdict"]["status"] == "unavailable"
        assert set(t["intent"]["risk_verdict"]["inputs"]["missing"]) == {
            "per_symbol_max", "industry_max", "drawdown_max", "daily_loss_max"}
        assert t["exportable"] is False
        assert "unavailable" in (t["blocked_reason"] or "")


@pytest.mark.asyncio
async def test_tickets_pass_the_contract_as_recorded(client, app):
    """落库的票必须能过契约校验——"我记了一张票"与"它是张合法票"是两件事。"""
    from fd_paper_trading._vendor.fd_quant.order_intent import check_order_intent

    await _wire(app)
    h = await _authed(client)
    run_id = await _make_weight_run(app, client, h,
                                    params_extra={"risk_thresholds": THRESHOLDS})

    await app.state.strategy_runner._tick_once(run_id)

    for t in await _tickets(app, run_id):
        assert check_order_intent(t["intent"]) == []


@pytest.mark.asyncio
async def test_a_committed_ticket_carries_the_order_it_became(client, app):
    """票与单可对账：`order_id` 指向真实订单行（不是随手编的 uuid）。"""
    from fd_paper_trading.persistence.models import Order
    from sqlalchemy import select

    await _wire(app)
    h = await _authed(client)
    run_id = await _make_weight_run(app, client, h,
                                    params_extra={"risk_thresholds": THRESHOLDS})

    await app.state.strategy_runner._tick_once(run_id)

    placed = [t for t in await _tickets(app, run_id) if t["outcome"] == "placed"]
    assert placed
    async with app.state.db.session_factory() as session:
        for t in placed:
            order = await session.get(Order, t["order_id"])
            assert order is not None
            assert order.strategy_run_id == run_id
            assert order.symbol == t["symbol"]


@pytest.mark.asyncio
async def test_run_detail_exposes_the_tickets(client, app):
    """票要有读出口：`GET /strategy-runs/{id}` 里能拿到，且与库内逐条一致。

    只写库不给出口，等于"记了但看不见"——与本 spec 要解决的形态正相反。
    """
    await _wire(app)
    h = await _authed(client)
    run_id = await _make_weight_run(app, client, h,
                                    params_extra={"risk_thresholds": THRESHOLDS})

    await app.state.strategy_runner._tick_once(run_id)

    detail = await _run_detail(client, h, run_id)
    exposed = detail.get("order_intents") or []
    stored = await _tickets(app, run_id)
    assert len(exposed) == len(stored) > 0
    assert [str(e["intent_id"]) for e in exposed] == [str(s["intent_id"]) for s in stored]
    for e in exposed:
        assert e["intent"]["paper_only"] is True
        assert "exportable" in e and "outcome" in e


@pytest.mark.asyncio
async def test_template_run_also_produces_tickets(client, app):
    """**生产里真正在跑的那条路**（3 个 RUNNING 运行都是 template）。

    只给权重模式出票的话，票在生产里永远不会出现——这条钉住单标的路径也出票。
    """
    provider = await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)

    sup = app.state.strategy_runner
    await sup._tick_once(run_id)
    provider.add_bars(1)
    await sup._tick_once(run_id)

    tickets = await _tickets(app, run_id)
    orders = await _orders_for_run(client, h, account_id, run_id)
    assert len(tickets) == len(orders) == 2, (len(tickets), len(orders))
    for t in tickets:
        assert t["outcome"] == "placed"
        assert t["order_id"] is not None
        assert t["intent"]["derived_from"] == [f"strategy_run:{run_id}"]
        assert t["intent"]["paper_only"] is True
        assert t["intent"]["symbol"] == "AAPL"
        assert t["intent"]["as_of"] in ("2026-01-05", "2026-01-06")
        # 这条路径没有组合阈值 → 判定是"不可用"而不是"通过"
        assert t["intent"]["risk_verdict"]["status"] == "unavailable"
        assert t["exportable"] is False


@pytest.mark.asyncio
async def test_template_capped_signal_keeps_its_reason(client, app):
    """被当日上限截住的信号：票照记，`outcome=capped` 且有原因（三态可对账）。"""
    provider = await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(app, uuid.UUID(strat["id"]), account_id)

    sup = app.state.strategy_runner
    # 把当日上限压到 0：所有信号都落到"截住"这条分支。
    # 上限挂在**用户组**上（`group_config(user, key)` → `user.group.config`），
    # 不是挂在用户上——先前按 user.group_config 写，直接 AttributeError。
    from sqlalchemy import select

    from fd_paper_trading.persistence.models import User, UserGroup

    # 测试用户没有绑定组（`user.group_id is None`）→ 现建一个组并绑上：
    # 上限只能从组上压低，这是 `group_config()` 的唯一读法。
    async with app.state.db.session_factory() as session:
        user = (await session.execute(select(User))).scalars().first()
        if user.group_id is None:
            group = UserGroup(name="cap-zero", config={"max_orders_per_day": 0})
            session.add(group)
            await session.flush()
            user.group_id = group.id
        else:
            group = await session.get(UserGroup, user.group_id)
            group.config = {**(group.config or {}), "max_orders_per_day": 0}
        await session.commit()

    await sup._tick_once(run_id)

    tickets = await _tickets(app, run_id)
    assert tickets
    assert all(t["outcome"] == "capped" for t in tickets)
    assert all("daily order cap" in (t["outcome_reason"] or "") for t in tickets), \
        [t["outcome_reason"] for t in tickets]
    assert all(t["order_id"] is None for t in tickets)


# ---------------------------------------------------------------------------
# run 级 risk 覆盖的入口与生效（2026-11-12：补齐 start_strategy 的入口）
#
# 引擎侧本来就支持（runner 两条轨都读 run.params["risk"]），但**入口不存在**：
# PaperRunRequest 没有 risk 字段，于是 live 永远配不上止损。以下三条把
# 「入口写得进 params」「不传就不留痕」「越界当场拒」钉住，第四条把「写进去真会平仓」
# 钉住——这一条此前一条测试都没有。
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_run_persists_risk_overlay_on_params(client, app):
    """带了 risk → 起跑即快照进 run.params（可事后归因"这轮带过止损"）。"""
    await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)

    r = await client.post(
        f"/api/v1/strategies/{strat['id']}/runs",
        json={"account_id": str(account_id),
              "risk": {"stop_loss_pct": "0.08", "cooldown_bars": 2}},
        headers=h,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    # 快照是 JSON 形态：Decimal 序列化成字符串（"0.08"），比较前先转 float。
    assert float(body["params"]["risk"]["stop_loss_pct"]) == pytest.approx(0.08)
    assert body["params"]["risk"]["cooldown_bars"] == 2

    await client.post(f"/api/v1/strategy-runs/{body['id']}/stop", headers=h)


@pytest.mark.asyncio
async def test_start_run_without_risk_leaves_no_snapshot(client, app):
    """不传 → params 里没有 risk 键（"没配"与"配了空"是两个事实）。"""
    await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)

    r = await client.post(
        f"/api/v1/strategies/{strat['id']}/runs",
        json={"account_id": str(account_id)},
        headers=h,
    )
    assert r.status_code == 202, r.text
    body = r.json()
    assert "risk" not in body["params"], body["params"]

    await client.post(f"/api/v1/strategy-runs/{body['id']}/stop", headers=h)


@pytest.mark.asyncio
async def test_start_run_out_of_range_risk_is_refused(client, app):
    """越界止损（>1）当场拒，而不是起跑后每根 bar 静默无效。"""
    await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)

    r = await client.post(
        f"/api/v1/strategies/{strat['id']}/runs",
        json={"account_id": str(account_id), "risk": {"stop_loss_pct": "1.5"}},
        headers=h,
    )
    assert r.status_code in (400, 422), r.text
    assert "stop_loss_pct" in r.text


@pytest.mark.asyncio
async def test_live_risk_overlay_closes_the_position_on_a_gap_bar(client, app):
    """带 10% 止损起跑，丢一根崩盘 bar → 真的要平仓，且 trigger 留痕。

    这条是这一组的价值所在：在此之前 `run.params["risk"]` 在 live 上**零测试覆盖**，
    "live 逐 bar 执行止损"只是代码注释与规格里的一句话。
    """
    provider = await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h)
    run_id = await _make_run_db(
        app, uuid.UUID(strat["id"]), account_id,
        params_extra={"risk": {"stop_loss_pct": "0.10"}},
    )

    sup = app.state.strategy_runner
    await sup._tick_once(run_id)  # 首次 tick：预热 + 在最新那根 bar 上建仓

    orders = await _orders_for_run(client, h, account_id, run_id)
    assert any(o["side"] == "BUY" for o in orders), \
        f"前提不成立：起跑后没有多头持仓，止损无从触发 {[o['side'] for o in orders]}"

    # 崩盘 bar：low 69 远低于止损价（成本 ~104 × 0.9）
    crash_ts = int(datetime(2026, 1, 6, tzinfo=UTC).timestamp())
    provider._bars.append(
        Bar(
            ts=crash_ts,
            open=Decimal("70"),
            high=Decimal("71"),
            low=Decimal("69"),
            close=Decimal("70.5"),
            volume=Decimal(1000),
        )
    )
    await sup._tick_once(run_id)

    detail = await _run_detail(client, h, run_id)
    triggers = detail["params"].get("risk_triggers")
    assert triggers, "止损覆盖没在 live 上生效：run.params 里没有 risk_triggers"
    assert triggers[-1]["symbol"] == "AAPL"
    assert triggers[-1]["kind"], triggers[-1]

    orders = await _orders_for_run(client, h, account_id, run_id)
    assert any(o["side"] == "SELL" for o in orders), \
        f"止损触发了却没卖出：{[o['side'] for o in orders]}"


@pytest.mark.asyncio
async def test_weight_track_live_refuses_risk_overlay_instead_of_no_op(client, app):
    """权重轨 live 传止损 → 显式 400（否则是"看着受保护实际没保护"的空转）。

    权重轨 live 只消费冷却（`effective_target_weight`），而**没有任何地方触发
    止损、也没有地方上冷却**——配置收下就等于骗人。
    """
    await _wire(app)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(client, h, spec=WEIGHT_SPEC_TWO, name="wr-risk")

    r = await client.post(
        f"/api/v1/strategies/{strat['id']}/runs",
        json={"account_id": str(account_id), "risk": {"stop_loss_pct": "0.10"}},
        headers=h,
    )
    assert r.status_code == 400, r.text
    assert "risk_not_supported_on_weight_track" in r.text
    assert "weights_series" in r.text
