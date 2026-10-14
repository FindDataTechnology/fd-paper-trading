"""Unit tests for the SIM fill calculator.

Drives ``SimFillEngine.compute_fill`` directly. With a single static quote and
a deep book, a market order always fills and a limit order fills only when it
crosses the book; otherwise it rests OPEN.
"""

from decimal import Decimal

from fd_paper_trading.execution.fill_engine import FillResult, SimFillEngine
from fd_paper_trading.marketdata.provider import Quote

_QUOTE = Quote(instrument_id="AAPL.SIM", bid=Decimal("100.00"), ask=Decimal("101.00"))


def test_market_buy_fills_at_ask():
    result = SimFillEngine().compute_fill(_QUOTE, "BUY", "MARKET", Decimal("10"))
    assert result.status == "FILLED"
    assert result.filled_quantity == Decimal("10")
    assert result.avg_fill_price == Decimal("101.00")  # ask
    assert result.fills == [(Decimal("101.00"), Decimal("10"))]


def test_market_sell_fills_at_bid():
    result = SimFillEngine().compute_fill(_QUOTE, "SELL", "MARKET", Decimal("10"))
    assert result.status == "FILLED"
    assert result.filled_quantity == Decimal("10")
    assert result.avg_fill_price == Decimal("100.00")  # bid


def test_crossing_limit_buy_fills_immediately():
    result = SimFillEngine().compute_fill(
        _QUOTE, "BUY", "LIMIT", Decimal("10"), limit_price=Decimal("99999")
    )
    assert result.status == "FILLED"
    assert result.filled_quantity == Decimal("10")
    assert result.avg_fill_price == Decimal("101.00")


def test_at_ask_limit_buy_fills():
    # limit == ask is a crossing order and fills.
    result = SimFillEngine().compute_fill(
        _QUOTE, "BUY", "LIMIT", Decimal("10"), limit_price=Decimal("101.00")
    )
    assert result.status == "FILLED"


def test_non_crossing_limit_buy_rests_open():
    result = SimFillEngine().compute_fill(
        _QUOTE, "BUY", "LIMIT", Decimal("10"), limit_price=Decimal("1.00")
    )
    assert result.status == "OPEN"
    assert result.filled_quantity == Decimal("0")
    assert result.fills == []
    assert result.avg_fill_price is None


def test_limit_sell_crossing_fills_at_bid():
    result = SimFillEngine().compute_fill(
        _QUOTE, "SELL", "LIMIT", Decimal("10"), limit_price=Decimal("99.00")
    )
    assert result.status == "FILLED"
    assert result.avg_fill_price == Decimal("100.00")


def test_limit_sell_non_crossing_rests_open():
    result = SimFillEngine().compute_fill(
        _QUOTE, "SELL", "LIMIT", Decimal("10"), limit_price=Decimal("101.00")
    )
    assert result.status == "OPEN"
