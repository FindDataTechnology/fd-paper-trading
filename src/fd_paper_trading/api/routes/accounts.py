"""Account, position, and order-list endpoints under /api/v1/accounts/*."""

import uuid
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...accounts.schemas import AccountCreate, AccountDeposit, AccountOut, AccountReset, AccountSummary
from ...accounts.service import AccountService
from ...auth.dependencies import get_current_user, get_session
from ...persistence.models import Account, User
from ...persistence.repositories import AccountRepository, OrderRepository, PositionRepository
from ...config import get_settings
from ...persistence.models import Fill
from ..errors import APIError
from ..schemas import DailyPnlGap, DailyPnlOut, OrderOut, PositionOut

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
    return await AccountService(
        session,
        settings=request.app.state.settings,
        provider=request.app.state.market_data_provider,
    ).summary(account)


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


@router.get("/accounts/{account_id}/daily-pnl", response_model=DailyPnlOut)
async def daily_pnl(
    account_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DailyPnlOut:
    """今日已实现 + 以**上一交易日收盘**为基准的未实现（spec: daily-pnl-tracking）。

    三处刻意的口径，都写进返回值而不是留给前端猜：

    * 已实现只算 ``ts >= day_start`` 的成交（``fills.realized_pnl``），
      不是持仓行上的终身累计；
    * 未实现基准是「严格早于今天的最后一根日线 close」，不是成本价；
    * 拿不到基准的持仓**不计入**，并在 ``gaps`` 里点名——绝不回落到成本价冒充；
    * 窗口内 ``realized_pnl IS NULL`` 的成交（本列上线前的历史行）**不当 0**，
      笔数走 ``unattributed_fills``，让"这个数只覆盖有归属的部分"写在返回值里。
    """
    account = await _get_owned_account(account_id, user.id, session)
    settings = get_settings()
    provider = request.app.state.market_data_provider

    now = datetime.now(timezone.utc)
    day_start = datetime.combine(now.date(), time(settings.pnl_day_start_utc_hour), timezone.utc)
    if day_start > now:  # 日界小时还没到：窗口从昨天那个小时起算
        day_start -= timedelta(days=1)

    realized_today = await session.scalar(
        select(func.coalesce(func.sum(Fill.realized_pnl), 0)).where(
            Fill.account_id == account.id,
            Fill.ts >= day_start,
            Fill.ts <= now,
        )
    )
    realized_today = Decimal(str(realized_today or 0))

    # 窗口内从未记录过逐笔归属的成交（本列上线前的历史行，realized_pnl IS NULL）。
    # 单独报数而不当 0 处理：那些成交可能实现过大额盈亏，把它们当保本平仓是编数据。
    unattributed_fills = await session.scalar(
        select(func.count()).select_from(Fill).where(
            Fill.account_id == account.id,
            Fill.ts >= day_start,
            Fill.ts <= now,
            Fill.realized_pnl.is_(None),
        )
    ) or 0

    gaps: list[DailyPnlGap] = []
    unrealized = Decimal("0")
    today = now.date()
    for position in await PositionRepository(session).list_for_account(account.id, user.id):
        if position.quantity == 0:
            continue
        bars = await provider.get_bars(position.symbol, "1d", 10)
        # 严格早于今天：当天的日线可能已在盘中存在（最新一根），不能当"昨收"
        prior = [
            b for b in bars
            if datetime.fromtimestamp(b.ts, tz=timezone.utc).date() < today
        ]
        if not prior:
            gaps.append(DailyPnlGap(symbol=position.symbol, reason="no_previous_close"))
            continue
        baseline = prior[-1].close

        quote = await provider.get_quote(position.symbol)
        if quote is None:
            # 有基准无报价：按基线计 0（不制造虚假盈亏），并点名
            gaps.append(DailyPnlGap(symbol=position.symbol, reason="no_quote"))
            mark = baseline
        else:
            mark = (quote.bid + quote.ask) / 2
        unrealized += position.quantity * (mark - baseline)

    return DailyPnlOut(
        account_id=account.id,
        as_of=now,
        day_start=day_start,
        baseline="previous_close",
        realized_today=realized_today,
        unrealized=unrealized,
        daily_pnl=realized_today + unrealized,
        gaps=gaps,
        unattributed_fills=int(unattributed_fills),
    )


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
