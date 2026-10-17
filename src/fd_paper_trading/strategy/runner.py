"""Live paper runner (design D5/D8, spec: strategy-live-paper).

Each PAPER run is one supervised asyncio task that polls for a new daily bar
(default 30 min), evaluates the strategy against the bar window, and places
its signals as orders through ``PaperExecutionService`` with ``strategy_run_id``
attribution. Parity with the backtester: both evaluate ``on_bar(ctx)`` over an
oldest-first bar window and consume ``ctx.signals``; the only difference is
how signals become fills (replay portfolio vs. live ledger + provider quote).

A weight-track run (spec: strategy-weight-mode) takes a sibling path: it is
actionable when its *primary* universe symbol has a new bar, it re-evaluates
every ``rebalance.every_n_bars`` processed bars, and a rebalance becomes one
attributed MARKET order per symbol through the same execution service - no
batch API, so the ledger, rate limits and order cap keep one implementation.
It reuses the weight backtester's target rules (step-function periods, contract
validation, integer-share rounding, halted symbols valued but not traded) so a
paper run cannot disagree with its own backtest about what a target means.

Lifecycle: ``STARTING -> RUNNING -> STOPPED | ERROR``. Stopping cancels the
run's resting LIMIT orders (never closes positions). An unexpected task death
marks the run ERROR; routine failures (data fetch, evaluation) log an event
and skip the tick so a single bad bar can't kill a live run.

The supervisor lives on ``app.state.strategy_runner``; the app lifespan calls
``recover()`` to resume active runs after a restart and ``shutdown()`` to
cancel tasks on exit.
"""

from __future__ import annotations

import asyncio
import math
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Mapping, Sequence

from ..execution import OrderRejected, PaperExecutionService
from ..marketdata import MarketDataProvider
from ..persistence.models import (
    ACTIVE_RUN_STATUSES,
    Strategy,
    StrategyEquityPoint,
    StrategyEventType,
    StrategyRun,
    StrategyRunMode,
    StrategyRunStatus,
    User,
    group_config,
)
from ..persistence.repositories import (
    AccountRepository,
    OrderRepository,
    PositionRepository,
    StrategyRunRepository,
    UserRepository,
)
from .backtester import BarEvaluator, EvaluationError, build_evaluator
from .ctx import StrategyContext, SymbolSeries, WeightContext
from .spec import WeightSpec, validate_spec
from .weight_backtester import (
    CASH_LABEL,
    Instruction,
    RebalanceSnapshot,
    WeightSource,
    build_series,
    build_weight_evaluator,
)
from .weights import validate_weights

UTC = timezone.utc


def _bar_date(bar) -> "datetime.date":
    return datetime.fromtimestamp(bar.ts, tz=UTC).date()


def _first_new_bar(bars: list, last_processed: str | None) -> int | None:
    """Index of the first bar strictly newer than ``last_processed`` (or None)."""
    for i, bar in enumerate(bars):
        if _bar_date(bar).isoformat() > last_processed:
            return i
    return None


def _day_start() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _latest_close(bars: Sequence) -> Decimal | None:
    """Most recent close of a bar list, or None when it never traded."""
    if not bars:
        return None
    return Decimal(str(bars[-1].close))


def _marks_for_weight(
    day: date, universe: Sequence[str], series: Mapping[str, SymbolSeries],
) -> tuple[dict[str, Decimal], list[str]]:
    """Latest close per symbol, plus the symbols whose close is stale on ``day``.

    Same rule as the weight backtester's: a symbol with no bar at all
    contributes no mark (there is no value to report), while one that traded
    before but not today is marked at that older close and reported as stale.
    """
    marks: dict[str, Decimal] = {}
    stale: list[str] = []
    for symbol in universe:
        found = series.get(symbol)
        if found is None:
            continue
        close = found.latest_close()
        if close is None:
            continue
        marks[symbol] = Decimal(str(close))
        if not found.has_bar_on(day):
            stale.append(symbol)
    return marks, stale


def _weight_weights(
    positions: Mapping[str, Decimal], marks: Mapping[str, Decimal],
    cash: Decimal, universe: Sequence[str] | None = None,
) -> dict[str, float]:
    """Portfolio weights after a rebalance, cash included under ``CASH``.

    Computed on the account's actual ledger state (not the intended one), so a
    rejected or capped order shows up as a deviation from the target instead of
    being assumed to have happened. Universe symbols are reported even at zero:
    a rotation that liquidated a name is only explicable if the record shows the
    name ending at 0.0 rather than vanishing from the mapping.
    """
    values = {
        sym: Decimal(qty) * marks[sym]
        for sym, qty in positions.items()
        if qty and sym in marks
    }
    equity = Decimal(cash) + sum(values.values(), Decimal(0))
    if equity <= 0:
        return {}
    out = {sym: 0.0 for sym in (universe or [])}
    out.update({sym: float(value / equity) for sym, value in values.items()})
    out[CASH_LABEL] = float(Decimal(cash) / equity)
    return out


