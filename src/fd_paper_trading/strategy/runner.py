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
import inspect
import math
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Mapping, Sequence

from sqlalchemy import or_, update

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
from ..valuation import marked_equity
from .backtester import BarEvaluator, EvaluationError, build_evaluator
from .ctx import StrategyContext, SymbolSeries, WeightContext
from .risk_overlay import (
    CooldownState,
    RiskConfig,
    effective_target_weight,
    evaluate_quote_overlay,
)
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

#: 判据探针窗宽度（天）。以**源水位**为右端取最近这么多天——正常宽度，
#: 足够覆盖一个长假与一周的滞后。
CALIBER_PROBE_DAYS = 35
#: 问不到源水位时的兜底窗。这是卸载 400 天盲窗前的行为，逐位保留：
#: 一个问不出水位的 provider 不该因此丢掉判据（那会是"读不到"="不存在"）。
CALIBER_PROBE_FALLBACK_DAYS = 400


def caliber_probe_window(watermark: dict | None,
                         today: "datetime.date") -> tuple["datetime.date", "datetime.date"]:
    """把「源水位」翻译成判据探针的 ``(start, end)``。

    有水位：``end = as_of``，往回 ``CALIBER_PROBE_DAYS`` 天。
    没水位（``None`` / ``status != ok`` / ``as_of`` 解析不了 / 水位在未来）：
    ``end = today``，往回 ``CALIBER_PROBE_FALLBACK_DAYS`` 天。

    水位在**未来**也算不可用：那是时钟或上游写错了，拿它当右端会取到一个
    空窗口然后诚实地报"判据未知"——把配置错误伪装成数据缺失。
    """
    end = today
    days = CALIBER_PROBE_FALLBACK_DAYS
    if watermark and watermark.get("status") == "ok":
        raw = watermark.get("as_of")
        if raw:
            try:
                as_of = date.fromisoformat(str(raw)[:10])
            except ValueError:
                as_of = None
            if as_of is not None and as_of <= today:
                end = as_of
                days = CALIBER_PROBE_DAYS
    return end - timedelta(days=days), end



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
        # Identifies this process in the run lease. Two supervisors in one
        # process (tests, or a restart in place) still count as one writer.
        self.owner = uuid.uuid4().hex
        # Lease lifetime: long enough to cover a slow tick (bar fetch + evaluation
        # + orders), short enough that a crashed pod hands over within a poll or
        # two. Never allowed below a few polls, so a slow tick cannot lose it.
        self.lease_seconds = max(3 * self.poll_seconds, 60.0)

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
                # Spec (strategy-live-execution): live runs record the provider's
                # declared adjustment caliber at start, so "which price world
                # this run trades in" is data, not convention. Providers that
                # declare nothing leave the keys absent.
                coverage_fn = getattr(self.app.state.market_data_provider,
                                      "get_bars_with_coverage", None)
                # Weight runs carry the universe in params; template/code runs
                # carry a single symbol on the strategy spec — both trade a
                # price world that must be recorded (spec: strategy-live-
                # execution; gap found by the 2026-11-07 three-way check).
                first_symbol = (
                    (run.params or {}).get("universe") or [None]
                )[0]
                if first_symbol is None and coverage_fn is not None:
                    strategy = await session.get(Strategy, run.strategy_id)
                    if strategy is not None:
                        try:
                            spec = validate_spec(strategy.spec)
                            first_symbol = getattr(spec, "symbol", None)
                        except Exception:  # noqa: BLE001 - caliber is best-effort
                            first_symbol = None
                if coverage_fn is not None and first_symbol:
                    try:
                        # Probe a window anchored on the source's *watermark*, not
                        # on the wall clock: a provider whose feed lags (e.g.
                        # astock_daily to 2026-07-10) has no bar in a same-day
                        # window, and would report adjusted=unknown even though it
                        # declares a caliber. The registry now publishes that
                        # offset as a fact, so the window shrinks back to its
                        # normal width instead of blindly spanning 400 days
                        # (change1 task 6.5). Providers that cannot answer keep
                        # the old fixed window — asking is best-effort, never a
                        # precondition for recording a caliber.
                        watermark = None
                        watermark_fn = getattr(
                            self.app.state.market_data_provider,
                            "source_watermark", None)
                        if watermark_fn is not None:
                            try:
                                # 能力方法可能是 async（真 provider：要在**本循环**里用
                                # 已有的 asyncpg 连接池；丢进 to_thread 开新循环会得到
                                # "connection was closed in the middle of operation"），
                                # 也可能是 sync（测试替身/简单 provider）。两种都收：
                                # 判 coroutine 再 await，否则当值用——不靠 try/except 猜。
                                watermark = watermark_fn()
                                if inspect.isawaitable(watermark):
                                    watermark = await watermark
                            except Exception:  # noqa: BLE001 - 尽力而为
                                watermark = None
                        probe_start, probe_end = caliber_probe_window(
                            watermark, datetime.now(UTC).date())
                        cov = await coverage_fn(first_symbol, probe_start, probe_end)
                        if cov.get("adjusted"):
                            run.params = {
                                **(run.params or {}),
                                "price_caliber": cov["adjusted"],
                                "price_source": cov.get("source"),
                            }
                            if watermark:
                                # 水位落库：判据是"那一刻这个源的哪段数据"，
                                # 水位是这段数据的右端——不留痕就无法事后复现。
                                run.params = {
                                    **run.params,
                                    "price_as_of_max": watermark.get("as_of"),
                                    "price_watermark_status": watermark.get("status"),
                                }
                    except Exception:  # noqa: BLE001 - caliber is best-effort
                        pass
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
            run.runner_owner = None
            run.runner_lease_until = None
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
            run.runner_owner = None
            run.runner_lease_until = None
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
        # Single-writer gate. Two pods overlap during a rolling update; without
        # this both would evaluate the same bars and place the same orders, and
        # the "already processed" guard cannot help — it is written after the
        # orders go in. Not claimed = another process owns the run right now, so
        # this poll is a no-op (not an error: it is the expected case).
        if not await self._claim_lease(run_id):
            return
        # Routine failures (bar fetch, evaluation) are caught inside _tick_once
        # and logged; anything escaping here is a run bug -> ERROR via the loop.
        await self._tick_once(run_id)

    async def _claim_lease(self, run_id: uuid.UUID) -> bool:
        """Take or renew this process's lease on the run; False if someone else holds it.

        One conditional UPDATE, so it is atomic on both SQLite and Postgres and
        needs no advisory-lock branch: the row is updated only while the lease is
        free, expired, or already ours. ``rowcount`` is the arbiter.
        """
        until = datetime.now(UTC) + timedelta(seconds=self.lease_seconds)
        async with self.app.state.db.session_factory() as session:
            result = await session.execute(
                update(StrategyRun)
                .where(
                    StrategyRun.id == run_id,
                    or_(
                        StrategyRun.runner_owner.is_(None),
                        StrategyRun.runner_owner == self.owner,
                        StrategyRun.runner_lease_until.is_(None),
                        StrategyRun.runner_lease_until < datetime.now(UTC),
                    ),
                )
                .values(runner_owner=self.owner, runner_lease_until=until)
            )
            await session.commit()
            return result.rowcount == 1

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

        Every tick is also a "new quote arrival" for the run's account: the
        run's resting STOP/STOP_LIMIT orders are re-evaluated here (design:
        触发评估挂在报价到达时点). A store failure during stop evaluation is
        an event, not a tick failure — the strategy evaluation must not be
        blocked by the stop ledger.
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

            # New quote arrival for this tick: evaluate resting stops on the
            # run's account before (and independent of) strategy evaluation.
            service: PaperExecutionService = self.app.state.execution_service
            try:
                triggered = await service.evaluate_open_stops(session, account)
                await session.commit()
                for order in triggered:
                    await self._event(
                        session, run, strategy.user_id,
                        StrategyEventType.ORDER,
                        f"stop triggered: {order.symbol} {order.side} "
                        f"{order.filled_quantity} @ {order.avg_fill_price}",
                        {"order_id": str(order.id), "kind": "stop_triggered"},
                    )
                    await session.commit()
            except Exception as exc:  # noqa: BLE001 - stop ledger must not block the run
                await self._event(session, run, strategy.user_id,
                                  StrategyEventType.ERROR,
                                  f"stop evaluation failed: {exc}")
                await session.rollback()

            instrument = provider.get_instrument(spec.symbol)
            instrument_id = (
                instrument.symbol_id if instrument is not None else f"{spec.symbol}.SIM"
            )
            position = await PositionRepository(session).get(account.id, instrument_id)
            position_qty = position.quantity if position is not None else Decimal("0")

            # Run-level risk overlay (spec: shared by backtest and live). The
            # config lives on the run's params; per-bar state (peak, cooldown)
            # is carried in run.params too so a restarted run resumes honestly.
            risk_cfg = RiskConfig.from_dict((run.params or {}).get("risk"))
            risk_state = (run.params or {}).get("risk_state") or {}
            cooldown = CooldownState(remaining=dict(risk_state.get("cooldown", {})))
            peak_value = risk_state.get("peak_value")
            peak_value = Decimal(str(peak_value)) if peak_value is not None else None

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
                # Live-side overlay (same module the backtester uses — parity by
                # construction): a trigger injects a close-out MARKET instruction
                # ahead of the strategy's own signals for this bar.
                if risk_cfg.active and position_qty > 0:
                    trigger = evaluate_quote_overlay(
                        risk_cfg, spec.symbol, "SELL",
                        entry_price=position.avg_price if position is not None else None,
                        peak_value=peak_value,
                        quote_bid=bar.low,   # tick=bar replay: bar low stands in
                        quote_ask=bar.high,  # for the quote's two sides
                    )
                    if trigger is not None:
                        run.params = {**(run.params or {}), **{}}
                        risk_triggers = (run.params or {}).setdefault(
                            "risk_triggers", []
                        )
                        risk_triggers.append({
                            "date": _bar_date(bar).isoformat(),
                            "symbol": trigger.symbol,
                            "kind": trigger.kind,
                            "trigger_level": str(trigger.trigger_level),
                            "fill_price": str(trigger.fill_price),
                            "detail": trigger.detail,
                        })
                        cooldown.arm(spec.symbol, risk_cfg.cooldown_bars)
                        from .ctx import Signal  # engine-level import guard

                        ctx.signals.insert(0, Signal(
                            side="SELL", quantity=abs(position_qty),
                        ))
                if peak_value is not None or risk_cfg.active:
                    mark = bar.close
                    peak_value = mark if peak_value is None else max(peak_value, mark)
                cooldown.tick()
                placed = await self._place_signals(
                    session, run, strategy, spec.symbol, account, ctx,
                    user, placed,
                )
                # The bar's orders moved the position (and cash). Re-read it so
                # the risk overlay and the next catch-up bar see the ledger as
                # it now is, not the quantity read before the loop started —
                # otherwise several missed bars trade against a stale holding.
                position = await PositionRepository(session).get(
                    account.id, instrument_id
                )
                position_qty = (
                    position.quantity if position is not None else Decimal("0")
                )
                if risk_cfg.active:
                    run.params = {
                        **(run.params or {}),
                        "risk_state": {
                            "peak_value": str(peak_value) if peak_value is not None else None,
                            "cooldown": cooldown.remaining,
                        },
                    }
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


    async def _record_risk_verdict(self, session, run: StrategyRun, day: date,
                                   actual_weights: Mapping[str, float] | None) -> None:
        """组合级风控判定随 run 留痕（change4 3.2）。

        **只判定、不处置**：门按契约不产出任何"清仓/减仓"字段（change4 1.5），
        这里也不据此改写订单——触达之后要不要停手属于组合纪律，不是实现方的默认值。
        所以这一步的产物只有一个：`run.params["risk_events"]` 里多一条可复核的判定。

        没配阈值就什么都不做（`risk_thresholds` 缺席 → 键不出现），
        而不是记一条"ok"——"没判过"与"判过且通过"必须能分开。
        """
        thresholds = (run.params or {}).get("risk_thresholds")
        if not thresholds:
            return
        from .._vendor.fd_quant.risk import evaluate_portfolio_gate

        # 只判真实持仓：CASH 是现金占比，不是可风控的标的（把它当标的会让
        # "单票集中度"里多出一条永远无害的项）。
        weights = {sym: float(w) for sym, w in (actual_weights or {}).items()
                   if sym != "CASH"}
        if not weights:
            return
        points = await StrategyRunRepository(session).list_equity_points(run.id)
        equity_series = [(p.date.isoformat(), float(p.equity)) for p in points]
        try:
            verdict = evaluate_portfolio_gate(
                weights=weights,
                thresholds=thresholds,
                sectors=(run.params or {}).get("risk_sectors"),
                equity_series=equity_series or None,
                as_of=day.isoformat(),
                price_as_of_max=(run.params or {}).get("price_as_of_max"),
            )
        except Exception:  # noqa: BLE001 - 判定是留痕，绝不让交易 tick 失败
            logger.warning("risk verdict failed for run %s on %s", run.id, day,
                           exc_info=True)
            return
        events = list((run.params or {}).get("risk_events") or [])
        events.append({"day": day.isoformat(), "verdict": verdict})
        run.params = {**(run.params or {}), "risk_events": events}

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

        # Risk-overlay cooldown (spec: 止损当根卖、下个调仓点原样买回在构造上
        # 不可能). Applied to the EFFECTIVE weights before order conversion;
        # the snapshot records both so the deviation is attributable.
        risk_cfg = RiskConfig.from_dict((run.params or {}).get("risk"))
        risk_state = (run.params or {}).get("risk_state") or {}
        cooldown = CooldownState(remaining=dict(risk_state.get("cooldown", {})))
        effective_symbols = {
            k: effective_target_weight(k, Decimal(str(v)), cooldown)
            for k, v in target_symbols.items()
        }
        if effective_symbols != target_symbols:
            snapshot.effective_weights = {
                k: float(v) for k, v in effective_symbols.items()
            }
            target_symbols = {
                k: float(v) for k, v in effective_symbols.items()
            }

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
        await self._record_risk_verdict(
            session, run, day, snapshot.actual_weights
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
        marked = await marked_equity(session, account, provider)
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
