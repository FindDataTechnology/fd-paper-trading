"""Position lots: T+1 sellability and FIFO realized PnL.

## Why a pure module

The two rules this holds — "what is sellable today" and "which shares did this
sell consume" — are the kind of thing that must be provable without a database.
They are pure functions over lot rows, so FIFO consumption can be tested with
three literals instead of a seeded ledger.

## The T+1 rule, stated once

``available(lots, as_of)`` is the sum of ``remaining`` over lots whose
``opened_on`` is strictly before ``as_of``. A share bought today has
``opened_on == today`` and is not available; it becomes so the next day. This is
the single place the rule lives — the sell guard and the stop-trigger guard both
call it, so they cannot disagree about when a share is sellable.

## Legacy positions have no lots

A position that predates this change has no lot rows. Treating "no lots" as
"nothing sellable" would freeze every existing account. So a position **with no
lots at all** is fully sellable (``available_quantity`` falls back to the
position's quantity). Once a symbol has any lot, the rule switches to per-lot —
stated in the spec, not left implicit, because the two modes coexisting is the
one place a reader could be surprised.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Sequence

from ..persistence.models import PositionLot


@dataclass(frozen=True)
class LotLike:
    """The fields FIFO needs, so tests need not build a full ORM row."""

    remaining: Decimal
    unit_cost: Decimal
    opened_on: date


def available(lots: Sequence[LotLike], as_of: date) -> Decimal:
    """Shares sellable on ``as_of``: everything opened strictly before today."""
    return sum(
        (lot.remaining for lot in lots if lot.opened_on < as_of),
        Decimal(0),
    )


def total_remaining(lots: Sequence[LotLike]) -> Decimal:
    return sum((lot.remaining for lot in lots), Decimal(0))


@dataclass
class Consumption:
    """One lot's contribution to a sell: the lot, and how much was taken."""

    lot: LotLike
    taken: Decimal


def consume_fifo(
    lots: Sequence[LotLike],
    quantity: Decimal,
) -> list[Consumption]:
    """Consume ``quantity`` across ``lots`` oldest-first (already sorted).

    Returns per-lot consumptions. Raises if the lots cannot cover the quantity:
    an over-sell must be refused upstream, never partially fulfilled. Realized
    PnL is computed by the caller from the sell price (it is a property of the
    sell, not of which lot was consumed).
    """
    need = quantity
    out: list[Consumption] = []
    for lot in lots:
        if need <= 0:
            break
        if lot.remaining <= 0:
            continue
        take = lot.remaining if lot.remaining <= need else need
        out.append(Consumption(lot=lot, taken=take))
        need -= take
    if need > 0:
        raise ValueError(
            f"lots cannot cover {quantity} (short by {need}); "
            "the sell guard must reject before consuming"
        )
    return out


def new_lot(
    *,
    account_id: uuid.UUID,
    instrument_id: str,
    symbol: str,
    opened_on: date,
    quantity: Decimal,
    unit_cost: Decimal,
) -> PositionLot:
    """A freshly-bought lot, fully remaining."""
    return PositionLot(
        account_id=account_id,
        instrument_id=instrument_id,
        symbol=symbol,
        opened_on=opened_on,
        quantity=quantity,
        remaining=quantity,
        unit_cost=unit_cost,
    )
