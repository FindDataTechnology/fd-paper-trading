"""Live paper runner (design D5/D8, spec: strategy-live-paper).

Each PAPER run is one supervised asyncio task that polls for a new daily bar
(default 30 min), evaluates the strategy against the bar window, and places
its signals as orders through ``PaperExecutionService`` with ``strategy_run_id``
attribution. Parity with the backtester: both evaluate ``on_bar(ctx)`` over an
oldest-first bar window and consume ``ctx.signals``; the only difference is
how signals become fills (replay portfolio vs. live ledger + provider quote).

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
import uuid
from datetime import datetime, timezone
from decimal import Decimal

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
from .ctx import StrategyContext
from .spec import validate_spec

UTC = timezone.utc


def _bar_date(bar) -> "datetime.date":
    return datetime.fromtimestamp(bar.ts, tz=UTC).date()


def _first_new_bar(bars: list, last_processed: str | None) -> int | None:
    """Index of the first bar strictly newer than ``last_processed`` (or None)."""
    for i, bar in enumerate(bars):
        if _bar_date(bar).isoformat() > last_processed:
            return i
    return None


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

            day_start = datetime.now(UTC).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            placed = await OrderRepository(session).count_for_run(run.id, day_start)
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
