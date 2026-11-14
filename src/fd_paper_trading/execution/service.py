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
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from ..marketdata import MarketDataProvider, Quote
from ..persistence.models import (
    Account,
    BalanceTransaction,
    Fill,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    group_config,
)
from ..persistence.repositories import OrderRepository, PositionRepository, UserRepository
from .fill_engine import FillResult, SimFillEngine

# How old a quote may be and still be traded against. Daily-close-derived quotes
# (``fd_open_data``) stay valid through a weekend or a short holiday, so the
# bound is ~one trading day of calendar slack rather than 24h sharp. A quote
# whose source did not stamp ``ts_event`` (0 = unknown) is never treated as
# stale: only some providers stamp it, and rejecting on "unknown" would reject
# every simulated/derived quote.
MAX_QUOTE_AGE_SECONDS = 4 * 24 * 60 * 60


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
        max_quote_age_seconds: float = MAX_QUOTE_AGE_SECONDS,
    ) -> None:
        self.provider = provider
        self.fill_engine = fill_engine or SimFillEngine()
        self.max_quote_age_seconds = max_quote_age_seconds
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
        stop_price: Decimal | None = None,
    ) -> Order:
        # Idempotency: a replay of a submission already accepted under this key
        # returns the order that key produced. Nothing is re-fetched, re-priced
        # or re-applied, so a retry cannot move cash twice.
        if client_order_id is not None:
            existing = await OrderRepository(session).get_by_client_order_id(
                account.id, client_order_id
            )
            if existing is not None:
                return existing

        if self.rate_limiter is not None and not self.rate_limiter.allow(account.id):
            raise OrderRejected("rate_limited", "Order rate limit exceeded")

        # Group limits belong here, not in the strategy routes: the routes only
        # guard strategy creation, so a direct POST /orders used to bypass both
        # the symbol allowlist and the daily cap entirely — an operator who set
        # them in the admin panel believed they bound.
        await self._enforce_group_limits(session, account, symbol)

        instrument = self.provider.get_instrument(symbol)
        if instrument is None:
            raise OrderRejected("unknown_instrument", f"No instrument for symbol '{symbol}'")

        quote = await self.provider.get_quote(symbol)
        if quote is None:
            raise OrderRejected("no_quote", f"No live quote for '{symbol}'")
        self._require_fresh_quote(symbol, quote)

        # Stop-order validation (design: 疯狂组合在入口拒绝).
        if order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
            if stop_price is None:
                raise OrderRejected(
                    "invalid_stop", "stop_price is required for STOP/STOP_LIMIT orders"
                )
            if order_type == OrderType.STOP_LIMIT and limit_price is None:
                raise OrderRejected(
                    "invalid_stop", "limit_price is required for STOP_LIMIT orders"
                )
            # Sensible trigger/fill relationship (spec: a sell stop-limit with
            # limit_price > stop_price can never fill sensibly -> rejected).
            if order_type == OrderType.STOP_LIMIT:
                if side == OrderSide.SELL and limit_price > stop_price:
                    raise OrderRejected(
                        "invalid_stop",
                        "sell stop-limit requires limit_price <= stop_price",
                    )
                if side == OrderSide.BUY and limit_price < stop_price:
                    raise OrderRejected(
                        "invalid_stop",
                        "buy stop-limit requires limit_price >= stop_price",
                    )

        # Buying power is checked here and again at stop-trigger time (the cash
        # may have gone elsewhere between placement and trigger) — same helper,
        # so the two paths cannot drift.
        self._check_buying_power(
            account, side, order_type, quantity, limit_price, stop_price, quote
        )

        instrument_id = instrument.symbol_id  # e.g. "AAPL.SIM"
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
            stop_price=stop_price,
            status=OrderStatus.PENDING,
        )
        session.add(order)
        await session.flush()

        result = self.fill_engine.compute_fill(
            quote, side, order_type, quantity, limit_price, stop_price
        )

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
            if order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
                # A stop whose trigger condition already holds at placement
                # (quote gapped through the stop) fires immediately — recorded
                # as triggered, never as "resting then magic-filled".
                from datetime import datetime, timezone

                order.triggered_at = datetime.now(timezone.utc)
        elif result.status == "OPEN":
            order.status = OrderStatus.OPEN  # resting limit / untriggered stop
        else:
            order.status = OrderStatus.REJECTED
            order.reject_reason = "execution engine rejected order"

        await session.flush()
        return order

    async def _enforce_group_limits(
        self, session: AsyncSession, account: Account, symbol: str
    ) -> None:
        """Apply the user's group allowlist and daily order cap to one order.

        Both are read from the group the account's owner belongs to. A user with
        no group (or a group that sets neither) is unrestricted, which is the
        documented default: ``allowed_symbols=None`` means "no allowlist".

        The cap counts by account, not by run: an account has exactly one owner,
        so this is the per-user-per-day figure the admin panel promises. Runs
        are still free to place up to the cap between them.
        """
        user = await UserRepository(session).get_by_id(account.user_id)
        if user is None:
            return

        allowed = group_config(user, "allowed_symbols")
        if allowed is not None and symbol not in allowed:
            raise OrderRejected(
                "symbol_not_allowed",
                f"Symbol {symbol} is not in your group's allowed symbols",
            )

        max_per_day = group_config(user, "max_orders_per_day")
        if max_per_day is None:
            return
        day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        placed_today = await OrderRepository(session).count_for_account(
            account.id, day_start
        )
        if placed_today >= int(max_per_day):
            raise OrderRejected(
                "daily_order_cap",
                f"Daily order cap of {max_per_day} reached for this account",
            )

    @staticmethod
    def _check_buying_power(
        account: Account,
        side: str,
        order_type: str,
        quantity: Decimal,
        limit_price: Decimal | None,
        stop_price: Decimal | None,
        quote: Quote,
    ) -> None:
        """Reject a buy the account's cash cannot cover (no margin, paper only).

        A market or crossing-limit buy fills at the ask; a resting limit buy
        reserves buying power at its limit price; a stop buy reserves at its
        stop price (the worst case it can trigger into). Shared by order
        placement and stop-trigger evaluation so both price the same way.
        """
        if side != OrderSide.BUY:
            return
        if (
            order_type == OrderType.LIMIT
            and limit_price is not None
            and limit_price < quote.ask
        ):
            ref_price = limit_price  # resting limit
        elif order_type in (OrderType.STOP, OrderType.STOP_LIMIT):
            ref_price = stop_price if stop_price is not None else quote.ask
        else:
            ref_price = quote.ask  # market or crossing limit
        if account.cash_balance < quantity * ref_price:
            raise OrderRejected(
                "insufficient_buying_power", "Not enough cash for this order"
            )

    def _quote_age_seconds(self, quote: Quote, now: float | None = None) -> float | None:
        """Age of the quote in seconds, or None when the source did not stamp it.

        ``Quote.ts_event`` defaults to 0 and only some providers set it; 0 or a
        negative value means "unknown", and unknown is never treated as stale —
        doing so would reject every provider that does not stamp a timestamp.
        """
        if quote.ts_event is None or quote.ts_event <= 0:
            return None
        return (time.time() if now is None else now) - quote.ts_event

    def _is_stale_quote(self, quote: Quote) -> bool:
        age = self._quote_age_seconds(quote)
        return age is not None and age > self.max_quote_age_seconds

    def _require_fresh_quote(self, symbol: str, quote: Quote) -> None:
        age = self._quote_age_seconds(quote)
        if age is not None and age > self.max_quote_age_seconds:
            raise OrderRejected(
                "stale_quote",
                f"Quote for '{symbol}' is {age / 86400:.1f} days old "
                f"(max {self.max_quote_age_seconds / 86400:.1f}); refusing to trade",
            )

    async def evaluate_open_stops(
        self,
        session: AsyncSession,
        account: Account,
        symbol: str | None = None,
    ) -> list[Order]:
        """Evaluate every resting STOP/STOP_LIMIT order of one account against
        fresh quotes (design: 触发评估挂在"新报价到达"时点).

        Called whenever a new quote arrives for a symbol (order placement on
        that symbol, a run tick, or a realtime quote broadcast). A triggered
        STOP fills at its stop price through the same ledger path as any other
        fill, so it inherits the same constraints as a freshly placed order:

        - a stale quote is skipped (an old price must not trigger a stop);
        - buys are re-checked against the cash actually available at trigger
          time, at the price the trigger would fill at;
        - sells are bounded by the quantity actually held (``_apply_fill``).

        A trigger that fails a check is recorded REJECTED with the reason —
        never "triggered implies filled".
        """
        from datetime import datetime, timezone

        from sqlalchemy import select

        stmt = (
            select(Order)
            .where(
                Order.account_id == account.id,
                Order.status == OrderStatus.OPEN,
                Order.order_type.in_([OrderType.STOP, OrderType.STOP_LIMIT]),
            )
            .with_for_update()
        )
        if symbol is not None:
            stmt = stmt.where(Order.symbol == symbol)
        stops = (await session.execute(stmt)).scalars().all()
        triggered_orders: list[Order] = []
        for order in stops:
            quote = await self.provider.get_quote(order.symbol)
            if quote is None:
                continue  # no fresh quote: nothing to evaluate against
            if self._is_stale_quote(quote):
                continue  # an old price must not trigger (see MAX_QUOTE_AGE_SECONDS)
            # Record the trigger moment *only* when the stop condition actually
            # holds. `result.status == "OPEN"` cannot carry this: the engine uses
            # OPEN for both "condition not met, still resting" and "triggered but
            # the limit has not crossed". Stamping on OPEN marked every resting
            # stop as triggered, and the next evaluation — feeding that stamp back
            # as `triggered=True` — skips the condition check and fills a STOP at
            # its stop price (or a STOP_LIMIT by its limit rule) although the
            # price never crossed. Measured 2026-11-13: bid 269.95, stop 268.95,
            # an unrelated limit order on the same symbol → the stop filled at
            # 268.95 and the position vanished.
            if order.triggered_at is None and self.fill_engine.trigger_condition_met(
                quote, order.side, order.stop_price
            ):
                order.triggered_at = datetime.now(timezone.utc)
            # Compute the fill *after* stamping, so a stop triggered on this very
            # evaluation is judged as triggered (STOP fills; STOP_LIMIT keeps
            # resting as a limit until its limit crosses).
            result = self.fill_engine.compute_fill(
                quote,
                order.side,
                order.order_type,
                order.quantity,
                order.limit_price,
                order.stop_price,
                triggered=order.triggered_at is not None,
            )
            if result.fills:
                try:
                    # The cash a stop was placed against may be gone by the time
                    # it triggers; the same check as placement, at trigger price.
                    self._check_buying_power(
                        account, order.side, order.order_type, order.quantity,
                        order.limit_price, order.stop_price, quote,
                    )
                    for price, qty in result.fills:
                        await self._apply_fill(
                            session, account, order, order.instrument_id,
                            order.symbol, order.side, qty, price,
                        )
                except OrderRejected as exc:
                    # The trigger is real but unfillable: record why, keep the
                    # ledger untouched beyond the failure (single-fill engine).
                    order.status = OrderStatus.REJECTED
                    order.reject_reason = f"{exc.code}: {exc.message}"
                    order.filled_quantity = Decimal("0")
                    order.avg_fill_price = None
                    await session.flush()
                    continue
                order.filled_quantity = result.filled_quantity
                order.avg_fill_price = result.avg_fill_price
                order.status = (
                    OrderStatus.FILLED
                    if result.filled_quantity >= order.quantity
                    else OrderStatus.PARTIALLY_FILLED
                )
                triggered_orders.append(order)
            # Untriggered / triggered-but-resting stop-limits keep OPEN.
        if triggered_orders:
            await session.flush()
        return triggered_orders

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
        # Nothing is sold that is not held: paper trading is cash-only and
        # long-only, so a sell is always a reduction of an existing position.
        # Enforced here (the single ledger entry point) so every caller — order
        # placement and stop triggers alike — inherits the bound.
        if side == OrderSide.SELL:
            held = position.quantity if position is not None else Decimal("0")
            if qty > held:
                raise OrderRejected(
                    "insufficient_position",
                    f"Cannot sell {qty} {symbol}: only {held} held",
                )
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
                # 这一笔自己实现的那部分（买入为 0）。落到成交行上，"今日已实现"
                # 才是一条带 ts 的求和；持仓行那个累计值仍按原样维护，两者分工不变。
                realized_pnl=realized,
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
