"""Give resting stop orders the "next quote arrival" that a manual account never has.

Where a stop's trigger comes from
---------------------------------
The stop-order design (change `2026-11-06-stop-order-execution`, D1) hangs the
trigger on "a new quote arrives", and the quote cadence is the daily close — no
intraday pretence, because this service's price *is* the last daily close ± a
5 bps half-spread. That design enumerated what counts as an arrival as "runner
tick / manual order / run evaluation". Two of the three are things the user or a
strategy does; nothing in the process ticks on its own.

For an account traded by hand that leaves a hole with no wake-up at all: a resting
stop is evaluated once when it is placed (`api/routes/orders.py`) and afterwards
only if the owner happens to place *another* order on the same symbol. The close
can fall through the stop price and the order still rests — the one thing a stop
order exists to prevent, on exactly the account type the feature was built for
("账户层 STOP 单给手动交易用", same design D3).

This sweeper supplies the missing tick. Every ``interval_seconds`` it finds the
accounts holding a resting STOP/STOP_LIMIT order and drives them through the same
``PaperExecutionService.evaluate_open_stops`` path, so no fill, buying-power or
staleness semantics are duplicated here — the sweep only decides *when* to ask.

Why the realtime quote tick is not the place for it: ``_emit_ticks`` applies a
deliberate wobble to make the stream look alive. Triggering a stop off a synthetic
±0.1% move would fill orders on a price nobody quoted. The sweeper reads the real
provider instead, so a sweep can only ever act on the same close a placement would.

Accounts with an active strategy run are skipped: the runner evaluates their stops
itself and records an ORDER event in the run's log, so taking them here would make
a fill appear in the ledger with nothing in the run log explaining it.
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from ..api.schemas import OrderOut
from ..persistence.models import (
    ACTIVE_RUN_STATUSES,
    Account,
    Order,
    OrderStatus,
    OrderType,
    StrategyRun,
)
from ..persistence.repositories import OrderRepository

logger = logging.getLogger(__name__)

_RESTING_STOP_TYPES = (OrderType.STOP, OrderType.STOP_LIMIT)


class StopSweeper:
    """Periodically re-evaluate resting stop orders across all accounts."""

    def __init__(self, app, interval_seconds: float = 60.0) -> None:
        self.app = app
        self.interval_seconds = interval_seconds
        self._task: asyncio.Task | None = None

    # ---- lifecycle ----

    async def start(self) -> None:
        if self.interval_seconds <= 0:
            return  # 0 disables the sweep (same convention as the other knobs)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.interval_seconds)
            try:
                await self.sweep_once()
            except Exception:  # noqa: BLE001 - one bad sweep must not kill the loop
                logger.exception("stop sweep failed")

    # ---- one pass ----

    async def accounts_with_resting_stops(self) -> list:
        """Account ids holding a resting stop that no active run is driving."""
        db = self.app.state.db
        async with db.session_factory() as session:
            stmt = (
                select(Order.account_id)
                .where(
                    Order.status == OrderStatus.OPEN,
                    Order.order_type.in_(_RESTING_STOP_TYPES),
                    ~Order.account_id.in_(
                        select(StrategyRun.account_id).where(
                            StrategyRun.status.in_(ACTIVE_RUN_STATUSES)
                        )
                    ),
                )
                .distinct()
            )
            return list((await session.execute(stmt)).scalars())

    async def sweep_once(self) -> list[Order]:
        """One pass. Returns the orders this pass triggered (for tests/observability)."""
        db = self.app.state.db
        service = self.app.state.execution_service
        manager = getattr(self.app.state, "realtime", None)
        triggered_all: list[Order] = []

        for account_id in await self.accounts_with_resting_stops():
            async with db.session_factory() as session:
                # FOR UPDATE serializes with a concurrent placement on the same
                # account: both paths read-modify-write cash/positions.
                account = await session.get(Account, account_id, with_for_update=True)
                if account is None:
                    continue
                try:
                    triggered = await service.evaluate_open_stops(session, account)
                    await session.commit()
                except Exception:  # noqa: BLE001 - one account must not stop the pass
                    logger.exception("stop sweep failed for account %s", account_id)
                    await session.rollback()
                    continue

                for order in triggered:
                    triggered_all.append(order)
                    if manager is None:
                        continue
                    # Re-fetch so the payload carries the fills written by the
                    # trigger (added via FK, so the in-memory collection is empty).
                    fresh = await OrderRepository(session).get_for_user(
                        order.id, account.user_id
                    )
                    if fresh is None:
                        continue
                    await manager.broadcast_to_user(
                        account.user_id,
                        {"type": "order",
                         "order": OrderOut.model_validate(fresh).model_dump(mode="json")},
                    )
        return triggered_all
