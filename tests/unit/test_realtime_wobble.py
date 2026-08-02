"""Unit tests for ConnectionManager._wobble quote math."""

from decimal import Decimal

from fd_trade.marketdata import SimulatedMarketDataProvider
from fd_trade.marketdata.provider import Quote
from fd_trade.realtime.manager import ConnectionManager


def _manager() -> ConnectionManager:
    return ConnectionManager(provider=SimulatedMarketDataProvider())


def test_bid_less_than_mid_less_than_ask():
    mgr = _manager()
    quote = Quote(instrument_id="AAPL.SIM", bid=Decimal("100.00"), ask=Decimal("101.00"))
    mid = (100.00 + 101.00) / 2  # 100.5
    bid, ask = mgr._wobble("AAPL", quote)
    assert float(bid) < mid < float(ask)


def test_bid_strictly_less_than_ask():
    mgr = _manager()
    quote = Quote(instrument_id="AAPL.SIM", bid=Decimal("100.00"), ask=Decimal("101.00"))
    bid, ask = mgr._wobble("AAPL", quote)
    assert float(bid) < float(ask)


def test_deterministic_for_same_tick_count_and_symbol():
    mgr = _manager()
    quote = Quote(instrument_id="AAPL.SIM", bid=Decimal("100.00"), ask=Decimal("101.00"))
    first = mgr._wobble("AAPL", quote)
    second = mgr._wobble("AAPL", quote)
    assert first == second


def test_output_is_two_decimal_string():
    mgr = _manager()
    quote = Quote(instrument_id="AAPL.SIM", bid=Decimal("100.00"), ask=Decimal("101.00"))
    bid, ask = mgr._wobble("AAPL", quote)
    assert isinstance(bid, str) and isinstance(ask, str)
    # Two decimal places.
    assert len(bid.split(".")[-1]) == 2
    assert len(ask.split(".")[-1]) == 2