def _weight_instructions(
    cash: Decimal,
    positions: Mapping[str, Decimal],
    marks: Mapping[str, Decimal],
    stale: Sequence[str],
    target: Mapping[str, float],
    day: date,
    buy_prices: Mapping[str, Decimal] | None = None,
    sell_prices: Mapping[str, Decimal] | None = None,
) -> list[Instruction]:
    """Target-vs-current share deltas as instructions, sized by the engine's rules.

    This mirrors the weight backtester's conversion (D3/D4) against the live
    ledger instead of an in-memory portfolio: equity at the latest closes,
    ``floor(|delta value| / mark)`` per symbol, a held symbol missing from the
    target treated as a target of zero (that is what makes a rotation
    expressible), and a halted symbol reported but never traded.

    The one place live must differ from the replay is the cash bound, and only
    because fills do not happen at the close here: a MARKET buy pays the ask and
    a sell receives the bid, so ``buy_prices``/``sell_prices`` carry those and
    the bound is computed on them. Sizing on the mark while the service charges
    the ask is exactly how a correct-looking rebalance ends with its last order
    refused for insufficient buying power; sizing on the ask makes the pre-size
    agree with the check the service will apply, and valuing a sell at the bid
    keeps the cash a rotation frees a conservative estimate.
    """
    buys_at = dict(buy_prices or {})
    sells_at = dict(sell_prices or {})
    values = {
        sym: Decimal(qty) * marks[sym]
        for sym, qty in positions.items()
        if qty and sym in marks
    }
    equity = Decimal(cash) + sum(values.values(), Decimal(0))
    if equity <= 0:
        return []

    # A target mapping states the whole intended portfolio: a held symbol that
    # is absent from it wants zero, not "leave it alone".
    weights = {str(k).upper(): float(v) for k, v in target.items()}
    for sym, qty in positions.items():
        if qty and sym not in weights:
            weights[sym] = 0.0

    instructions: list[Instruction] = []
    buys: list[tuple[str, Decimal, Decimal, Decimal]] = []
    for sym in sorted(weights):
        mark = marks.get(sym)
        if mark is None or mark <= 0:
            instructions.append(
                Instruction(
                    symbol=sym, side="BUY", quantity=Decimal(0),
                    price=Decimal(0), requested_quantity=Decimal(0),
                    skipped=True,
                    skip_reason="no price available (symbol never traded)",
                )
            )
            continue
        target_value = equity * Decimal(str(weights[sym]))
        current_value = Decimal(positions.get(sym, 0)) * mark
        delta_value = target_value - current_value
        qty = Decimal(int(math.floor(abs(delta_value) / mark)))  # down (D3)
        if qty == 0:
            continue
        side = "BUY" if delta_value > 0 else "SELL"
        if sym in stale:
            # D4: valued at the last close, but not tradable today.
            instructions.append(
                Instruction(
                    symbol=sym, side=side, quantity=Decimal(0), price=mark,
                    requested_quantity=qty, skipped=True,
                    skip_reason=(
                        f"no bar on {day.isoformat()}; valued at the last close "
                        f"but not tradable (halted)"
                    ),
                )
            )
            continue
        if side == "SELL":
            held = Decimal(positions.get(sym, 0))
            if qty > held:
                # Sells are bounded by the ledger, and the reduction is recorded
                # rather than silently differing from the instruction.
                instructions.append(
                    Instruction(
                        symbol=sym, side=side, quantity=held, price=mark,
                        requested_quantity=qty, scaled=True,
                        scale_reason=(
                            f"position bound: reduced from {qty} to {held} shares"
                        ),
                    )
                )
            else:
                instructions.append(
                    Instruction(
                        symbol=sym, side=side, quantity=qty, price=mark,
                        requested_quantity=qty,
                    )
                )
        else:
            buys.append((sym, qty, mark, buys_at.get(sym, mark)))

    # Cash freed by this rebalance's own sells is spendable for its buys; the
    # buys are then sized down (with the reason) so the sequence can never
    # overdraw, which is the cash constraint the execution service would
    # otherwise enforce one rejected order at a time.
    spendable = Decimal(cash) + sum(
        (i.quantity * sells_at.get(i.symbol, i.price)
         for i in instructions if i.side == "SELL"),
        Decimal(0),
    )
    for sym, qty, mark, fill_price in buys:
        cost = qty * fill_price
        if cost <= spendable:
            instructions.append(
                Instruction(
                    symbol=sym, side="BUY", quantity=qty, price=mark,
                    requested_quantity=qty,
                )
            )
            spendable -= cost
            continue
        affordable = Decimal(int(math.floor(spendable / fill_price)))
        price_note = (
            "" if fill_price == mark
            else f" at {fill_price}/share, the price a MARKET buy pays"
        )
        instructions.append(
            Instruction(
                symbol=sym, side="BUY", quantity=affordable, price=mark,
                requested_quantity=qty, scaled=True,
                scale_reason=(
                    f"insufficient cash: reduced from {qty} to {affordable} shares"
                    + price_note
                ),
            )
        )
        spendable -= affordable * fill_price
    return instructions


