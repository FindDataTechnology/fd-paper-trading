"""Unit tests for AccountService against an in-memory SQLite session.

No HTTP round-trip. Exercises create (default + custom balance, initial
deposit transaction), deposit/withdraw edge cases, and reset after a trade.
"""

import uuid
from decimal import Decimal

import pytest
import pytest_asyncio
from sqlalchemy import select

from fd_paper_trading.accounts.service import AccountService
from fd_paper_trading.config import Settings
from fd_paper_trading.persistence.base import create_database, init_db
from fd_paper_trading.persistence.models import BalanceTransaction, Order, OrderStatus, Position
from fd_paper_trading.persistence.repositories import AccountRepository


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest_asyncio.fixture
async def session():
    db = create_database("sqlite+aiosqlite://")
    await init_db(db)
    async with db.session_factory() as s:
        yield s
    await db.engine.dispose()


async def test_create_default_balance_records_initial_deposit(session):
    svc = AccountService(session, settings=_settings())
    account = await svc.create(uuid.uuid4(), "default", None)
    assert account.cash_balance == Decimal("100000")
    assert account.starting_balance == Decimal("100000")

    txns = (await session.execute(
        select(BalanceTransaction).where(BalanceTransaction.account_id == account.id)
    )).scalars().all()
    assert len(txns) == 1
    assert txns[0].type == "deposit"
    assert txns[0].amount == Decimal("100000")


async def test_create_custom_balance(session):
    svc = AccountService(session, settings=_settings())
    account = await svc.create(uuid.uuid4(), "acct", Decimal("50000"))
    assert account.cash_balance == Decimal("50000")


async def test_deposit_and_withdrawal(session):
    svc = AccountService(session, settings=_settings())
    account = await svc.create(uuid.uuid4(), "acct", Decimal("100000"))
    await svc.deposit(account, Decimal("5000"))
    assert account.cash_balance == Decimal("105000")
    await svc.deposit(account, Decimal("-3000"))
    assert account.cash_balance == Decimal("102000")


async def test_zero_amount_deposit_rejected(session):
    svc = AccountService(session, settings=_settings())
    account = await svc.create(uuid.uuid4(), "acct", Decimal("100000"))
    with pytest.raises(ValueError):
        await svc.deposit(account, Decimal("0"))


async def test_over_withdrawal_rejected(session):
    svc = AccountService(session, settings=_settings())
    account = await svc.create(uuid.uuid4(), "acct", Decimal("1000"))
    with pytest.raises(ValueError):
        await svc.deposit(account, Decimal("-999999"))
    assert account.cash_balance == Decimal("1000")  # unchanged


async def test_reset_cancels_orders_flattens_positions_restores_balance(session):
    svc = AccountService(session, settings=_settings())
    account = await svc.create(uuid.uuid4(), "acct", Decimal("100000"))

    # Manually open a long position and a resting open order.
    session.add(Position(
        account_id=account.id, instrument_id="AAPL.SIM", symbol="AAPL",
        quantity=Decimal("10"), avg_price=Decimal("100"), realized_pnl=Decimal("0"),
    ))
    session.add(Order(
        account_id=account.id, user_id=account.user_id, instrument_id="AAPL.SIM",
        symbol="AAPL", side="BUY", order_type="LIMIT", quantity=Decimal("5"),
        limit_price=Decimal("1.00"), status=OrderStatus.OPEN,
    ))
    await session.flush()

    # Re-fetch so the positions/orders relationships are loaded.
    account = await AccountRepository(session).get_for_user(account.id, account.user_id)
    await svc.reset(account, Decimal("100000"))
    await session.flush()

    account = await AccountRepository(session).get_for_user(account.id, account.user_id)
    assert all(p.quantity == Decimal("0") for p in account.positions)
    assert all(o.status == OrderStatus.CANCELLED for o in account.orders)
    assert account.cash_balance == Decimal("100000")
    assert account.starting_balance == Decimal("100000")
