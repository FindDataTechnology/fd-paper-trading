"""Repository functions for each aggregate root.

Repositories are thin async wrappers over an ``AsyncSession``. They do not
commit - the caller (service/route) owns the transaction boundary.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import (
    ACTIVE_ORDER_STATUSES,
    Account,
    ApiToken,
    BalanceTransaction,
    Fill,
    Order,
    OrderStatus,
    Position,
    User,
)


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, email: str, password_hash: str) -> User:
        user = User(email=email, password_hash=password_hash)
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        res = await self.session.execute(select(User).where(User.email == email))
        return res.scalar_one_or_none()


class ApiTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, user_id: uuid.UUID, name: str, token_hash: str) -> ApiToken:
        token = ApiToken(user_id=user_id, name=name, token_hash=token_hash)
        self.session.add(token)
        await self.session.flush()
        return token

    async def get_by_hash(self, token_hash: str) -> ApiToken | None:
        res = await self.session.execute(
            select(ApiToken).where(ApiToken.token_hash == token_hash)
        )
        return res.scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> list[ApiToken]:
        res = await self.session.execute(
            select(ApiToken)
            .where(ApiToken.user_id == user_id)
            .order_by(ApiToken.created_at.desc())
        )
        return list(res.scalars().all())

    async def revoke(self, token: ApiToken) -> ApiToken:
        token.revoked_at = datetime.now(timezone.utc)
        await self.session.flush()
        return token

    async def touch(self, token: ApiToken) -> None:
        token.last_used_at = datetime.now(timezone.utc)
        await self.session.flush()


class AccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, account: Account) -> Account:
        self.session.add(account)
        await self.session.flush()
        return account

    async def get_for_user(
        self, account_id: uuid.UUID, user_id: uuid.UUID
    ) -> Account | None:
        """Fetch an account only if it belongs to ``user_id`` (enforces isolation)."""
        res = await self.session.execute(
            select(Account)
            .where(Account.id == account_id, Account.user_id == user_id)
            .options(selectinload(Account.positions), selectinload(Account.orders))
        )
        return res.scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> list[Account]:
        res = await self.session.execute(
            select(Account).where(Account.user_id == user_id).order_by(Account.created_at.desc())
        )
        return list(res.scalars().all())

    async def add_balance_transaction(self, txn: BalanceTransaction) -> None:
        self.session.add(txn)
        await self.session.flush()

    async def count_open_orders(self, account_id: uuid.UUID) -> int:
        res = await self.session.execute(
            select(Order.id)
            .where(Order.account_id == account_id, Order.status.in_(ACTIVE_ORDER_STATUSES))
        )
        return len(res.all())

    async def list_open_orders(self, account_id: uuid.UUID) -> list[Order]:
        res = await self.session.execute(
            select(Order)
            .where(Order.account_id == account_id, Order.status.in_(ACTIVE_ORDER_STATUSES))
            .order_by(Order.created_at.desc())
        )
        return list(res.scalars().all())


class OrderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, order: Order) -> Order:
        self.session.add(order)
        await self.session.flush()
        return order

    async def get_for_user(
        self, order_id: uuid.UUID, user_id: uuid.UUID
    ) -> Order | None:
        res = await self.session.execute(
            select(Order)
            .where(Order.id == order_id, Order.user_id == user_id)
            .options(selectinload(Order.fills))
        )
        return res.scalar_one_or_none()

    async def list_for_account(
        self,
        account_id: uuid.UUID,
        user_id: uuid.UUID,
        status: str | None = None,
    ) -> list[Order]:
        stmt = select(Order).where(
            Order.account_id == account_id, Order.user_id == user_id
        )
        if status is not None:
            stmt = stmt.where(Order.status == status)
        stmt = stmt.order_by(Order.created_at.desc()).options(selectinload(Order.fills))
        res = await self.session.execute(stmt)
        return list(res.scalars().all())


class PositionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_account(
        self, account_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Position]:
        # Join via Account to enforce ownership without a separate lookup.
        res = await self.session.execute(
            select(Position)
            .join(Account, Account.id == Position.account_id)
            .where(Account.id == account_id, Account.user_id == user_id)
            .where(Position.quantity != 0)
            .order_by(Position.symbol.asc())
        )
        return list(res.scalars().all())

    async def get(self, account_id: uuid.UUID, instrument_id: str) -> Position | None:
        res = await self.session.execute(
            select(Position).where(
                Position.account_id == account_id, Position.instrument_id == instrument_id
            )
        )
        return res.scalar_one_or_none()


class FillRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for_account(
        self, account_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Fill]:
        res = await self.session.execute(
            select(Fill)
            .join(Account, Account.id == Fill.account_id)
            .where(Account.id == account_id, Account.user_id == user_id)
            .order_by(Fill.ts.desc())
        )
        return list(res.scalars().all())
