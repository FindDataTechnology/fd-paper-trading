"""Account, position, and order-list endpoints under /api/v1/accounts/*."""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...accounts.schemas import AccountCreate, AccountDeposit, AccountOut, AccountReset, AccountSummary
from ...accounts.service import AccountService
from ...auth.dependencies import get_current_user, get_session
from ...persistence.models import Account, User
from ...persistence.repositories import AccountRepository, OrderRepository, PositionRepository
from ..errors import APIError
from ..schemas import OrderOut, PositionOut

router = APIRouter(tags=["accounts"])


async def _get_owned_account(
    account_id: uuid.UUID,
    user_id: uuid.UUID,
    session: AsyncSession,
) -> Account:
    account = await AccountRepository(session).get_for_user(account_id, user_id)
    if account is None:
        raise APIError("not_found", "Account not found", 404)
    return account


@router.get("/accounts", response_model=list[AccountOut])
async def list_accounts(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[Account]:
    return await AccountRepository(session).list_for_user(user.id)


@router.post("/accounts", response_model=AccountOut, status_code=201)
async def create_account(
    payload: AccountCreate,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Account:
    account = await AccountService(session, settings=request.app.state.settings).create(
        user.id, payload.name, payload.starting_balance
    )
    await session.commit()
    return account


@router.get("/accounts/{account_id}", response_model=AccountSummary)
async def get_account(
    account_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict:
    account = await _get_owned_account(account_id, user.id, session)
    return await AccountService(session, settings=request.app.state.settings).summary(account)


@router.post("/accounts/{account_id}/deposit", response_model=AccountOut)
async def deposit(
    account_id: uuid.UUID,
    payload: AccountDeposit,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Account:
    account = await _get_owned_account(account_id, user.id, session)
    try:
        await AccountService(session, settings=request.app.state.settings).deposit(account, payload.amount)
    except ValueError as exc:
        raise APIError("invalid_amount", str(exc), 400) from exc
    await session.commit()
    return account


@router.post("/accounts/{account_id}/reset", response_model=AccountOut)
async def reset_account(
    account_id: uuid.UUID,
    payload: AccountReset,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Account:
    account = await _get_owned_account(account_id, user.id, session)
    await AccountService(session, settings=request.app.state.settings).reset(account, payload.starting_balance)
    await session.commit()
    return account


@router.get("/accounts/{account_id}/positions", response_model=list[PositionOut])
async def list_positions(
    account_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[PositionOut]:
    await _get_owned_account(account_id, user.id, session)
    return await PositionRepository(session).list_for_account(account_id, user.id)


@router.get("/accounts/{account_id}/orders", response_model=list[OrderOut])
async def list_orders(
    account_id: uuid.UUID,
    status: str | None = None,
    strategy_run_id: uuid.UUID | None = None,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[OrderOut]:
    await _get_owned_account(account_id, user.id, session)
    return await OrderRepository(session).list_for_account(
        account_id, user.id, status, strategy_run_id
    )
