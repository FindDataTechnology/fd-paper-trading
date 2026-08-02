"""Unit tests for the Nautilus fill calculator.

Drives ``NautilusFillEngine.compute_fill`` directly with a known instrument and
quote. The engine is stateless per order, so a resting limit never fills on its
own - only market and crossing-limit orders fill at placement.
"""

from decimal import Decimal

from fd_trade.execution.fill_engine import FillResult, NautilusFillEngine
from fd_trade.marketdata import SimulatedMarketDataProvider
from fd_trade.marketdata.provider import Quote

_INSTRUMENT = SimulatedMarketDataProvider().get_instrument("AAPL")
_QUOTE = Quote(instrument_id="AAPL.SIM", bid=Decimal("100.00"), ask=Decimal("101.00"))


def test_market_buy_fills_at_ask():
    result = NautilusFillEngine().compute_fill(_INSTRUMENT, _QUOTE, "BUY", "MARKET", Decimal("10"))
    assert result.status == "FILLED"
    assert result.filled_quantity == Decimal("10")
    assert result.avg_fill_price == Decimal("101.00")  # ask
    assert len(result.fills) == 1


def test_market_sell_fills_at_bid():
    result = NautilusFillEngine().compute_fill(_INSTRUMENT, _QUOTE, "SELL", "MARKET", Decimal("10"))
    assert result.status == "FILLED"
    assert result.filled_quantity == Decimal("10")
    assert result.avg_fill_price == Decimal("100.00")  # bid


def test_crossing_limit_fills_immediately():
    result = NautilusFillEngine().compute_fill(
        _INSTRUMENT, _QUOTE, "BUY", "LIMIT", Decimal("10"), limit_price=Decimal("99999")
    )
    assert result.status == "FILLED"
    assert result.filled_quantity == Decimal("10")
    assert result.avg_fill_price is not None


def test_non_crossing_limit_rests_open():
    result = NautilusFillEngine().compute_fill(
        _INSTRUMENT, _QUOTE, "BUY", "LIMIT", Decimal("10"), limit_price=Decimal("1.00")
    )
    assert result.status == "OPEN"
    assert result.filled_quantity == Decimal("0")
    assert result.fills == []
    assert result.avg_fill_price is None


def test_extract_none_order_is_rejected():
    # Covers the REJECTED branch of _extract (no order produced by the engine).
    result = NautilusFillEngine._extract(None, Decimal("10"))
    assert isinstance(result, FillResult)
    assert result.status == "REJECTED"
    assert result.fills == []
    assert result.filled_quantity == Decimal("0")
    assert result.avg_fill_price is None
