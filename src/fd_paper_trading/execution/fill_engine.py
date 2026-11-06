"""SIM fill calculator.

The simulated provider emits a single static quote with a deep book (1M each
side), so a fill is fully determined by arithmetic against that one quote:

  market buy   -> fills at ask
  market sell  -> fills at bid
  limit buy    -> fills at ask iff limit >= ask, else rests OPEN
  limit sell   -> fills at bid iff limit <= bid, else rests OPEN
  stop sell    -> triggers when bid <= stop_price, fills at stop_price
  stop buy     -> triggers when ask >= stop_price, fills at stop_price
  stop-limit sell -> triggers when bid <= stop_price, then behaves as limit sell
  stop-limit buy  -> triggers when ask >= stop_price, then behaves as limit buy

No queueing, no partial fills, no slippage, no price movement — a full
matching engine would produce exactly this result at ~1000x the cost for
this paper-only calculator.

STOP/STOP_LIMIT trigger evaluation is stateless: the engine receives the
current quote and the order's stop_price/limit_price, decides whether the
trigger condition is met, and if so applies the corresponding fill rule.
"""

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

from ..marketdata.provider import Quote


class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP = "STOP"
    STOP_LIMIT = "STOP_LIMIT"


@dataclass
class FillResult:
    """Outcome of attempting to fill an order."""

    status: str  # FILLED | PARTIALLY_FILLED | OPEN | REJECTED | TRIGGERED
    fills: list[tuple[Decimal, Decimal]]  # list of (price, quantity)
    filled_quantity: Decimal
    avg_fill_price: Decimal | None


class TriggerResult(str, Enum):
    """Result of evaluating a stop order against a quote."""
    NOT_TRIGGERED = "NOT_TRIGGERED"
    TRIGGERED_FILLED = "TRIGGERED_FILLED"
    TRIGGERED_RESTING = "TRIGGERED_RESTING"


@dataclass
class TriggerOutcome:
    """Result of evaluating a stop-type order against a quote."""
    trigger_result: TriggerResult
    fill_result: 'FillResult | None' = None


