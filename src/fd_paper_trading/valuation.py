"""Mark-to-market valuation, shared by the run equity snapshot and the account
summary.

One loop, one fallback rule: a position is worth ``quantity × latest close``,
or ``quantity × average price`` when no close can be fetched — a data gap
degrades to book value, never to zero. Both callers must agree on this, or the
run's equity curve and the account page would report different numbers for the
same ledger.
"""

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from .marketdata import MarketDataProvider
from .persistence.models import Account
from .persistence.repositories import PositionRepository


async def latest_close(
    provider: MarketDataProvider | None, symbol: str
) -> Decimal | None:
    """Latest daily close for ``symbol``; None when unavailable (data gap)."""
    if provider is None:
        return None
    try:
        bars = await provider.get_bars(symbol, "1d", 1)
    except Exception:  # noqa: BLE001 - a fetch failure is a gap, not a crash
        return None
    return bars[-1].close if bars else None


async def marked_equity(
    session: AsyncSession,
    account: Account,
    provider: MarketDataProvider | None,
) -> Decimal:
    """Account equity: cash plus every open position at its latest close."""
    total = account.cash_balance
    positions = await PositionRepository(session).list_for_account(
        account.id, account.user_id
    )
    for position in positions:
        mark = await latest_close(provider, position.symbol)
        if mark is None:
            mark = position.avg_price  # fallback: book value
        total += position.quantity * mark
    return total
