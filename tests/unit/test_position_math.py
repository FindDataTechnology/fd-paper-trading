"""Unit tests for signed-quantity position PnL math.

Exercises ``PaperExecutionService._update_position`` directly - no HTTP, no
database, no Nautilus engine. This is the highest-risk accounting logic.
"""

from decimal import Decimal

from fd_paper_trading.execution.service import PaperExecutionService
from fd_paper_trading.persistence.models import Position


def _pos(qty="0", avg="0", realized="0") -> Position:
    return Position(
        account_id=None,
        instrument_id="AAPL.SIM",
        symbol="AAPL",
        quantity=Decimal(qty),
        avg_price=Decimal(avg),
        realized_pnl=Decimal(realized),
    )


def test_open_long_sets_quantity_and_avg_no_pnl():
    pos = _pos()
    realized = PaperExecutionService._update_position(pos, "BUY", Decimal("10"), Decimal("100"))
    assert pos.quantity == Decimal("10")
    assert pos.avg_price == Decimal("100")
    assert realized == Decimal("0")
    assert pos.realized_pnl == Decimal("0")


def test_open_short_sets_negative_quantity():
    pos = _pos()
    realized = PaperExecutionService._update_position(pos, "SELL", Decimal("10"), Decimal("100"))
    assert pos.quantity == Decimal("-10")
    assert pos.avg_price == Decimal("100")
    assert realized == Decimal("0")


def test_add_same_direction_uses_weighted_avg_no_pnl():
    pos = _pos(qty="10", avg="100")
    realized = PaperExecutionService._update_position(pos, "BUY", Decimal("10"), Decimal("120"))
    assert pos.quantity == Decimal("20")
    assert pos.avg_price == Decimal("110")  # (10*100 + 10*120) / 20
    assert realized == Decimal("0")


def test_reduce_realizes_pnl_and_keeps_avg():
    pos = _pos(qty="10", avg="100")
    realized = PaperExecutionService._update_position(pos, "SELL", Decimal("4"), Decimal("110"))
    assert pos.quantity == Decimal("6")
    assert pos.avg_price == Decimal("100")  # avg unchanged on a reduce
    assert realized == Decimal("40")  # (110 - 100) * 4
    assert pos.realized_pnl == Decimal("40")


def test_fully_close_realizes_all_pnl():
    pos = _pos(qty="10", avg="100")
    realized = PaperExecutionService._update_position(pos, "SELL", Decimal("10"), Decimal("110"))
    assert pos.quantity == Decimal("0")
    assert realized == Decimal("100")  # (110 - 100) * 10
    assert pos.realized_pnl == Decimal("100")


def test_flip_long_to_short_resets_avg_and_realizes_closing_leg():
    pos = _pos(qty="10", avg="100")
    realized = PaperExecutionService._update_position(pos, "SELL", Decimal("15"), Decimal("110"))
    assert pos.quantity == Decimal("-5")  # 10 - 15
    assert pos.avg_price == Decimal("110")  # flipped side -> avg resets to fill price
    assert realized == Decimal("100")  # closing leg: (110 - 100) * 10
    assert pos.realized_pnl == Decimal("100")


def test_flip_short_to_long_resets_avg_and_realizes_closing_leg():
    pos = _pos(qty="-10", avg="100")
    realized = PaperExecutionService._update_position(pos, "BUY", Decimal("15"), Decimal("90"))
    assert pos.quantity == Decimal("5")  # -10 + 15
    assert pos.avg_price == Decimal("90")  # flipped side -> avg resets to fill price
    assert realized == Decimal("100")  # (90 - 100) * 10 * -1
    assert pos.realized_pnl == Decimal("100")


def test_add_to_short_uses_weighted_avg():
    pos = _pos(qty="-10", avg="100")
    realized = PaperExecutionService._update_position(pos, "SELL", Decimal("10"), Decimal("120"))
    assert pos.quantity == Decimal("-20")
    assert pos.avg_price == Decimal("110")  # (10*100 + 10*120) / 20
    assert realized == Decimal("0")
