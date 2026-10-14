"""Paper order execution: SIM fill calculation + Postgres ledger.

The ``PaperExecutionService`` is the bridge between the API and the ledger:
- It asks the market-data provider for a quote and instrument.
- It uses ``SimFillEngine`` (pure-Python arithmetic against the single quote)
  to compute fills.
- It applies fills to the PostgreSQL ledger (positions, balances, fill events).

Account isolation lives in Postgres (per ``account_id``); concurrent orders on
the same account are serialized by the caller holding a ``FOR UPDATE`` row lock
on the account, so read-modify-write on cash/positions cannot race.
"""

import time
import uuid
from collections import OrderedDict
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from ..marketdata import MarketDataProvider
from ..persistence.models import (
    Account,
    BalanceTransaction,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)
from ..persistence.repositories import PositionRepository
from .fill_engine import FillResult, SimFillEngine


class OrderRejected(Exception):
    """Raised when an order cannot be accepted/filled for a business reason."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class _TokenBucket:
    __slots__ = ("rate", "burst", "tokens", "ts")

    def __init__(self, rate: float, burst: float) -> None:
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.ts = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        self.tokens = min(self.burst, self.tokens + (now - self.ts) * self.rate)
        self.ts = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class RateLimiter:
    """Per-account token buckets, LRU-bounded.

    ponytail: in-memory only; swap for Redis if the app ever runs multiple
    workers (in-process limits would not be shared).
    """

    def __init__(self, rate: float, burst: float, max_accounts: int = 10_000) -> None:
        self.rate = rate
        self.burst = burst
        self.max_accounts = max_accounts
        self._buckets: OrderedDict[uuid.UUID, _TokenBucket] = OrderedDict()

    def allow(self, account_id: uuid.UUID) -> bool:
        bucket = self._buckets.get(account_id)
        if bucket is None:
            if len(self._buckets) >= self.max_accounts:
                self._buckets.popitem(last=False)
            bucket = _TokenBucket(self.rate, self.burst)
            self._buckets[account_id] = bucket
        self._buckets.move_to_end(account_id)
        return bucket.allow()


class PaperExecutionService:
    def __init__(
        self,
        provider: MarketDataProvider,
        fill_engine: SimFillEngine | None = None,
        rate_limit_per_second: float = 0,
        rate_limit_burst: float = 20,
    ) -> None:
        self.provider = provider
        self.fill_engine = fill_engine or SimFillEngine()
        self.rate_limiter = (
            RateLimiter(rate_limit_per_second, rate_limit_burst)
            if rate_limit_per_second > 0
            else None
        )

    async def place_order(
        self,
        session: AsyncSession,
        account: Account,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Decimal,
        limit_price: Decimal | None = None,
        client_order_id: str | None = None,
        strategy_run_id: uuid.UUID | None = None,
    ) -> Order:
        if self.rate_limiter is not None and not self.rate_limiter.allow(account.id):
            raise OrderRejected("rate_limited", "Order rate limit exceeded")

        instrument = self.provider.get_instrument(symbol)
        if instrument is None:
            raise OrderRejected("unknown_instrument", f"No instrument for symbol '{symbol}'")

        quote = await self.provider.get_quote(symbol)
        if quote is None:
            raise OrderRejected("no_quote", f"No live quote for '{symbol}'")

        # Simple buying-power check for buys (paper trading, no margin on cash).
        # A market or crossing-limit buy fills at the ask; a resting limit buy
        # reserves buying power at its limit price.
        if side == OrderSide.BUY:
            if (
                order_type == OrderType.LIMIT
                and limit_price is not None
                and limit_price < quote.ask
            ):
                ref_price = limit_price  # resting limit
            else:
                ref_price = quote.ask  # market or crossing limit
            if account.cash_balance < quantity * ref_price:
                raise OrderRejected(
                    "insufficient_buying_power", "Not enough cash for this order"
                )

        instrument_id = str(instrument.id)  # e.g. "AAPL.SIM"
        order = Order(
            account_id=account.id,
            user_id=account.user_id,
            client_order_id=client_order_id,
            strategy_run_id=strategy_run_id,
            instrument_id=instrument_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            limit_price=limit_price,
            status=OrderStatus.PENDING,
        )
        session.add(order)
        await session.flush()

        result = self.fill_engine.compute_fill(quote, side, order_type, quantity, limit_price)

        if result.fills:
            for price, qty in result.fills:
                await self._apply_fill(
                    session, account, order, instrument_id, symbol, side, qty, price
                )
            order.filled_quantity = result.filled_quantity
            order.avg_fill_price = result.avg_fill_price
            order.status = (
                OrderStatus.FILLED
                if result.filled_quantity >= quantity
                else OrderStatus.PARTIALLY_FILLED
            )
        elif result.status == "OPEN":
            order.status = OrderStatus.OPEN  # resting limit order
        else:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "execution engine rejected order"

        await session.flush()
        return order

    async def cancel_order(self, session: AsyncSession, order: Order) -> Order:
        if order.status not in (
            OrderStatus.PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.OPEN,
            OrderStatus.PARTIALLY_FILLED,
        ):
            raise OrderRejected(
                "not_cancellable", f"Order in status {order.status} cannot be cancelled"
            )
        order.status = OrderStatus.CANCELLED
        await session.flush()
        return order

    async def _apply_fill(
        self,
        session: AsyncSession,
        account: Account,
        order: Order,
        instrument_id: str,
        symbol: str,
        side: str,
        qty: Decimal,
        price: Decimal,
    ) -> None:
        position = await PositionRepository(session).get(account.id, instrument_id)
        if position is None:
            position = Position(
                account_id=account.id,
                instrument_id=instrument_id,
                symbol=symbol,
                quantity=Decimal("0"),
                avg_price=Decimal("0"),
                realized_pnl=Decimal("0"),
            )
            session.add(position)
            await session.flush()

        realized = self._update_position(position, side, qty, price)

        signed_qty = qty if side == OrderSide.BUY else -qty
        cash_delta = -signed_qty * price
        account.cash_balance += cash_delta

        session.add(
            Fill(
                order_id=order.id,
                account_id=account.id,
                instrument_id=instrument_id,
                side=side,
                quantity=qty,
                price=price,
            )
        )
        session.add(
            BalanceTransaction(
                account_id=account.id,
                type="trade",
                amount=cash_delta,
                balance_after=account.cash_balance,
                reference=str(order.id),
            )
        )
        await session.flush()

    @staticmethod
    def _update_position(
        position: Position, side: str, qty: Decimal, price: Decimal
    ) -> Decimal:
        """Apply a fill to a signed-quantity position; return realized PnL."""
        signed = qty if side == OrderSide.BUY else -qty
        old_qty = position.quantity
        old_avg = position.avg_price

        if old_qty == 0:
            position.quantity = signed
            position.avg_price = price
            return Decimal("0")

        same_direction = (old_qty > 0) == (signed > 0)
        if same_direction:
            new_qty = old_qty + signed
            position.avg_price = (abs(old_qty) * old_avg + qty * price) / abs(new_qty)
            position.quantity = new_qty
            return Decimal("0")

        # Reducing / closing / flipping.
        closing = min(qty, abs(old_qty))
        realized = (price - old_avg) * closing * (1 if old_qty > 0 else -1)
        new_qty = old_qty + signed
        if abs(signed) > abs(old_qty):
            position.avg_price = price  # flipped to the other side
        position.quantity = new_qty
        position.realized_pnl += realized
        return realized