class PaperRunSupervisor:
    """Owns the poll tasks for all live paper runs (single-digit per user)."""

    def __init__(self, app, poll_seconds: float | None = None) -> None:
        self.app = app
        # Tests set this directly (e.g. 0.01) to avoid the 30-min default.
        self.poll_seconds = (
            poll_seconds
            if poll_seconds is not None
            else app.state.settings.strategy_poll_seconds
        )
        self.lookback_bars = app.state.settings.strategy_lookback_bars
        self._tasks: dict[uuid.UUID, asyncio.Task] = {}
        self._stop_flags: dict[uuid.UUID, asyncio.Event] = {}
        # One evaluator per run for its whole lifetime: template state
        # (bar counters, previous cross values) must survive across ticks.
        self._evaluators: dict[uuid.UUID, BarEvaluator] = {}
        # Weight-track code sources: one ``target_weights`` evaluator per run,
        # built once so a sandbox spec is not re-parsed on every rebalance.
        self._weight_evaluators: dict[uuid.UUID, object] = {}

    # ---- management (called by routes / lifespan) ----

    def is_running(self, run_id: uuid.UUID) -> bool:
        task = self._tasks.get(run_id)
        return task is not None and not task.done()

    async def start(self, run_id: uuid.UUID) -> None:
        """Spawn the poll task (idempotent)."""
        if self.is_running(run_id):
            return
        flag = asyncio.Event()
        self._stop_flags[run_id] = flag
        self._tasks[run_id] = asyncio.create_task(
            self._run_loop(run_id, flag), name=f"paper-run-{run_id}"
        )

    async def stop(self, run_id: uuid.UUID) -> None:
        """Request a graceful stop; the loop finalizes STOPPED + cancels LIMITs."""
        task = self._tasks.get(run_id)
        if task is None:
            # No in-memory task (e.g. after a restart without recovery): the
            # run row may still be active - finalize directly.
            await self._finalize_stop(run_id)
            return
        self._stop_flags[run_id].set()
        await task

    async def recover(self) -> None:
        """Resume every active PAPER run on startup (design D8 recovery)."""
        async with self.app.state.db.session_factory() as session:
            runs = await StrategyRunRepository(session).list_active(
                mode=StrategyRunMode.PAPER
            )
        for run in runs:
            await self.start(run.id)

    async def shutdown(self) -> None:
        """Cancel all tasks without changing run status (recover resumes them)."""
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        self._tasks.clear()
        self._stop_flags.clear()

    # ---- poll loop ----

    async def _run_loop(self, run_id: uuid.UUID, stop: asyncio.Event) -> None:
        try:
            await self._mark_running(run_id)
            while not stop.is_set():
                await self._tick(run_id)
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)
                except asyncio.TimeoutError:
                    pass
            if stop.is_set():
                await self._finalize_stop(run_id)
        except asyncio.CancelledError:  # shutdown - leave status for recovery
            raise
        except Exception as exc:  # unexpected death -> ERROR
            await self._finalize_error(run_id, exc)
        finally:
            self._tasks.pop(run_id, None)
            self._stop_flags.pop(run_id, None)
            self._evaluators.pop(run_id, None)
            self._weight_evaluators.pop(run_id, None)

    # ---- lifecycle transitions ----

    async def _mark_running(self, run_id: uuid.UUID) -> None:
        async with self.app.state.db.session_factory() as session:
            run = await session.get(StrategyRun, run_id)
            if run is None or run.status not in ACTIVE_RUN_STATUSES:
                return
            if run.status == StrategyRunStatus.STARTING:
                run.status = StrategyRunStatus.RUNNING
                run.started_at = run.started_at or datetime.now(UTC)
                await self._event(
                    session, run, await self._user_id(session, run),
                    StrategyEventType.STATE_CHANGE, "run started",
                    {"status": run.status},
                )
                await session.commit()

    async def _finalize_stop(self, run_id: uuid.UUID) -> None:
        async with self.app.state.db.session_factory() as session:
            run = await session.get(StrategyRun, run_id)
            if run is None or run.status not in ACTIVE_RUN_STATUSES:
                return
            # Cancel the run's resting LIMIT orders (never closes positions).
            orders = await OrderRepository(session).list_active_for_run(run_id)
            service: PaperExecutionService = self.app.state.execution_service
            for order in orders:
                try:
                    await service.cancel_order(session, order)
                except OrderRejected:
                    pass  # already terminal; nothing to cancel
            run.status = StrategyRunStatus.STOPPED
            run.stopped_at = datetime.now(UTC)
            await self._event(
                session, run, await self._user_id(session, run),
                StrategyEventType.STATE_CHANGE, "run stopped",
                {"status": run.status, "cancelled_orders": len(orders)},
            )
            await session.commit()

    async def _finalize_error(self, run_id: uuid.UUID, exc: Exception) -> None:
        async with self.app.state.db.session_factory() as session:
            run = await session.get(StrategyRun, run_id)
            if run is None or run.status not in ACTIVE_RUN_STATUSES:
                return
            run.status = StrategyRunStatus.ERROR
            run.stopped_at = datetime.now(UTC)
            run.error = str(exc)
            await self._event(
                session, run, await self._user_id(session, run),
                StrategyEventType.ERROR, "run crashed", {"error": str(exc)},
            )
            await session.commit()

    @staticmethod
    async def _user_id(session, run: StrategyRun) -> uuid.UUID:
        strategy = await session.get(Strategy, run.strategy_id)
        return strategy.user_id if strategy is not None else uuid.uuid4()

    # ---- one poll ----

    async def _tick(self, run_id: uuid.UUID) -> None:
        # Routine failures (bar fetch, evaluation) are caught inside _tick_once
        # and logged; anything escaping here is a run bug -> ERROR via the loop.
        await self._tick_once(run_id)

    async def _tick_once(self, run_id: uuid.UUID) -> None:
        """Fetch bars, detect a new bar, evaluate, place signals, snapshot.

        The evaluator is stateful and cached per run. On the first tick it is
        primed over the history (signals discarded) so its state matches a
        backtest that had been running the same window; only the latest bar
        trades. Later ticks evaluate each bar strictly newer than
        ``last_bar_date`` (normally one) and place their signals, mirroring
        backtest semantics across data gaps.

        A weight spec is dispatched to ``_tick_weight`` before any of this:
        its contract is a target mapping per rebalance point, not signals per
        bar, so it needs its own actionability rule and order conversion.
        """
        async with self.app.state.db.session_factory() as session:
            run = await session.get(StrategyRun, run_id)
            if run is None or run.status not in ACTIVE_RUN_STATUSES:
                return
            strategy = await session.get(Strategy, run.strategy_id)
            if strategy is None:
                raise EvaluationError(f"strategy {run.strategy_id} was deleted")
            user = await UserRepository(session).get_by_id(strategy.user_id)
            spec = validate_spec(strategy.spec)
            provider: MarketDataProvider = self.app.state.market_data_provider

            if isinstance(spec, WeightSpec):
                await self._tick_weight(session, run, strategy, user, spec, provider)
                return

            try:
                bars = await provider.get_bars(spec.symbol, "1d", self.lookback_bars)
            except Exception as exc:
                # Data gap: no new bar was observed - skip, do not advance.
                await self._event(session, run, strategy.user_id,
                                  StrategyEventType.ERROR, f"bar fetch failed: {exc}")
                await session.commit()
                return
            if not bars:
                await self._event(session, run, strategy.user_id,
                                  StrategyEventType.ERROR,
                                  f"no bar data for {spec.symbol}")
                await session.commit()
                return

            latest_date = _bar_date(bars[-1])
            last_processed = (run.params or {}).get("last_bar_date")
            if last_processed == latest_date.isoformat():
                return  # no new bar - nothing to do this poll

            account = await AccountRepository(session).get_for_user(
                run.account_id, strategy.user_id, for_update=True, eager=False
            )
            if account is None:
                raise EvaluationError(f"account {run.account_id} was deleted")

            instrument = provider.get_instrument(spec.symbol)
            instrument_id = (
                instrument.symbol_id if instrument is not None else f"{spec.symbol}.SIM"
            )
            position = await PositionRepository(session).get(account.id, instrument_id)
            position_qty = position.quantity if position is not None else Decimal("0")

            evaluator = self._evaluators.get(run_id)
            if evaluator is None:
                evaluator = build_evaluator(spec)
                self._evaluators[run_id] = evaluator
                # Prime over all history but the latest bar; signals discarded.
                for i in range(len(bars) - 1):
                    prime_ctx = StrategyContext(
                        bars[: i + 1], position=position_qty, cash=account.cash_balance
                    )
                    await evaluator.evaluate(prime_ctx)
                start_idx = len(bars) - 1  # first live bar trades this tick
            else:
                start_idx = _first_new_bar(bars, last_processed)
                if start_idx is None:
                    return  # window does not yet reach a bar newer than processed

            placed = await OrderRepository(session).count_for_run(run.id, _day_start())
            evaluated_any = False
            for i in range(start_idx, len(bars)):
                bar = bars[i]
                ctx = StrategyContext(
                    bars[: i + 1], position=position_qty, cash=account.cash_balance
                )
                try:
                    await evaluator.evaluate(ctx)
                except Exception as exc:
                    # Bar skipped (design D6): log it; keep going with later bars.
                    await self._event(session, run, strategy.user_id,
                                      StrategyEventType.ERROR,
                                      f"evaluation failed on {_bar_date(bar)}: {exc}")
                    continue
                evaluated_any = True
                placed = await self._place_signals(
                    session, run, strategy, spec.symbol, account, ctx,
                    user, placed,
                )
            if not evaluated_any:
                # Every new bar failed to evaluate; still advance so we don't
                # retry the same bars forever.
                run.params = {**(run.params or {}),
                              "last_bar_date": latest_date.isoformat()}
                await session.commit()
                return

            await self._snapshot_equity(session, run, provider, account, latest_date)
            run.params = {**(run.params or {}),
                          "last_bar_date": latest_date.isoformat()}
            await session.commit()

    async def _place_signals(
        self, session, run: StrategyRun, strategy: Strategy, symbol: str,
        account, ctx: StrategyContext, user: User, placed: int,
    ) -> int:
        """Turn ``ctx.signals`` into orders; enforce the daily order cap.

        ``placed`` is the run's order count today so far (carried across the
        bars evaluated in one tick); returns the updated count.
        """
        service: PaperExecutionService = self.app.state.execution_service
        max_per_day = group_config(user, "max_orders_per_day")
        for signal in ctx.signals:
            if placed >= max_per_day:
                await self._event(
                    session, run, strategy.user_id, StrategyEventType.REJECTION,
                    f"signal skipped: daily order cap {max_per_day} reached",
                    {"side": signal.side, "quantity": str(signal.quantity)},
                )
                continue
            try:
                order = await service.place_order(
                    session, account, symbol, signal.side,
                    "LIMIT" if signal.limit_price is not None else "MARKET",
                    signal.quantity, signal.limit_price,
                    strategy_run_id=run.id,
                )
            except OrderRejected as exc:
                await self._event(
                    session, run, strategy.user_id, StrategyEventType.REJECTION,
                    f"signal rejected: {exc.message}",
                    {"code": exc.code, "side": signal.side,
                     "quantity": str(signal.quantity)},
                )
                continue
            placed += 1
            await self._event(
                session, run, strategy.user_id, StrategyEventType.ORDER,
                f"placed {signal.side} {signal.quantity} {symbol}",
                {"order_id": str(order.id), "status": order.status,
                 "order_type": order.order_type},
            )
        return placed

    # ---- weight track (spec: strategy-weight-mode) ----

    async def _tick_weight(
        self, session, run: StrategyRun, strategy: Strategy, user: User,
        spec: WeightSpec, provider: MarketDataProvider,
    ) -> None:
        """One weight-run poll: detect a new primary bar, rebalance if due.

        Actionability follows the spec's wording: the primary (first) universe
        symbol is the run's clock, so a date on which only a peer traded does
        not advance the run - the cadence must not depend on which symbol
        happened to have data first. Once a newer primary bar exists, every
        date the tick reaches is processed in order (a poll that missed days
        replays them rather than swallowing them), which is what makes "every N
        processed bars" a property of the run and not of the poll interval.

        The first tick processes only the latest bar, exactly as the
        single-symbol path primes over history and trades just the final one: a
        live run starts now, and firing a burst of orders for bars that closed
        before it existed would be a fabrication rather than a replay.
        """
        universe = list(run.universe or spec.universe)
        if not universe:
            await self._event(session, run, strategy.user_id,
                              StrategyEventType.ERROR,
                              "weight run has an empty universe")
            await session.commit()
            return

        bars_by_symbol: dict[str, list] = {}
        for symbol in universe:
            try:
                bars_by_symbol[symbol] = await provider.get_bars(
                    symbol, "1d", self.lookback_bars
                )
            except Exception as exc:
                # A missing series means no new bar was observed: skip the tick
                # and do not advance, so the next poll retries the same date.
                await self._event(session, run, strategy.user_id,
                                  StrategyEventType.ERROR,
                                  f"bar fetch failed for {symbol}: {exc}")
                await session.commit()
                return

        primary_bars = bars_by_symbol[universe[0]]
        if not primary_bars:
            await self._event(session, run, strategy.user_id,
                              StrategyEventType.ERROR,
                              f"no bar data for {universe[0]}")
            await session.commit()
            return

        latest_date = _bar_date(primary_bars[-1])
        last_processed = (run.params or {}).get("last_bar_date")
        if last_processed == latest_date.isoformat():
            return  # no new bar for the primary symbol - nothing to do

        account = await AccountRepository(session).get_for_user(
            run.account_id, strategy.user_id, for_update=True, eager=False
        )
        if account is None:
            raise EvaluationError(f"account {run.account_id} was deleted")

        # The primary symbol's series is the decision axis, so a peer's stray
        # future-dated bar cannot pull the run forward.
        axis = sorted({_bar_date(b) for b in primary_bars})
        if last_processed is None:
            pending = [latest_date]  # first tick: history is context, not decisions
        else:
            pending = [d for d in axis if d.isoformat() > last_processed]
        if not pending:
            return

        processed = int((run.params or {}).get("bars_processed", 0))
        every = spec.rebalance.every_n_bars
        snapshots = list(run.weight_snapshots or [])
        gaps = list(run.data_gaps or [])
        placed = await OrderRepository(session).count_for_run(run.id, _day_start())

        for day in pending:
            processed += 1
            # The cadence counts processed bars for this run (spec wording), so
            # the Nth one rebalances; a tick that reaches several bars advances
            # the count once per bar, not once per poll.
            if processed % every == 0:
                placed, snapshot, day_gaps = await self._rebalance(
                    session, run, strategy, user, spec, provider, account,
                    universe, bars_by_symbol, day, placed,
                )
                snapshots = [*snapshots, snapshot.to_dict()]
                gaps.extend(day_gaps)
            # Every processed bar gets its point, rebalance or not (the spec's
            # non-rebalance scenario), valued at that day's closes.
            await self._snapshot_weight_equity(
                session, run, provider, account, universe, bars_by_symbol, day
            )

        run.weight_snapshots = snapshots
        run.data_gaps = gaps
        # The universe is pinned onto the run so a later spec edit cannot
        # rewrite what this run traded (design D7).
        run.universe = universe
        run.params = {
            **(run.params or {}),
            "last_bar_date": latest_date.isoformat(),
            "bars_processed": processed,
        }
        await session.commit()

    async def _rebalance(
        self, session, run: StrategyRun, strategy: Strategy, user: User,
        spec: WeightSpec, provider: MarketDataProvider, account,
        universe: Sequence[str], bars_by_symbol: Mapping[str, list],
        day: date, placed: int,
    ) -> tuple[int, RebalanceSnapshot, list[dict]]:
        """Convert one rebalance point's target weights into attributed orders.

        The sizing rules are the weight backtester's, reached through its own
        entry points (``build_series`` -> ``WeightContext`` -> ``WeightSource`` /
        the spec's evaluator, then the same per-symbol delta, integer rounding
        and cash bound), because a paper run that computed its own targets or
        its own rounding could disagree with the backtest of the same strategy.
        What differs here is only the last step: a share delta becomes one
        MARKET order through ``PaperExecutionService`` instead of an in-memory
        portfolio mutation, so the ledger, rate limit and daily cap keep one
        implementation (design D6: no batch API).
        """
        series = build_series(universe, bars_by_symbol, day)
        marks, stale = _marks_for_weight(day, universe, series)
        snapshot = RebalanceSnapshot(
            day=day, target_weights={}, actual_weights={},
            stale_symbols=list(stale),
        )

        positions = await self._weight_positions(session, provider, account, universe)
        ctx = WeightContext(
            day=day, universe=universe, series=series,
            cash=account.cash_balance, positions=positions,
        )

        evaluator = self._weight_evaluator(run.id, spec)
        try:
            if evaluator is not None:
                # A code source's evaluator spawns a sandbox subprocess; it is
                # synchronous, so it runs off the loop rather than stalling
                # every other run and request for the duration.
                target = dict(await asyncio.to_thread(evaluator, ctx))
                cash_floor = 0.0
            else:
                target, cash_floor, _ = WeightSource(spec, series).target(day, ctx, 0)
            target = {str(k).upper(): float(v) for k, v in target.items()}
            target_symbols = {k: v for k, v in target.items() if k != CASH_LABEL}
            # Same rule as the backtest: the contract judges the weights, and an
            # illegal target produces no orders rather than being rescaled.
            errors = validate_weights(universe, target_symbols, cash_floor=cash_floor)
            if errors:
                snapshot.error = "; ".join(errors)
                snapshot.actual_weights = _weight_weights(
                    positions, marks, account.cash_balance, universe
                )
                await self._event(
                    session, run, strategy.user_id, StrategyEventType.ERROR,
                    f"target weights refused on {day.isoformat()}: "
                    + "; ".join(errors),
                    {"date": day.isoformat()},
                )
                return placed, snapshot, []
            snapshot.target_weights = dict(target)
            snapshot.cash_floor = float(cash_floor)
        except Exception as exc:
            snapshot.error = f"evaluation failed: {exc}"
            snapshot.actual_weights = _weight_weights(
                positions, marks, account.cash_balance, universe
            )
            await self._event(
                session, run, strategy.user_id, StrategyEventType.ERROR,
                f"weight evaluation failed on {day.isoformat()}: {exc}",
            )
            return placed, snapshot, []

        buy_prices, sell_prices = await self._execution_prices(provider, universe, marks)
        instructions = _weight_instructions(
            account.cash_balance, positions, marks, stale, target_symbols, day,
            buy_prices, sell_prices,
        )
        placed = await self._place_weight_instructions(
            session, run, strategy, user, account, instructions, day, placed,
        )
        snapshot.instructions = instructions
        # Recorded after placement: the snapshot is the evidence of what the
        # engine actually holds, which is where rounding and scale-downs show.
        snapshot.actual_weights = await self._actual_weight_weights(
            session, provider, account, universe, marks
        )
        await self._event(
            session, run, strategy.user_id, StrategyEventType.REBALANCE,
            f"rebalance {day.isoformat()}: {len(target_symbols)} target symbol(s), "
            f"{sum(1 for i in instructions if not i.skipped and i.quantity > 0)} "
            f"order(s)",
            {"date": day.isoformat(), "universe": list(universe),
             "target_weights": dict(snapshot.target_weights),
             "cash_floor": snapshot.cash_floor,
             "instruction_count": len(instructions)},
        )
        return placed, snapshot, [{"date": day.isoformat(), "symbol": s} for s in stale]

    def _weight_evaluator(self, run_id: uuid.UUID, spec: WeightSpec):
        """The run's ``target_weights`` callable, or None for a static series."""
        if spec.weights_series is not None:
            return None
        evaluator = self._weight_evaluators.get(run_id)
        if evaluator is None:
            evaluator = build_weight_evaluator(spec)
            self._weight_evaluators[run_id] = evaluator
        return evaluator

    async def _weight_positions(
        self, session, provider: MarketDataProvider, account,
        universe: Sequence[str],
    ) -> dict[str, Decimal]:
        """The account's positions restricted to the universe, keyed by symbol."""
        positions: dict[str, Decimal] = {}
        for symbol in universe:
            instrument = provider.get_instrument(symbol)
            instrument_id = (
                instrument.symbol_id if instrument is not None else f"{symbol}.SIM"
            )
            position = await PositionRepository(session).get(account.id, instrument_id)
            if position is not None and position.quantity != 0:
                positions[symbol] = position.quantity
        return positions

    async def _actual_weight_weights(
        self, session, provider: MarketDataProvider, account,
        universe: Sequence[str], marks: Mapping[str, Decimal],
    ) -> dict[str, float]:
        """Portfolio weights after a rebalance, on the same marks as the target."""
        positions = await self._weight_positions(session, provider, account, universe)
        return _weight_weights(positions, marks, account.cash_balance, universe)

    @staticmethod
    async def _execution_prices(
        provider: MarketDataProvider, universe: Sequence[str],
        marks: Mapping[str, Decimal],
    ) -> tuple[dict[str, Decimal], dict[str, Decimal]]:
        """What each symbol's MARKET order would actually trade at: (buys, sells).

        Buys pay the ask and sells receive the bid, so the cash bound must be
        computed on those rather than on the close: sizing 100% of equity at the
        close leaves nothing for the spread, and the last order of a rebalance is
        then refused by the service's own buying-power check. A symbol with no
        live quote falls back to its mark, which makes the bound no worse than
        the mark-based one it replaces.
        """
        buy_prices: dict[str, Decimal] = {}
        sell_prices: dict[str, Decimal] = {}
        for symbol in universe:
            quote = None
            try:
                quote = await provider.get_quote(symbol)
            except Exception:
                quote = None
            mark = marks.get(symbol)
            if quote is not None:
                buy_prices[symbol] = quote.ask
                sell_prices[symbol] = quote.bid
            elif mark is not None:
                buy_prices[symbol] = mark
                sell_prices[symbol] = mark
        return buy_prices, sell_prices

    async def _place_weight_instructions(
        self, session, run: StrategyRun, strategy: Strategy, user: User,
        account, instructions: Sequence[Instruction], day: date, placed: int,
    ) -> int:
        """Place one MARKET order per executable instruction; enforce the cap.

        Quantities are already whole shares, so what is left to the service is
        exactly its own business: buying power for buys, the ledger's position
        for sells, and the attribution every other order goes through. A refused
        order is logged and the rest go on - a partial rebalance is a real
        outcome, not a failed one.

        The daily cap counts every order this rebalance produces, and the
        instructions it cannot fit are logged with the existing ``rejection``
        type and message shape (the single-symbol path's cap record), so a
        capped rebalance reads the same in the run's event log either way. A
        skipped-by-rule instruction (a halted symbol) is logged too: it never
        reaches the service, which is exactly the fact worth naming.
        """
        service: PaperExecutionService = self.app.state.execution_service
        max_per_day = group_config(user, "max_orders_per_day")
        for instruction in instructions:
            if instruction.skipped or instruction.quantity <= 0:
                await self._event(
                    session, run, strategy.user_id, StrategyEventType.SKIP,
                    f"{instruction.symbol} not traded on {day.isoformat()}: "
                    f"{instruction.skip_reason or 'no tradable quantity'}",
                    {"date": day.isoformat(), "symbol": instruction.symbol,
                     "side": instruction.side,
                     "requested_quantity": str(instruction.requested_quantity),
                     "reason": instruction.skip_reason},
                )
                continue
            if placed >= max_per_day:
                await self._event(
                    session, run, strategy.user_id, StrategyEventType.REJECTION,
                    f"signal skipped: daily order cap {max_per_day} reached",
                    {"date": day.isoformat(), "symbol": instruction.symbol,
                     "side": instruction.side,
                     "quantity": str(instruction.quantity),
                     "requested_quantity": str(instruction.requested_quantity)},
                )
                continue
            try:
                order = await service.place_order(
                    session, account, instruction.symbol, instruction.side,
                    "MARKET", instruction.quantity,
                    strategy_run_id=run.id,
                )
            except OrderRejected as exc:
                await self._event(
                    session, run, strategy.user_id, StrategyEventType.REJECTION,
                    f"instruction rejected: {exc.message}",
                    {"date": day.isoformat(), "code": exc.code,
                     "symbol": instruction.symbol, "side": instruction.side,
                     "quantity": str(instruction.quantity)},
                )
                continue
            placed += 1
            await self._event(
                session, run, strategy.user_id, StrategyEventType.ORDER,
                f"placed {instruction.side} {instruction.quantity} "
                f"{instruction.symbol}",
                {"date": day.isoformat(), "order_id": str(order.id),
                 "status": order.status, "order_type": order.order_type},
            )
        return placed

    async def _snapshot_weight_equity(
        self, session, run: StrategyRun, provider: MarketDataProvider, account,
        universe: Sequence[str], bars_by_symbol: Mapping[str, list], day: date,
    ) -> None:
        """Daily equity point: cash + positions marked at latest closes.

        Every processed bar gets one, rebalance or not (the spec keeps the curve
        continuous across non-rebalance ticks). A universe symbol is marked from
        the series already fetched - a halted one included, whose last close is
        the valuation rule while its non-tradability is the rebalance rule - and
        a position outside the universe falls back to its own latest close, then
        to book value, exactly as the single-symbol path does.
        """
        series = build_series(universe, bars_by_symbol, day)
        marks, _ = _marks_for_weight(day, universe, series)
        positions = await PositionRepository(session).list_for_account(
            account.id, account.user_id
        )
        marked = account.cash_balance
        for pos in positions:
            mark = marks.get(pos.symbol)
            if mark is None:
                mark = await self._latest_close_outside_universe(provider, pos.symbol)
            if mark is None:
                mark = pos.avg_price  # fallback: book value
            marked += pos.quantity * mark
        points = await StrategyRunRepository(session).list_equity_points(run.id)
        if day not in {p.date for p in points}:
            session.add(
                StrategyEquityPoint(run_id=run.id, date=day, equity=marked)
            )

    @staticmethod
    async def _latest_close_outside_universe(
        provider: MarketDataProvider, symbol: str
    ) -> Decimal | None:
        try:
            bars = await provider.get_bars(symbol, "1d", 1)
        except Exception:
            return None
        return _latest_close(bars or [])

    async def _snapshot_equity(
        self, session, run: StrategyRun, provider: MarketDataProvider,
        account, bar_date,
    ) -> None:
        """Daily equity point: cash + all positions marked at latest close."""
        cash = account.cash_balance
        positions = await PositionRepository(session).list_for_account(
            account.id, account.user_id
        )
        marked = cash
        for pos in positions:
            mark = None
            try:
                bars = await provider.get_bars(pos.symbol, "1d", 1)
                if bars:
                    mark = bars[-1].close
            except Exception:
                mark = None
            if mark is None:
                mark = pos.avg_price  # fallback: book value
            marked += pos.quantity * mark
        points = await StrategyRunRepository(session).list_equity_points(run.id)
        if bar_date not in {p.date for p in points}:
            session.add(
                StrategyEquityPoint(run_id=run.id, date=bar_date, equity=marked)
            )

    # ---- helpers ----

    async def _event(
        self, session, run: StrategyRun, user_id: uuid.UUID,
        type: str, message: str, data: dict | None = None,
    ) -> None:
        await StrategyRunRepository(session).add_event(run.id, type, message, data)
        manager = getattr(self.app.state, "realtime", None)
        if manager is not None:
            await manager.broadcast_to_user(
                user_id,
                {"type": "strategy_event",
                 "event": {"run_id": str(run.id), "strategy_id": str(run.strategy_id),
                           "type": type, "message": message, "data": data or {}}},
            )
