"""SIM fill calculator.

The simulated provider emits a single static quote with a deep book (1M each
side), so a fill is fully determined by arithmetic against that one quote:

  market buy   -> fills at ask
  market sell  -> fills at bid
  limit buy    -> fills at ask iff limit >= ask, else rests OPEN
  limit sell   -> fills at bid iff limit <= bid, else rests OPEN

No queueing, no partial fills, no slippage, no price movement — a full
matching engine would produce exactly this result at ~1000x the cost for
this paper-only calculator.
"""

from dataclasses import dataclass
from decimal import Decimal

from ..marketdata.provider import Quote


@dataclass
class FillResult:
    """Outcome of attempting to fill an order."""

    status: str  # FILLED | PARTIALLY_FILLED | OPEN | REJECTED
    fills: list[tuple[Decimal, Decimal]]  # list of (price, quantity)
    filled_quantity: Decimal
    avg_fill_price: Decimal | None


class SimFillEngine:
    """Computes fills for a single order against a single quote (pure Python)."""

    def compute_fill(
        self,
        quote: Quote,
        side: str,
        order_type: str,
        quantity: Decimal,
        limit_price: Decimal | None = None,
    ) -> FillResult:
        if order_type == "MARKET":
            price = quote.ask if side == "BUY" else quote.bid
            return FillResult("FILLED", [(price, quantity)], quantity, price)

        # LIMIT: fills only when it crosses the book.
        if side == "BUY":
            if limit_price is not None and limit_price >= quote.ask:
                price = quote.ask
            else:
                return FillResult("OPEN", [], Decimal("0"), None)
        else:
            if limit_price is not None and limit_price <= quote.bid:
                price = quote.bid
            else:
                return FillResult("OPEN", [], Decimal("0"), None)
        return FillResult("FILLED", [(price, quantity)], quantity, price)
