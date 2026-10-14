"""Order endpoints: place, cancel, and query orders under /api/v1/orders."""

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth.dependencies import get_current_user, get_session
from ...execution import OrderRejected, PaperExecutionService
from ...persistence.models import User
from ...persistence.repositories import AccountRepository, OrderRepository
from ..errors import APIError
from ..schemas import OrderCreate, OrderOut

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
        )
    except OrderRejected as exc:
        status_code = 429 if exc.code == "rate_limited" else 400
        raise APIError(exc.code, exc.message, status_code) from exc
    await session.commit()
    # Re-fetch so fills are eager-loaded for the response (fills were added via
    # FK after the order was created, so the in-memory collection is not populated).
    order = await OrderRepository(session).get_for_user(order.id, user.id)
    assert order is not None
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
