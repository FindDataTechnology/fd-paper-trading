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


# ---------------------------------------------------------------------------
# STOP / STOP_LIMIT (spec: order-execution "Stop orders rest, trigger, fill")
# ---------------------------------------------------------------------------


def test_untriggered_sell_stop_rests_open():
    """bid (100) > stop (95): no trigger, rests OPEN, zero position impact."""
    result = SimFillEngine().compute_fill(
        _QUOTE, "SELL", "STOP", Decimal("10"), stop_price=Decimal("95.00")
    )
    assert result.status == "OPEN"
    assert result.filled_quantity == Decimal("0")
    assert result.fills == []


def test_triggered_sell_stop_fills_at_stop_price():
    """bid (100) <= stop (105): trigger met, fills at the stop price."""
    result = SimFillEngine().compute_fill(
        _QUOTE, "SELL", "STOP", Decimal("10"), stop_price=Decimal("105.00")
    )
    assert result.status == "FILLED"
    assert result.avg_fill_price == Decimal("105.00")
    assert result.fills == [(Decimal("105.00"), Decimal("10"))]


def test_sell_stop_at_bid_triggers():
    """bid == stop is at-or-below: triggers (spec: bid <= stop)."""
    result = SimFillEngine().compute_fill(
        _QUOTE, "SELL", "STOP", Decimal("10"), stop_price=Decimal("100.00")
    )
    assert result.status == "FILLED"
    assert result.avg_fill_price == Decimal("100.00")


def test_buy_stop_triggers_when_ask_at_or_above_stop():
    """ask (101) >= stop (99): buy stop triggers, fills at the stop price."""
    result = SimFillEngine().compute_fill(
        _QUOTE, "BUY", "STOP", Decimal("10"), stop_price=Decimal("99.00")
    )
    assert result.status == "FILLED"
    assert result.avg_fill_price == Decimal("99.00")


def test_buy_stop_below_ask_rests():
    """ask (101) < stop (105): not triggered, rests."""
    result = SimFillEngine().compute_fill(
        _QUOTE, "BUY", "STOP", Decimal("10"), stop_price=Decimal("105.00")
    )
    assert result.status == "OPEN"


def test_stop_requires_stop_price():
    result = SimFillEngine().compute_fill(_QUOTE, "SELL", "STOP", Decimal("10"))
    assert result.status == "REJECTED"


def test_stop_limit_requires_limit_price():
    result = SimFillEngine().compute_fill(
        _QUOTE, "SELL", "STOP_LIMIT", Decimal("10"), stop_price=Decimal("95.00")
    )
    assert result.status == "REJECTED"


def test_stop_limit_triggers_then_limit_rests():
    """Triggered (bid <= stop) but limit not crossed -> rests as triggered limit."""
    # bid=100 <= stop=105 triggers; limit=103 > bid=100 does not cross -> rests
    # (sell-limit fills only when limit <= bid; 103 > 100 rests)
    result = SimFillEngine().compute_fill(
        _QUOTE, "SELL", "STOP_LIMIT", Decimal("10"),
        limit_price=Decimal("103.00"), stop_price=Decimal("105.00"),
    )
    assert result.status == "OPEN"
    assert result.filled_quantity == Decimal("0")


def test_stop_limit_triggers_then_fills_at_bid():
    """Triggered and limit crossed: fills at the bid (limit rule after trigger)."""
    # bid=100 <= stop=105 triggers; limit=99 <= bid=100 crosses -> fills at bid
    result = SimFillEngine().compute_fill(
        _QUOTE, "SELL", "STOP_LIMIT", Decimal("10"),
        limit_price=Decimal("99.00"), stop_price=Decimal("105.00"),
    )
    assert result.status == "FILLED"
    assert result.avg_fill_price == Decimal("100.00")  # bid, not the limit


def test_stop_limit_buy_trigger_then_limit_rule():
    """Buy stop-limit: ask >= stop triggers; then limit buy rule applies."""
    # ask=101 >= stop=99 triggers; limit=102 >= ask=101 crosses -> fills at ask
    result = SimFillEngine().compute_fill(
        _QUOTE, "BUY", "STOP_LIMIT", Decimal("10"),
        limit_price=Decimal("102.00"), stop_price=Decimal("99.00"),
    )
    assert result.status == "FILLED"
    assert result.avg_fill_price == Decimal("101.00")
    # limit below ask after trigger: rests as triggered limit
    result2 = SimFillEngine().compute_fill(
        _QUOTE, "BUY", "STOP_LIMIT", Decimal("10"),
        limit_price=Decimal("100.50"), stop_price=Decimal("99.00"),
    )
    assert result2.status == "OPEN"


def test_pre_triggered_stop_limit_with_explicit_flag():
    """A stop-limit already marked triggered skips the stop check (the ledger
    records the trigger moment; subsequent evaluations only apply the limit
    rule)."""
    # triggered=True: limit=103 vs bid=100 does not cross -> rests; the trigger
    # flag alone does not fill it
    result = SimFillEngine().compute_fill(
        _QUOTE, "SELL", "STOP_LIMIT", Decimal("10"),
        limit_price=Decimal("103.00"), stop_price=Decimal("105.00"),
        triggered=True,
    )
    assert result.status == "OPEN"
    # and with limit crossed (limit=100 <= bid=100) it fills even though the
    # current quote no longer satisfies the stop condition
    result2 = SimFillEngine().compute_fill(
        _QUOTE, "SELL", "STOP_LIMIT", Decimal("10"),
        limit_price=Decimal("100.00"), stop_price=Decimal("105.00"),
        triggered=True,
    )
    assert result2.status == "FILLED"
    assert result2.avg_fill_price == Decimal("100.00")


def test_gap_through_stop_fills_in_one_quote():
    """A quote that gaps through the stop triggers and fills in one evaluation
    (daily-bar reality: the quote already reflects the gap)."""
    gapped = Quote(instrument_id="AAPL.SIM", bid=Decimal("80.00"), ask=Decimal("81.00"))
    result = SimFillEngine().compute_fill(
        gapped, "SELL", "STOP", Decimal("10"), stop_price=Decimal("95.00")
    )
    assert result.status == "FILLED"
    assert result.avg_fill_price == Decimal("95.00")  # the stop, not the bid


def test_unsupported_order_type_raises():
    import pytest

    with pytest.raises(ValueError):
        SimFillEngine().compute_fill(_QUOTE, "BUY", "STOP_LOSS", Decimal("10"))
