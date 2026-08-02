"""Spike: prove Nautilus's matching engine fills a market order against a quote tick.

Uses BacktestEngine (which SandboxExecutionClient wraps internally) with a CASH
account and a TestClock. If a market BUY fills at the ask, we have a testable
pattern for the per-account paper execution service.
"""

from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide, TimeInForce
from nautilus_trader.model.objects import Money
from nautilus_trader.model.data import QuoteTick
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy

USD = TestInstrumentProvider.equity().quote_currency  # reuse the equity's quote currency
instrument = TestInstrumentProvider.equity()
print("instrument:", instrument.id, "venue:", instrument.id.venue, "currency:", instrument.quote_currency)


class MarketBuyStrategy(Strategy):
    def __init__(self, instrument_id, quantity):
        super().__init__(config=StrategyConfig(order_id_tag="spike"))
        self.instrument_id = instrument_id
        self.quantity = quantity
        self.filled_event = None
        self._submitted = False

    def on_start(self):
        self.subscribe_quote_ticks(self.instrument_id)

    def on_quote_tick(self, tick):
        if self._submitted:
            return
        self._submitted = True
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=self.quantity,
            time_in_force=TimeInForce.DAY,
        )
        self.submit_order(order)

    def on_order_filled(self, event):
        self.filled_event = event
        self.log.info(f"FILLED {event.client_order_id} qty={event.last_qty} px={event.last_px}")


def main():
    engine = BacktestEngine()
    venue = instrument.id.venue
    engine.add_venue(
        venue=venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        starting_balances=[Money(100_000, instrument.quote_currency)],
    )
    engine.add_instrument(instrument)

    quote = QuoteTick(
        instrument_id=instrument.id,
        bid_price=instrument.make_price(Decimal("149.00")),
        ask_price=instrument.make_price(Decimal("150.00")),
        bid_size=instrument.make_qty(Decimal("1000")),
        ask_size=instrument.make_qty(Decimal("1000")),
        ts_event=0,
        ts_init=0,
    )
    engine.add_data([quote])

    strategy = MarketBuyStrategy(instrument.id, instrument.make_qty(Decimal("10")))
    engine.add_strategy(strategy)

    engine.run()

    account = engine.portfolio.account(venue)
    positions = engine.cache.positions(venue=venue)
    orders = engine.cache.orders(venue=venue)
    print("account balance:", account.balance(instrument.quote_currency))
    print("positions:", [(p.instrument_id, p.side, p.quantity, p.avg_px_open) for p in positions])
    print("orders:", [(o.client_order_id, o.side, o.quantity, o.status) for o in orders])
    print("filled event:", strategy.filled_event is not None)

    engine.dispose()


if __name__ == "__main__":
    main()
