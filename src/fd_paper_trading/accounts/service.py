"""Account management service: creation, balances, summary, reset.

Owns the transaction boundary for account mutations. The fill-engine execution
engine is the live trading cache; Postgres (via this service) is the durable
source of truth.
"""

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..persistence.models import Account, BalanceTransaction, OrderStatus
from ..persistence.repositories import AccountRepository


class AccountService:
    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self.session = session
        self.repo = AccountRepository(session)
        self.settings = settings or get_settings()

    async def create(
        self,
        user_id: uuid.UUID,
        name: str,
        starting_balance: Decimal | None,
    ) -> Account:
        settings = self.settings
        balance = (
            starting_balance if starting_balance is not None else settings.default_starting_balance
        )
        venue = "PAPER-" + uuid.uuid4().hex[:12].upper()
        account = Account(
            user_id=user_id,
            name=name,
            venue=venue,
            currency=settings.account_currency,
            starting_balance=balance,
            cash_balance=balance,
        )
        account = await self.repo.create(account)
        await self.repo.add_balance_transaction(
            BalanceTransaction(
                account_id=account.id,
                type="deposit",
                amount=balance,
                balance_after=balance,
                reference="initial",
            )
        )
        return account

    async def deposit(self, account: Account, amount: Decimal) -> Account:
        if amount == 0:
            raise ValueError("amount must be non-zero")
        new_balance = account.cash_balance + amount
        if new_balance < 0:
            raise ValueError("insufficient cash for withdrawal")
        account.cash_balance = new_balance
        await self.repo.add_balance_transaction(
            BalanceTransaction(
                account_id=account.id,
                type="deposit" if amount > 0 else "withdrawal",
                amount=amount,
                balance_after=new_balance,
            )
        )
        return account

    async def summary(self, account: Account) -> dict:
        open_orders = await self.repo.count_open_orders(account.id)
        open_positions = sum(1 for p in account.positions if p.quantity != 0)
        return {
            "id": account.id,
            "name": account.name,
            "venue": account.venue,
            "currency": account.currency,
            "cash_balance": account.cash_balance,
            "equity": account.cash_balance,  # cash-only until positions are marked to market
            "open_positions": open_positions,
            "open_orders": open_orders,
            "created_at": account.created_at,
        }

    async def reset(
        self,
        account: Account,
        starting_balance: Decimal | None,
    ) -> Account:
        """Cancel open orders, flatten positions, and restore the starting balance.

        Positions are currently flattened at cost basis (zero realized PnL)
        because live market prices are not yet available; once the market-data
        layer is wired, this will flatten at current market. When the execution
        engine is integrated, open orders will also be cancelled on the engine.
        """
        for order in await self.repo.list_open_orders(account.id):
            order.status = OrderStatus.CANCELLED
            order.reject_reason = "account reset"

        # Flatten at cost basis: return each position's cost to cash.
        for pos in list(account.positions):
            if pos.quantity != 0:
                account.cash_balance += pos.quantity * pos.avg_price
                pos.quantity = Decimal("0")

        target = (
            starting_balance if starting_balance is not None else account.starting_balance
        )
        delta = target - account.cash_balance
        account.cash_balance = target
        account.starting_balance = target
        await self.repo.add_balance_transaction(
            BalanceTransaction(
                account_id=account.id,
                type="reset",
                amount=delta,
                balance_after=target,
                reference="account reset",
            )
        )
        await self.session.flush()
        return account
