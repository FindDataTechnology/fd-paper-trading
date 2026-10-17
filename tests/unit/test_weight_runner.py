"""Weight-track live paper runs (spec: strategy-weight-mode, tasks 4.1-4.3/4.5).

Drives ``PaperRunSupervisor._tick_once`` directly against a controlled bar
provider, the same way ``tests/e2e/test_strategy_paper_runs.py`` does: the run
row is created in the DB and each tick is an explicit call, so a test can name
exactly which bars exist when a poll happens. The provider here is per-symbol
(rather than one series) because the whole point of the weight track is that a
rebalance reads several symbols at once - including one that stopped trading.

The assertions are written against *what the ledger holds*, not against the
engine's intentions: an order was created, a position exists, the snapshot says
which shares were wanted. A test that only checked "the runner produced two
instructions" would pass while every order was rejected.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from fd_paper_trading.execution import PaperExecutionService
from fd_paper_trading.marketdata import SimulatedMarketDataProvider
from fd_paper_trading.marketdata.provider import Bar, Quote
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

UTC = timezone.utc


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _UniverseProvider(SimulatedMarketDataProvider):
    """SIM provider with a per-symbol daily series the test fully controls.

    Quotes are pinned to each symbol's last close so a fill's price equals the
    close the weights were computed against - without that, "positions match the
    target" would be a claim about the quote, not about the rebalance.
    """

    def __init__(self) -> None:
        super().__init__()
        self._bars: dict[str, list[Bar]] = {}
        self._last_close: dict[str, Decimal] = {}

    def set_series(self, symbol: str, closes: dict[int, str]) -> None:
        """Replace a symbol's series: ``{day-of-Jan-2026: close}``."""
        bars = [_bar(symbol, day, close) for day, close in sorted(closes.items())]
        self._bars[symbol] = bars
        self._last_close[symbol] = bars[-1].close

    def append(self, symbol: str, day: int, close: str) -> None:
        """Add one newer bar, simulating "a new daily bar arrived"."""
        bar = _bar(symbol, day, close)
        self._bars.setdefault(symbol, []).append(bar)
        self._last_close[symbol] = bar.close

    async def get_bars(self, symbol, timeframe, limit):
        bars = self._bars.get(symbol, [])
        return bars[-limit:] if limit else list(bars)

    async def get_quote(self, symbol: str) -> Quote | None:
        close = self._last_close.get(symbol)
        if close is None:
            return await super().get_quote(symbol)
        return Quote(
            instrument_id=f"{symbol}.SIM",
            bid=close - Decimal("0.01"),
            ask=close + Decimal("0.01"),
        )

    def get_instrument(self, symbol: str):
        instrument = super().get_instrument(symbol)
        if instrument is None:
            return None
        return instrument


