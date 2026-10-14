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
    ACTIVE_RUN_STATUSES,
    Account,
    ApiToken,
    BalanceTransaction,
    Fill,
    Order,
    OrderStatus,
    OrderType,
    Position,
    Strategy,
    StrategyEquityPoint,
    StrategyEvent,
    StrategyRun,
    StrategyRunMode,
    User,
    UserGroup,
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
        self,
        account_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        for_update: bool = False,
        eager: bool = True,
    ) -> Account | None:
        """Fetch an account only if it belongs to ``user_id`` (enforces isolation).

        ``for_update`` takes a ``FOR UPDATE`` row lock so concurrent order
        processing on the same account serializes (no lost cash/position
        updates). ``eager`` (default) also loads positions/orders for views
        that need them; the order path passes ``eager=False`` since it only
        reads ``cash_balance``.
        """
        stmt = select(Account).where(Account.id == account_id, Account.user_id == user_id)
        if eager:
            stmt = stmt.options(selectinload(Account.positions), selectinload(Account.orders))
        if for_update:
            stmt = stmt.with_for_update()
        res = await self.session.execute(stmt)
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
        strategy_run_id: uuid.UUID | None = None,
    ) -> list[Order]:
        stmt = select(Order).where(
            Order.account_id == account_id, Order.user_id == user_id
        )
        if status is not None:
            stmt = stmt.where(Order.status == status)
        if strategy_run_id is not None:
            stmt = stmt.where(Order.strategy_run_id == strategy_run_id)
        stmt = stmt.order_by(Order.created_at.desc()).options(selectinload(Order.fills))
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def count_for_run(self, run_id: uuid.UUID, since: datetime) -> int:
        """Orders placed by ``run_id`` since ``since`` (daily order cap)."""
        res = await self.session.execute(
            select(Order.id).where(
                Order.strategy_run_id == run_id, Order.created_at >= since
            )
        )
        return len(res.all())

    async def list_active_for_run(self, run_id: uuid.UUID) -> list[Order]:
        """Non-terminal orders of a run, oldest first (stop cancels LIMITs)."""
        res = await self.session.execute(
            select(Order)
            .where(
                Order.strategy_run_id == run_id,
                Order.order_type == OrderType.LIMIT,
                Order.status.in_(ACTIVE_ORDER_STATUSES),
            )
            .options(selectinload(Order.fills))
            .order_by(Order.created_at.asc())
        )
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


class UserGroupRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, name: str, config: dict) -> UserGroup:
        group = UserGroup(name=name, config=config)
        self.session.add(group)
        await self.session.flush()
        return group

    async def get_by_id(self, group_id: uuid.UUID) -> UserGroup | None:
        return await self.session.get(UserGroup, group_id)

    async def get_by_name(self, name: str) -> UserGroup | None:
        res = await self.session.execute(
            select(UserGroup).where(UserGroup.name == name)
        )
        return res.scalar_one_or_none()

    async def list_all(self) -> list[UserGroup]:
        res = await self.session.execute(select(UserGroup).order_by(UserGroup.name.asc()))
        return list(res.scalars().all())


class StrategyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, strategy: Strategy) -> Strategy:
        self.session.add(strategy)
        await self.session.flush()
        return strategy

    async def get_for_user(
        self, strategy_id: uuid.UUID, user_id: uuid.UUID
    ) -> Strategy | None:
        res = await self.session.execute(
            select(Strategy).where(
                Strategy.id == strategy_id, Strategy.user_id == user_id
            )
        )
        return res.scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> list[Strategy]:
        res = await self.session.execute(
            select(Strategy)
            .where(Strategy.user_id == user_id)
            .order_by(Strategy.created_at.desc())
        )
        return list(res.scalars().all())

    async def delete(self, strategy: Strategy) -> None:
        await self.session.delete(strategy)
        await self.session.flush()


class StrategyRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, run: StrategyRun) -> StrategyRun:
        self.session.add(run)
        await self.session.flush()
        return run

    async def get_for_user(
        self, run_id: uuid.UUID, user_id: uuid.UUID
    ) -> StrategyRun | None:
        # Join via Strategy to enforce ownership without a separate lookup.
        res = await self.session.execute(
            select(StrategyRun)
            .join(Strategy, Strategy.id == StrategyRun.strategy_id)
            .where(StrategyRun.id == run_id, Strategy.user_id == user_id)
        )
        return res.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        strategy_id: uuid.UUID | None = None,
        mode: str | None = None,
        status: str | None = None,
    ) -> list[StrategyRun]:
        stmt = (
            select(StrategyRun)
            .join(Strategy, Strategy.id == StrategyRun.strategy_id)
            .where(Strategy.user_id == user_id)
        )
        if strategy_id is not None:
            stmt = stmt.where(StrategyRun.strategy_id == strategy_id)
        if mode is not None:
            stmt = stmt.where(StrategyRun.mode == mode)
        if status is not None:
            stmt = stmt.where(StrategyRun.status == status)
        stmt = stmt.order_by(StrategyRun.created_at.desc())
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def list_active(self, mode: str | None = None) -> list[StrategyRun]:
        """All non-terminal runs (used for runner recovery on startup)."""
        stmt = select(StrategyRun).where(StrategyRun.status.in_(ACTIVE_RUN_STATUSES))
        if mode is not None:
            stmt = stmt.where(StrategyRun.mode == mode)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def count_active_paper_for_user(self, user_id: uuid.UUID) -> int:
        """Running paper runs owned by ``user_id`` (max_active_strategies)."""
        res = await self.session.execute(
            select(StrategyRun.id)
            .join(Strategy, Strategy.id == StrategyRun.strategy_id)
            .where(
                Strategy.user_id == user_id,
                StrategyRun.mode == StrategyRunMode.PAPER,
                StrategyRun.status.in_(ACTIVE_RUN_STATUSES),
            )
        )
        return len(res.all())

    async def add_equity_points(self, points: list[StrategyEquityPoint]) -> None:
        self.session.add_all(points)
        await self.session.flush()

    async def list_equity_points(self, run_id: uuid.UUID) -> list[StrategyEquityPoint]:
        res = await self.session.execute(
            select(StrategyEquityPoint)
            .where(StrategyEquityPoint.run_id == run_id)
            .order_by(StrategyEquityPoint.date.asc())
        )
        return list(res.scalars().all())

    async def add_event(
        self, run_id: uuid.UUID, type: str, message: str, data: dict | None = None
    ) -> StrategyEvent:
        event = StrategyEvent(run_id=run_id, type=type, message=message, data=data)
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_events(self, run_id: uuid.UUID) -> list[StrategyEvent]:
        res = await self.session.execute(
            select(StrategyEvent)
            .where(StrategyEvent.run_id == run_id)
            .order_by(StrategyEvent.ts.asc())
        )
        return list(res.scalars().all())