class SimFillEngine:
    """Computes fills for a single order against a single quote (pure Python)."""

    def compute_fill(
        self,
        quote: Quote,
        side: str,
        order_type: str,
        quantity: Decimal,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        triggered: bool = False,
    ) -> FillResult:
        """Compute fill for MARKET, LIMIT, STOP, or STOP_LIMIT order.

        Args:
            quote: Current quote with bid/ask
            side: "BUY" or "SELL"
            order_type: "MARKET", "LIMIT", "STOP", or "STOP_LIMIT"
            quantity: Order quantity
            limit_price: Limit price for LIMIT/STOP_LIMIT orders
            stop_price: Stop price for STOP/STOP_LIMIT orders
            triggered: Whether the stop has already been triggered (for STOP_LIMIT)
        """
        if order_type == "MARKET":
            price = quote.ask if side == "BUY" else quote.bid
            return FillResult("FILLED", [(price, quantity)], quantity, price)

        if order_type == "LIMIT":
            return self._compute_limit_fill(quote, side, quantity, limit_price)

        if order_type in ("STOP", "STOP_LIMIT"):
            return self._compute_stop_fill(
                quote, side, order_type, quantity, limit_price, stop_price, triggered
            )

        raise ValueError(f"Unknown order type: {order_type}")

    def _compute_limit_fill(
        self,
        quote: Quote,
        side: str,
        quantity: Decimal,
        limit_price: Decimal | None,
    ) -> FillResult:
        if limit_price is None:
            return FillResult("REJECTED", [], Decimal("0"), None)

        if side == "BUY":
            if limit_price >= quote.ask:
                price = quote.ask
            else:
                return FillResult("OPEN", [], Decimal("0"), None)
        else:
            if limit_price <= quote.bid:
                price = quote.bid
            else:
                return FillResult("OPEN", [], Decimal("0"), None)
        return FillResult("FILLED", [(price, quantity)], quantity, price)

    def _compute_stop_fill(
        self,
        quote: Quote,
        side: str,
        order_type: str,
        quantity: Decimal,
        limit_price: Decimal | None,
        stop_price: Decimal | None,
        triggered: bool,
    ) -> FillResult:
        """Compute fill for STOP or STOP_LIMIT order."""
        if stop_price is None:
            return FillResult("REJECTED", [], Decimal("0"), None)

        if order_type == "STOP_LIMIT" and limit_price is None:
            return FillResult("REJECTED", [], Decimal("0"), None)

        # Check trigger condition
        if side == "BUY":
            # Buy stop triggers when ask >= stop_price
            is_triggered = triggered or quote.ask >= stop_price
        else:
            # Sell stop triggers when bid <= stop_price
            is_triggered = triggered or quote.bid <= stop_price

        if not is_triggered:
            # Stop not triggered - rests as OPEN
            return FillResult("OPEN", [], Decimal("0"), None)

        # Stop triggered
        if order_type == "STOP":
            # STOP becomes MARKET at trigger - fill at stop_price
            price = stop_price
            return FillResult("FILLED", [(price, quantity)], quantity, price)

        # STOP_LIMIT: after trigger, behave as LIMIT order
        if side == "BUY":
            if limit_price >= quote.ask:
                price = quote.ask
            else:
                # Triggered but limit not crossed - rests as triggered LIMIT
                return FillResult("OPEN", [], Decimal("0"), None)
        else:
            if limit_price <= quote.bid:
                price = quote.bid
            else:
                return FillResult("OPEN", [], Decimal("0"), None)
        return FillResult("FILLED", [(price, quantity)], quantity, price)

    def evaluate_trigger(
        self,
        quote: Quote,
        side: str,
        order_type: str,
        stop_price: Decimal | None,
        limit_price: Decimal | None,
        triggered: bool = False,
    ) -> 'TriggerOutcome':
        """Evaluate whether a stop-type order triggers against a quote.

        Returns a TriggerOutcome indicating whether the stop triggered,
        and if so, the resulting fill (for STOP) or resting state (for STOP_LIMIT).
        """
        if order_type not in ("STOP", "STOP_LIMIT"):
            raise ValueError(f"evaluate_trigger only supports STOP/STOP_LIMIT, got {order_type}")

        if stop_price is None:
            from dataclasses import dataclass
            from decimal import Decimal
            from enum import Enum

            class TriggerResult(str, Enum):
                NOT_TRIGGERED = "NOT_TRIGGERED"
                TRIGGERED_FILLED = "TRIGGERED_FILLED"
                TRIGGERED_RESTING = "TRIGGERED_RESTING"

            @dataclass
            class TriggerOutcome:
                trigger_result: str
                fill_result: 'FillResult | None' = None

            return TriggerOutcome("NOT_TRIGGERED", FillResult("REJECTED", [], Decimal("0"), None))

        if order_type == "STOP_LIMIT" and limit_price is None:
            return TriggerOutcome("NOT_TRIGGERED", FillResult("REJECTED", [], Decimal("0"), None))

        # Check trigger condition
        if side == "BUY":
            is_triggered = triggered or quote.ask >= stop_price
        else:
            is_triggered = triggered or quote.bid <= stop_price

        if not is_triggered:
            return TriggerOutcome("NOT_TRIGGERED")

        # Stop triggered
        if order_type == "STOP":
            # STOP becomes MARKET at trigger - fill at stop_price
            fill = FillResult("FILLED", [(stop_price, Decimal("0"))], Decimal("0"), stop_price)
            return TriggerOutcome("TRIGGERED_FILLED", fill)

        # STOP_LIMIT: after trigger, check limit condition
        if side == "BUY":
            if limit_price is not None and limit_price >= quote.ask:
                fill = FillResult("FILLED", [(quote.ask, Decimal("0"))], Decimal("0"), quote.ask)
                return TriggerOutcome("TRIGGERED_FILLED", fill)
            # Limit not crossed - rests as triggered LIMIT
            return TriggerOutcome("TRIGGERED_RESTING")
        else:
            if limit_price is not None and limit_price <= quote.bid:
                fill = FillResult("FILLED", [(quote.bid, Decimal("0"))], Decimal("0"), quote.bid)
                return TriggerOutcome("TRIGGERED_FILLED", fill)
            return TriggerOutcome("TRIGGERED_RESTING")