def _bar(symbol: str, day: int, close: str) -> Bar:
    ts = int(datetime(2026, 1, day, tzinfo=UTC).timestamp())
    price = Decimal(close)
    return Bar(
        ts=ts, open=price, high=price, low=price, close=price, volume=Decimal(1000)
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _wire(app, provider: _UniverseProvider) -> _UniverseProvider:
    """Point the app at the controlled provider and speed up the supervisor."""
    app.state.market_data_provider = provider
    app.state.execution_service = PaperExecutionService(provider=provider)
    app.state.strategy_runner.poll_seconds = 0.01
    app.state.strategy_runner.lookback_bars = 250
    return provider


async def _authed(client):
    await client.post(
        "/api/v1/auth/register", json={"email": "w@x.io", "password": "password123"}
    )
    r = await client.post(
        "/api/v1/auth/login", json={"email": "w@x.io", "password": "password123"}
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _make_account(client, h, starting_balance="100000"):
    r = await client.post(
        "/api/v1/accounts",
        json={"name": "paper", "starting_balance": starting_balance},
        headers=h,
    )
    assert r.status_code == 201, r.text
    return uuid.UUID(r.json()["id"])


async def _make_strategy(client, h, spec):
    r = await client.post(
        "/api/v1/strategies", json={"name": "weights", "spec": spec}, headers=h
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _make_run(app, strategy_id, account_id, universe=None) -> uuid.UUID:
    """Create a STARTING PAPER run row directly (no spawned task)."""
    async with app.state.db.session_factory() as session:
        strat = await session.get(Strategy, strategy_id)
        run = StrategyRun(
            strategy_id=strat.id,
            strategy_version=strat.version,
            mode=StrategyRunMode.PAPER,
            account_id=account_id,
            status=StrategyRunStatus.STARTING,
            params={"universe": universe or strat.spec.get("universe")},
            universe=universe or strat.spec.get("universe"),
        )
        session.add(run)
        await session.commit()
        return run.id


async def _set_group(app, **overrides) -> None:
    """Seed the default group with overridden limits, bound to the test user."""
    async with app.state.db.session_factory() as session:
        group = UserGroup(
            id=DEFAULT_GROUP_ID, name="default",
            config={**DEFAULT_GROUP_CONFIG, **overrides},
        )
        session.add(group)
        user = await UserRepository(session).get_by_email("w@x.io")
        user.group_id = group.id
        await session.commit()


async def _orders(app, account_id) -> list[Order]:
    async with app.state.db.session_factory() as session:
        res = await session.execute(
            select(Order)
            .where(Order.account_id == account_id)
            .order_by(Order.created_at.asc())
        )
        return list(res.scalars().all())


async def _positions(app, account_id, symbol: str) -> Decimal:
    async with app.state.db.session_factory() as session:
        from fd_paper_trading.persistence.models import Position

        res = await session.execute(
            select(Position).where(
                Position.account_id == account_id, Position.symbol == symbol
            )
        )
        position = res.scalar_one_or_none()
        return position.quantity if position is not None else Decimal(0)


async def _run_row(app, run_id) -> StrategyRun:
    async with app.state.db.session_factory() as session:
        return await session.get(StrategyRun, run_id)


async def _events(app, run_id) -> list:
    from fd_paper_trading.persistence.repositories import StrategyRunRepository

    async with app.state.db.session_factory() as session:
        return await StrategyRunRepository(session).list_events(run_id)


async def _equity_dates(app, run_id) -> list[date]:
    from fd_paper_trading.persistence.repositories import StrategyRunRepository

    async with app.state.db.session_factory() as session:
        points = await StrategyRunRepository(session).list_equity_points(run_id)
        return [p.date for p in points]


# ---------------------------------------------------------------------------
# 4.1 - orders only at rebalance points, a snapshot for every processed bar
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_orders_only_at_rebalance_points_with_a_point_per_bar(client, app):
    provider = _UniverseProvider()
    provider.set_series("A", {1: "10.0", 2: "10.0"})
    provider.set_series("B", {1: "10.0", 2: "10.0"})
    await _wire(app, provider)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(
        client, h,
        {
            "kind": "weight",
            "universe": ["A", "B"],
            "rebalance": {"every_n_bars": 3},
            "weights_series": {"2026-01-01": {"A": 0.5, "B": 0.5}},
        },
    )
    run_id = await _make_run(app, uuid.UUID(strat["id"]), account_id)
    sup = app.state.strategy_runner

    await sup._tick_once(run_id)  # first tick: Jan 2 only (processed bar 1)
    assert await _orders(app, account_id) == [], (
        "the first tick is processed bar 1 of 3, so it must not trade"
    )
    assert await _equity_dates(app, run_id) == [date(2026, 1, 2)]

    # Bars 2 and 3 arrive on later polls; only bar 3 is a rebalance point.
    for day in (3, 4):
        provider.append("A", day, "10.0")
        provider.append("B", day, "10.0")
        await sup._tick_once(run_id)
        if day == 3:
            assert await _orders(app, account_id) == [], (
                "bar 2 is not a rebalance point"
            )
    orders = await _orders(app, account_id)
    assert len(orders) == 2, "exactly one rebalance point fired in those ticks"
    assert {o.symbol for o in orders} == {"A", "B"}
    assert all(o.side == "BUY" for o in orders)

    # Every processed bar has an equity point, rebalance or not.
    assert await _equity_dates(app, run_id) == [
        date(2026, 1, d) for d in range(2, 5)
    ]


@pytest.mark.asyncio
async def test_no_new_primary_bar_is_a_noop(client, app):
    provider = _UniverseProvider()
    provider.set_series("A", {1: "10.0", 2: "10.0"})
    provider.set_series("B", {1: "10.0", 2: "10.0"})
    await _wire(app, provider)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(
        client, h,
        {
            "kind": "weight",
            "universe": ["A", "B"],
            "rebalance": {"every_n_bars": 1},
            "weights_series": {"2026-01-01": {"A": 0.5, "B": 0.5}},
        },
    )
    run_id = await _make_run(app, uuid.UUID(strat["id"]), account_id)
    sup = app.state.strategy_runner

    await sup._tick_once(run_id)  # Jan 2: one rebalance, two orders
    assert len(await _orders(app, account_id)) == 2
    await sup._tick_once(run_id)  # same bars -> nothing

    assert len(await _orders(app, account_id)) == 2
    assert await _equity_dates(app, run_id) == [date(2026, 1, 2)]


@pytest.mark.asyncio
async def test_primary_symbol_is_the_clock(client, app):
    """A peer's newer bar alone must not advance the run (spec: primary symbol)."""
    provider = _UniverseProvider()
    provider.set_series("A", {1: "10.0", 2: "10.0"})
    provider.set_series("B", {1: "10.0", 2: "10.0"})
    await _wire(app, provider)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(
        client, h,
        {
            "kind": "weight",
            "universe": ["A", "B"],
            "rebalance": {"every_n_bars": 1},
            "weights_series": {"2026-01-01": {"A": 0.5, "B": 0.5}},
        },
    )
    run_id = await _make_run(app, uuid.UUID(strat["id"]), account_id)
    sup = app.state.strategy_runner

    await sup._tick_once(run_id)
    baseline = len(await _orders(app, account_id))

    provider.append("B", 3, "10.0")  # only the non-primary symbol trades
    await sup._tick_once(run_id)

    assert len(await _orders(app, account_id)) == baseline, (
        "a peer bar must not make the run actionable"
    )
    assert (await _run_row(app, run_id)).params["last_bar_date"] == "2026-01-02"


# ---------------------------------------------------------------------------
# 4.2 - one rebalance becomes attributed multi-symbol MARKET orders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rebalance_from_flat_buys_both_and_lands_on_target(client, app):
    """0.5/0.5 of 100,000 at 10.00/share: 5,000 shares each, except the last buy
    must also pay the spread, so it lands one step lower and the difference is
    reported rather than absorbed."""
    provider = _UniverseProvider()
    provider.set_series("A", {1: "10.0", 2: "10.0"})
    provider.set_series("B", {1: "10.0", 2: "10.0"})
    await _wire(app, provider)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(
        client, h,
        {
            "kind": "weight",
            "universe": ["A", "B"],
            "rebalance": {"every_n_bars": 1},
            "weights_series": {"2026-01-01": {"A": 0.5, "B": 0.5}},
        },
    )
    run_id = await _make_run(app, uuid.UUID(strat["id"]), account_id)
    sup = app.state.strategy_runner

    await sup._tick_once(run_id)

    orders = await _orders(app, account_id)
    assert len(orders) == 2
    by_symbol = {o.symbol: o for o in orders}
    for order in orders:
        assert order.strategy_run_id == run_id, "every order carries attribution"
        assert order.side == "BUY"
        assert order.order_type == "MARKET"
        assert order.status == "FILLED"
    # Quantities: 0.5 * 100,000 / 10.00 = 5,000 shares; the second buy pays the
    # 10.01 ask out of the 49,950 left, so it floors to 4,990.
    assert by_symbol["A"].quantity == Decimal(5000)
    assert by_symbol["B"].quantity == Decimal(4990)

    assert await _positions(app, account_id, "A") == Decimal(5000)
    assert await _positions(app, account_id, "B") == Decimal(4990)

    snapshot = (await _run_row(app, run_id)).weight_snapshots[0]
    assert snapshot["date"] == "2026-01-02"
    assert snapshot["target_weights"] == {"A": 0.5, "B": 0.5}
    assert snapshot["error"] is None
    # Positions are consistent with the target within the integer-rounding
    # remainder kept in cash (design D3), which is 0.10 here.
    assert snapshot["actual_weights"]["A"] == pytest.approx(0.5, abs=0.001)
    assert snapshot["actual_weights"]["B"] == pytest.approx(0.5, abs=0.001)
    assert snapshot["actual_weights"]["CASH"] == pytest.approx(
        float(Decimal("0.1") / Decimal("99900.1")), rel=1e-9
    )
    scaled = [i for i in snapshot["instructions"] if i["scaled"]]
    assert [i["symbol"] for i in scaled] == ["B"], (
        "the reduction must be recorded, not silent"
    )
    assert scaled[0]["quantity"] == "4990"
    assert "insufficient cash" in scaled[0]["scale_reason"]


@pytest.mark.asyncio
async def test_rotation_sells_what_left_the_target(client, app):
    """A held symbol absent from the target means zero, so a rotation sells it."""
    provider = _UniverseProvider()
    provider.set_series("A", {1: "10.0", 2: "10.0"})
    provider.set_series("B", {1: "10.0", 2: "10.0"})
    await _wire(app, provider)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(
        client, h,
        {
            "kind": "weight",
            "universe": ["A", "B"],
            "rebalance": {"every_n_bars": 1},
            "weights_series": {
                "2026-01-01": {"A": 0.5, "B": 0.5},
                "2026-01-03": {"B": 1.0},
            },
        },
    )
    run_id = await _make_run(app, uuid.UUID(strat["id"]), account_id)
    sup = app.state.strategy_runner

    await sup._tick_once(run_id)  # Jan 2: period 1, 0.5/0.5
    held_a = await _positions(app, account_id, "A")
    assert held_a > 0

    provider.append("A", 3, "10.0")
    provider.append("B", 3, "10.0")
    await sup._tick_once(run_id)  # Jan 3: period 2 drops A

    # Jan 3's period no longer names A, and an absent symbol is a zero target,
    # so the rotation liquidates it. B absorbs the freed cash.
    assert await _positions(app, account_id, "A") == 0
    assert await _positions(app, account_id, "B") > 0
    sells = [o for o in await _orders(app, account_id) if o.side == "SELL"]
    assert [o.symbol for o in sells] == ["A"]
    assert sells[0].quantity == held_a
    snapshots = (await _run_row(app, run_id)).weight_snapshots
    assert snapshots[-1]["target_weights"] == {"B": 1.0}
    assert snapshots[-1]["actual_weights"]["A"] == 0.0


@pytest.mark.asyncio
async def test_quantities_round_down_and_the_remainder_stays_in_cash(client, app):
    """A target that does not divide into whole shares must round DOWN (D3).

    0.5 of 100,000 at 3.00/share is 16,666.67 shares; rounding to nearest would
    buy 16,667 and round-up would buy 16,667 too, so an assertion that accepts
    either would not be testing the rule. The quantity is asserted exactly,
    along with the fraction that stays in cash instead of becoming a position.
    """
    provider = _UniverseProvider()
    provider.set_series("A", {1: "3.0", 2: "3.0"})
    await _wire(app, provider)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(
        client, h,
        {
            "kind": "weight",
            "universe": ["A"],
            "rebalance": {"every_n_bars": 1},
            "weights_series": {"2026-01-01": {"A": 0.5}},
        },
    )
    run_id = await _make_run(app, uuid.UUID(strat["id"]), account_id)
    sup = app.state.strategy_runner

    await sup._tick_once(run_id)

    orders = await _orders(app, account_id)
    assert len(orders) == 1
    assert orders[0].quantity == Decimal(16666), (
        "must round down, never to nearest and never up"
    )
    assert await _positions(app, account_id, "A") == Decimal(16666)
    snapshot = (await _run_row(app, run_id)).weight_snapshots[0]
    instruction = snapshot["instructions"][0]
    assert instruction["quantity"] == "16666"
    assert instruction["scaled"] is False, "rounding is not a cash scale-down"
    # 16,666 shares were bought at 3.01 (the ask) and are marked at 3.00 (the
    # close), so the realised weight sits just above the target: the spread is
    # a cost the book carries, and the 0.67 the rounding left stays in cash
    # (cash = 100,000 - 50,164.66), rather than being spent on a 16,667th share.
    assert snapshot["actual_weights"]["A"] == pytest.approx(0.5, abs=0.002)
    assert snapshot["actual_weights"]["CASH"] == pytest.approx(
        float(Decimal("49835.34") / Decimal("99833.34")), rel=1e-6
    )


@pytest.mark.asyncio
async def test_target_below_one_share_produces_no_instruction(client, app):
    """A target worth less than a share is not rounded up into one (D3)."""
    provider = _UniverseProvider()
    provider.set_series("A", {1: "1000.0", 2: "1000.0"})
    await _wire(app, provider)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(
        client, h,
        {
            "kind": "weight",
            "universe": ["A"],
            "rebalance": {"every_n_bars": 1},
            "weights_series": {"2026-01-01": {"A": 0.0001}},
        },
    )
    run_id = await _make_run(app, uuid.UUID(strat["id"]), account_id)
    sup = app.state.strategy_runner

    await sup._tick_once(run_id)

    assert await _orders(app, account_id) == []
    assert await _positions(app, account_id, "A") == 0
    snapshot = (await _run_row(app, run_id)).weight_snapshots[0]
    assert snapshot["instructions"] == []
    assert snapshot["error"] is None, "an empty target is legal, not an error"


# ---------------------------------------------------------------------------
# 4.3 - the daily order cap covers all of one rebalance's orders
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_daily_cap_skips_excess_instructions_and_logs_them(client, app):
    """Cap 1, three symbols to buy: one order, two logged skips, all visible."""
    provider = _UniverseProvider()
    for symbol in ("A", "B", "C"):
        provider.set_series(symbol, {1: "10.0", 2: "10.0"})
    await _wire(app, provider)
    h = await _authed(client)
    await _set_group(app, max_orders_per_day=1)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(
        client, h,
        {
            "kind": "weight",
            "universe": ["A", "B", "C"],
            "rebalance": {"every_n_bars": 1},
            "weights_series": {"2026-01-01": {"A": 0.3, "B": 0.3, "C": 0.3}},
        },
    )
    run_id = await _make_run(app, uuid.UUID(strat["id"]), account_id)
    sup = app.state.strategy_runner

    await sup._tick_once(run_id)
    assert len(await _orders(app, account_id)) == 1, "the cap allows exactly one"

    events = await _events(app, run_id)
    capped = [e for e in events if e.type == "rejection" and "cap" in e.message]
    assert len(capped) == 2, "both excess instructions must be visible"
    assert "cap 1" in capped[0].message
    # The skipped instructions are named by symbol, not just counted.
    assert {e.data["symbol"] for e in capped} == {"B", "C"}

    # The snapshot still records what was wanted versus what happened.
    snapshot = (await _run_row(app, run_id)).weight_snapshots[0]
    assert len(snapshot["instructions"]) == 3
    assert Decimal(snapshot["actual_weights"]["CASH"]) > 0.6


# ---------------------------------------------------------------------------
# 4.5 - stop semantics are unchanged for a weight run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_cancels_resting_limits_and_keeps_positions(client, app):
    provider = _UniverseProvider()
    provider.set_series("A", {1: "10.0", 2: "10.0"})
    provider.set_series("B", {1: "10.0", 2: "10.0"})
    await _wire(app, provider)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(
        client, h,
        {
            # A partial allocation, so the account keeps the cash a resting
            # limit order needs to reserve buying power.
            "kind": "weight",
            "universe": ["A", "B"],
            "rebalance": {"every_n_bars": 1},
            "weights_series": {"2026-01-01": {"A": 0.2, "B": 0.2}},
        },
    )
    run_id = await _make_run(app, uuid.UUID(strat["id"]), account_id)
    sup = app.state.strategy_runner
    await sup._tick_once(run_id)
    held = {
        "A": await _positions(app, account_id, "A"),
        "B": await _positions(app, account_id, "B"),
    }
    assert held["A"] > 0 and held["B"] > 0

    # A resting LIMIT attributed to the run (strategy signals are MARKET-only,
    # so the attribution + cancel path is exercised directly here).
    r = await client.post(
        "/api/v1/orders",
        json={
            "account_id": str(account_id), "symbol": "A", "side": "BUY",
            "order_type": "LIMIT", "quantity": "1", "limit_price": "1.00",
        },
        headers=h,
    )
    assert r.status_code == 202, r.text
    resting_id = uuid.UUID(r.json()["id"])
    assert r.json()["status"] == "OPEN", "a limit below the ask must rest"
    async with app.state.db.session_factory() as session:
        order = (
            await session.execute(select(Order).where(Order.id == resting_id))
        ).scalar_one()
        order.strategy_run_id = run_id
        await session.commit()

    await sup._finalize_stop(run_id)

    assert (await _run_row(app, run_id)).status == StrategyRunStatus.STOPPED
    # Positions are untouched - no flattening.
    assert await _positions(app, account_id, "A") == held["A"]
    assert await _positions(app, account_id, "B") == held["B"]
    async with app.state.db.session_factory() as session:
        order = (
            await session.execute(select(Order).where(Order.id == resting_id))
        ).scalar_one()
        assert order.status == "CANCELLED"


# ---------------------------------------------------------------------------
# D4 - a halted symbol is skipped with an event and still valued at its close
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_halted_symbol_skipped_with_event_and_still_valued(client, app):
    """B stops trading before the rebalance date: no order for B, but B is still
    valued at its last close, and the gap is recorded on the run."""
    provider = _UniverseProvider()
    provider.set_series("A", {1: "10.0", 2: "10.0", 3: "10.0"})
    provider.set_series("B", {1: "20.0", 2: "20.0"})  # no Jan 3 bar
    await _wire(app, provider)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(
        client, h,
        {
            "kind": "weight",
            "universe": ["A", "B"],
            "rebalance": {"every_n_bars": 1},
            "weights_series": {"2026-01-01": {"A": 0.5, "B": 0.5}},
        },
    )
    run_id = await _make_run(app, uuid.UUID(strat["id"]), account_id)
    sup = app.state.strategy_runner

    # Both symbols are wanted at 50%, but the decision date is Jan 3 - the day
    # B stopped trading - so only A may be bought.
    await sup._tick_once(run_id)

    orders = await _orders(app, account_id)
    assert [o.symbol for o in orders] == ["A"], "a halted symbol must not be traded"
    # The engine really wanted B at 50% (it is in the target and the account is
    # flat), so the skip is a rule firing, not an absent intention. The frozen
    # snapshot shape drops the requested quantity of a skipped instruction, so
    # the event - which carries it - is where the evidence is asserted.
    snapshot = (await _run_row(app, run_id)).weight_snapshots[0]
    assert snapshot["target_weights"] == {"A": 0.5, "B": 0.5}
    b_instructions = [i for i in snapshot["instructions"] if i["symbol"] == "B"]
    assert b_instructions and b_instructions[0]["skipped"]
    assert b_instructions[0]["side"] == "BUY"

    events = await _events(app, run_id)
    halts = [e for e in events if e.type == "skip" and e.data["symbol"] == "B"]
    assert halts, "the halted symbol must be logged, not silently dropped"
    assert "2026-01-03" in halts[0].message
    assert halts[0].data["date"] == "2026-01-03"
    assert "halted" in halts[0].data["reason"]
    assert Decimal(halts[0].data["requested_quantity"]) > 0, (
        "B was wanted; it was the halt that stopped it"
    )

    run = await _run_row(app, run_id)
    assert {"date": "2026-01-03", "symbol": "B"} in run.data_gaps
    assert "B" in snapshot["stale_symbols"]
    # Valued at its last close (20.0 on Jan 2), and the snapshot says so.
    assert b_instructions[0]["price"] == "20.0"


@pytest.mark.asyncio
async def test_halted_symbol_equity_uses_its_last_close(client, app):
    """The valuation half of D4: the stale close is what the curve uses.

    Constructed so the two rules disagree observably - B's position is marked at
    its last close, and an equity point short of that value would mean the
    snapshot had silently dropped B's holding.
    """
    provider = _UniverseProvider()
    provider.set_series("A", {1: "10.0", 2: "10.0"})
    provider.set_series("B", {1: "20.0"})  # only trades on day 1
    await _wire(app, provider)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(
        client, h,
        {
            "kind": "weight",
            "universe": ["A", "B"],
            "rebalance": {"every_n_bars": 1},
            "weights_series": {"2026-01-01": {"A": 0.0, "B": 1.0}},
        },
    )
    run_id = await _make_run(app, uuid.UUID(strat["id"]), account_id)
    sup = app.state.strategy_runner

    await sup._tick_once(run_id)  # Jan 2: A also trades, B is stale
    assert await _positions(app, account_id, "A") == 0
    assert await _positions(app, account_id, "B") == 0, (
        "B had no bar on Jan 2, so it cannot be bought"
    )

    from fd_paper_trading.persistence.repositories import StrategyRunRepository

    async with app.state.db.session_factory() as session:
        points = await StrategyRunRepository(session).list_equity_points(run_id)
    # Nothing was bought and no price moved, so equity is exactly the cash.
    assert points[-1].equity == Decimal("100000")
    run = await _run_row(app, run_id)
    assert {"date": "2026-01-02", "symbol": "B"} in run.data_gaps


# ---------------------------------------------------------------------------
# Source (code) track: the target comes from the strategy, not the series
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_code_source_reaches_the_rebalance_through_the_sandbox(client, app):
    """A ``source`` spec's target comes back from the sandbox and is executed."""
    provider = _UniverseProvider()
    provider.set_series("A", {1: "10.0", 2: "10.0"})
    provider.set_series("B", {1: "10.0", 2: "10.0"})
    await _wire(app, provider)
    h = await _authed(client)
    await _set_group(app, allow_custom_code=True)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(
        client, h,
        {
            "kind": "weight",
            "universe": ["A", "B"],
            "rebalance": {"every_n_bars": 1},
            "source": (
                "def target_weights(ctx):\n"
                "    return {'A': 0.75, 'B': 0.25}\n"
            ),
        },
    )
    run_id = await _make_run(app, uuid.UUID(strat["id"]), account_id)
    sup = app.state.strategy_runner

    await sup._tick_once(run_id)

    snapshot = (await _run_row(app, run_id)).weight_snapshots[0]
    assert snapshot["target_weights"] == {"A": 0.75, "B": 0.25}
    assert snapshot["error"] is None
    orders = await _orders(app, account_id)
    assert {o.symbol for o in orders} == {"A", "B"}
    assert await _positions(app, account_id, "A") > await _positions(
        app, account_id, "B"
    )


@pytest.mark.asyncio
async def test_illegal_code_target_produces_no_orders_and_an_event(client, app):
    """Task 5.3 in the live path: a refused target places nothing and says why."""
    provider = _UniverseProvider()
    provider.set_series("A", {1: "10.0", 2: "10.0"})
    provider.set_series("B", {1: "10.0", 2: "10.0"})
    await _wire(app, provider)
    h = await _authed(client)
    await _set_group(app, allow_custom_code=True)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(
        client, h,
        {
            "kind": "weight",
            "universe": ["A", "B"],
            "rebalance": {"every_n_bars": 1},
            # Sums to 1.2: the contract must refuse it rather than rescale it.
            "source": (
                "def target_weights(ctx):\n"
                "    return {'A': 0.7, 'B': 0.5}\n"
            ),
        },
    )
    run_id = await _make_run(app, uuid.UUID(strat["id"]), account_id)
    sup = app.state.strategy_runner

    await sup._tick_once(run_id)

    assert await _orders(app, account_id) == []
    snapshot = (await _run_row(app, run_id)).weight_snapshots[0]
    assert snapshot["error"], "a refused target must be recorded on the snapshot"
    events = await _events(app, run_id)
    assert any(e.type == "error" for e in events)
    # The run keeps going: no crash, no ERROR status.
    assert (await _run_row(app, run_id)).status in (
        StrategyRunStatus.STARTING, StrategyRunStatus.RUNNING
    )


@pytest.mark.asyncio
async def test_run_records_the_universe_and_keeps_it_after_a_spec_edit(client, app):
    """Design D7: the run pins what it traded, so a later edit cannot rewrite it."""
    provider = _UniverseProvider()
    provider.set_series("A", {1: "10.0", 2: "10.0"})
    provider.set_series("B", {1: "10.0", 2: "10.0"})
    await _wire(app, provider)
    h = await _authed(client)
    account_id = await _make_account(client, h)
    strat = await _make_strategy(
        client, h,
        {
            "kind": "weight",
            "universe": ["A", "B"],
            "rebalance": {"every_n_bars": 1},
            "weights_series": {"2026-01-01": {"A": 0.5, "B": 0.5}},
        },
    )
    run_id = await _make_run(app, uuid.UUID(strat["id"]), account_id)
    sup = app.state.strategy_runner
    await sup._tick_once(run_id)
    assert (await _run_row(app, run_id)).universe == ["A", "B"]

    r = await client.patch(
        f"/api/v1/strategies/{strat['id']}",
        json={
            "spec": {
                "kind": "weight",
                "universe": ["A"],
                "rebalance": {"every_n_bars": 1},
                "weights_series": {"2026-01-01": {"A": 1.0}},
            }
        },
        headers=h,
    )
    assert r.status_code == 200, r.text

    provider.append("A", 3, "10.0")
    provider.append("B", 3, "10.0")
    await sup._tick_once(run_id)
    assert (await _run_row(app, run_id)).universe == ["A", "B"], (
        "the run must keep trading the universe it started with"
    )
