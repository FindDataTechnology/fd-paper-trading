"""Position lots: T+1 availability and FIFO consumption (change B, tasks 3.2-3.3).

Pure functions over lot literals — no DB. These pin the two rules the sell guard
and the PnL maths depend on, so they cannot drift apart.
"""

from datetime import date
from decimal import Decimal

import pytest

from fd_paper_trading.execution.lots import (
    LotLike,
    available,
    consume_fifo,
    total_remaining,
)

TODAY = date(2026, 11, 24)


def _lot(remaining, unit_cost, opened_on):
    return LotLike(remaining=Decimal(remaining), unit_cost=Decimal(unit_cost),
                   opened_on=opened_on)


def test_same_day_lot_is_not_available():
    lots = [_lot("100", "10.00", TODAY)]
    assert available(lots, TODAY) == 0


def test_prior_day_lot_is_available():
    lots = [_lot("100", "10.00", date(2026, 11, 23))]
    assert available(lots, TODAY) == Decimal("100")


def test_mixed_lots_only_count_settled_shares():
    lots = [
        _lot("100", "10.00", date(2026, 11, 23)),
        _lot("50", "12.00", TODAY),
    ]
    assert available(lots, TODAY) == Decimal("100")
    assert total_remaining(lots) == Decimal("150")


def test_fifo_consumes_oldest_first():
    lots = [
        _lot("100", "10.00", date(2026, 11, 20)),
        _lot("100", "12.00", date(2026, 11, 21)),
    ]
    taken = consume_fifo(lots, Decimal("150"))
    assert [c.taken for c in taken] == [Decimal("100"), Decimal("50")]
    assert taken[0].lot.unit_cost == Decimal("10.00")


def test_fifo_refuses_to_over_consume():
    lots = [_lot("100", "10.00", date(2026, 11, 20))]
    with pytest.raises(ValueError):
        consume_fifo(lots, Decimal("101"))


def test_fifo_skips_exhausted_lots():
    lots = [
        _lot("0", "10.00", date(2026, 11, 18)),
        _lot("50", "12.00", date(2026, 11, 19)),
    ]
    taken = consume_fifo(lots, Decimal("50"))
    assert len(taken) == 1
    assert taken[0].lot.unit_cost == Decimal("12.00")
