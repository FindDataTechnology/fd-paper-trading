"""Order endpoints: place, cancel, and query orders under /api/v1/orders."""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth.dependencies import get_current_user, get_session
from ...execution import OrderRejected, PaperExecutionService
from ...persistence.models import User
from ...persistence.repositories import (
    AccountRepository,
    FillRepository,
    OrderRepository,
)
from ..errors import APIError
from ..schemas import FillOut, OrderCreate, OrderOut

router = APIRouter(tags=["orders"])


def _execution_service(request: Request) -> PaperExecutionService:
    return request.app.state.execution_service


async def _broadcast_order(request: Request, user_id: uuid.UUID, order) -> None:
    """Push an ``order`` event (carrying fills) to the owning user over /ws.

    v1 fills are applied synchronously inside ``place_order``, so broadcasting
    the final order after the transaction commits delivers both status and fill
    information. No-op when the realtime manager is absent (e.g. tests).
    """
    manager = getattr(request.app.state, "realtime", None)
    if manager is None:
        return
    payload = OrderOut.model_validate(order).model_dump(mode="json")
    await manager.broadcast_to_user(user_id, {"type": "order", "order": payload})


@router.post("/orders", response_model=OrderOut, status_code=202)
async def place_order(
    payload: OrderCreate,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    exec_service: PaperExecutionService = Depends(_execution_service),
) -> OrderOut:
    # FOR UPDATE serializes concurrent orders on the same account (the ledger
    # read-modify-writes cash/positions); no eager loads needed here.
    account = await AccountRepository(session).get_for_user(
        payload.account_id, user.id, for_update=True, eager=False
    )
    if account is None:
        raise APIError("not_found", "Account not found", 404)
    try:
        order = await exec_service.place_order(
            session,
            account,
            payload.symbol,
            payload.side,
            payload.order_type,
            payload.quantity,
            payload.limit_price,
            payload.client_order_id,
            stop_price=payload.stop_price,
        )
    except OrderRejected as exc:
        status_code = 429 if exc.code == "rate_limited" else 400
        raise APIError(exc.code, exc.message, status_code) from exc
    await session.commit()
    # Re-fetch so fills are eager-loaded for the response (fills were added via
    # FK after the order was created, so the in-memory collection is not populated).
    order = await OrderRepository(session).get_for_user(order.id, user.id)
    assert order is not None
    # A new quote just arrived for this symbol: evaluate this account's resting
    # stops against it (design: 触发评估挂在"新报价到达"时点). Runs after commit
    # so any fills it produces commit in their own transaction.
    try:
        await exec_service.evaluate_open_stops(session, account, symbol=payload.symbol)
        await session.commit()
    except OrderRejected:
        await session.rollback()
    await _broadcast_order(request, user.id, order)
    return order


@router.delete("/orders/{order_id}", response_model=OrderOut)
async def cancel_order(
    order_id: uuid.UUID,
    request: Request,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    exec_service: PaperExecutionService = Depends(_execution_service),
) -> OrderOut:
    order = await OrderRepository(session).get_for_user(order_id, user.id)
    if order is None:
        raise APIError("not_found", "Order not found", 404)
    try:
        await exec_service.cancel_order(session, order)
    except OrderRejected as exc:
        raise APIError(exc.code, exc.message, 400) from exc
    await session.commit()
    await _broadcast_order(request, user.id, order)
    return order


@router.get("/orders/{order_id}", response_model=OrderOut)
async def get_order(
    order_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OrderOut:
    order = await OrderRepository(session).get_for_user(order_id, user.id)
    if order is None:
        raise APIError("not_found", "Order not found", 404)
    return order


@router.get("/accounts/{account_id}/fills", response_model=list[FillOut])
async def list_account_fills(
    account_id: uuid.UUID,
    limit: int = 100,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[FillOut]:
    """账户级成交流水（按时间倒序）。

    为什么单开一条：`GET /orders/{id}` 里已经带 `fills`，但那是**逐单**视角——
    答不了"这个账户这两天到底成交了什么、每笔什么价位"。`FillRepository.list_for_account`
    早就写好了，却一直没有调用方；这条把它接上，而不是另写一份查询。

    账户归属在仓储的 SQL 里判（`Account.user_id = :user_id`）：别人的账户 id
    在此处得到的是空列表，而不是 403——**不确认存在性**，与 orders 的 404 口径
    有意不同（此处不构成按 id 探测他人账户的信号）。
    """
    if limit < 1 or limit > 1000:
        raise APIError("invalid_limit", "limit must be between 1 and 1000", 400)
    rows = await FillRepository(session).list_for_account(account_id, user.id)
    return [FillOut.model_validate(f) for f in rows[:limit]]
