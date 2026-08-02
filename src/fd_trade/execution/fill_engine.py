"""Nautilus-backed fill calculator.

Uses a ``BacktestEngine`` (the same ``SimulatedExchange``/``OrderMatchingEngine``
that ``SandboxExecutionClient`` wraps) to compute how an order fills against a
quote. This is used as a stateless fill calculator; account state (positions,
balances) is tracked in PostgreSQL, which keeps accounts isolated without
needing per-account Nautilus venues.
"""

from dataclasses import dataclass
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig, StrategyConfig
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, TimeInForce
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Money
from nautilus_trader.trading.strategy import Strategy

from ..marketdata.provider import Quote


@dataclass
class FillResult:
    """Outcome of attempting to fill an order."""

    status: str  # FILLED | PARTIALLY_FILLED | OPEN | REJECTED
    fills: list[tuple[Decimal, Decimal]]  # list of (price, quantity)
    filled_quantity: Decimal
    avg_fill_price: Decimal | None


class _OrderStrategy(Strategy):
    """Submits one order on the first quote tick so the book has a price."""

    def __init__(
        self,
        instrument: Instrument,
        side: OrderSide,
        order_type: str,
        quantity: Decimal,
        limit_price: Decimal | None,
    ) -> None:
        super().__init__(config=StrategyConfig(order_id_tag="fdt"))
        self.instrument = instrument
        self.side = side
        self.order_type = order_type
        self.quantity = quantity
        self.limit_price = limit_price
        self._submitted = False

    def on_start(self) -> None:
        self.subscribe_quote_ticks(self.instrument.id)

    def on_quote_tick(self, tick) -> None:  # type: ignore[override]
        if self._submitted:
            return
        self._submitted = True
        if self.order_type == "MARKET":
            order = self.order_factory.market(
                instrument_id=self.instrument.id,
                order_side=self.side,
                quantity=self.quantity,
                time_in_force=TimeInForce.DAY,
            )
        else:
            order = self.order_factory.limit(
                instrument_id=self.instrument.id,
                order_side=self.side,
                quantity=self.quantity,
                price=self.limit_price,
            )
        self.submit_order(order)


class NautilusFillEngine:
    """Computes fills for a single order against a single quote."""

    def compute_fill(
        self,
        instrument: Instrument,
        quote: Quote,
        side: str,
        order_type: str,
        quantity: Decimal,
        limit_price: Decimal | None = None,
    ) -> FillResult:
        # bypass_logging: the global Rust logger can only be initialized once per
        # process, so each stateless fill engine must skip logging setup.
        engine = BacktestEngine(
            config=BacktestEngineConfig(logging=LoggingConfig(bypass_logging=True))
        )
        venue = instrument.id.venue
        engine.add_venue(
            venue=venue,
            oms_type=OmsType.NETTING,
            # MARGIN so sells always fill (short or not); whether a sell closes a
            # long or opens a short is decided by the Postgres ledger, which knows
            # the account's real position. The calculator only needs the fill price.
            account_type=AccountType.MARGIN,
            # Generous balance: buying-power checks live in the Postgres ledger,
            # not in this stateless fill calculator.
            starting_balances=[Money(1_000_000_000, instrument.quote_currency)],
        )
        engine.add_instrument(instrument)

        tick = QuoteTick(
            instrument_id=instrument.id,
            bid_price=instrument.make_price(quote.bid),
            ask_price=instrument.make_price(quote.ask),
            bid_size=instrument.make_qty(Decimal("1_000_000")),
            ask_size=instrument.make_qty(Decimal("1_000_000")),
            ts_event=0,
            ts_init=0,
        )
        engine.add_data([tick])

        nautilus_side = OrderSide.BUY if side == "BUY" else OrderSide.SELL
        strat = _OrderStrategy(
            instrument,
            nautilus_side,
            order_type,
            instrument.make_qty(quantity),
            instrument.make_price(limit_price) if limit_price is not None else None,
        )
        engine.add_strategy(strat)
        engine.run()

        orders = engine.cache.orders(venue=venue)
        result = self._extract(orders[-1] if orders else None, quantity)
        engine.dispose()
        return result

    @staticmethod
    def _extract(order, quantity: Decimal) -> FillResult:
        if order is None:
            return FillResult("REJECTED", [], Decimal("0"), None)

        filled_qty = Decimal(str(order.filled_qty))
        if filled_qty >= quantity:
            status = "FILLED"
        elif filled_qty > 0:
            status = "PARTIALLY_FILLED"
        else:
            status = "OPEN"

        if filled_qty > 0 and order.avg_px is not None:
            avg = Decimal(str(order.avg_px))
            fills = [(avg, filled_qty)]
        else:
            avg = None
            fills = []
        return FillResult(status, fills, filled_qty, avg)
