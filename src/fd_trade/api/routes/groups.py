"""Admin-managed user groups (design D7): /api/v1/groups, /api/v1/users/{id}/group.

All endpoints require ``is_admin`` (the ``require_admin`` dependency). Groups
carry a ``config`` JSONB whose recognized keys are documented in
``DEFAULT_GROUP_CONFIG`` (max_active_strategies, max_backtest_days,
max_orders_per_day, allow_custom_code, allowed_symbols); unknown keys are
preserved verbatim.
"""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth.dependencies import get_current_user, get_session, require_admin
from ...persistence.models import User, UserGroup
from ...persistence.repositories import UserGroupRepository, UserRepository
from ..errors import APIError
from ..schemas import AssignUserGroup, UserGroupCreate, UserGroupOut, UserGroupUpdate

router = APIRouter(tags=["groups"])


async def _get_group(group_id: uuid.UUID, session: AsyncSession) -> UserGroup:
    group = await UserGroupRepository(session).get_by_id(group_id)
    if group is None:
        raise APIError("not_found", "Group not found", 404)
    return group


@router.get("/groups", response_model=list[UserGroupOut])
async def list_groups(
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> list[UserGroup]:
    return await UserGroupRepository(session).list_all()


@router.post("/groups", response_model=UserGroupOut, status_code=201)
async def create_group(
    payload: UserGroupCreate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> UserGroup:
    repo = UserGroupRepository(session)
    if await repo.get_by_name(payload.name) is not None:
        raise APIError("name_exists", "A group with this name already exists", 409)
    try:
        group = await repo.create(payload.name, payload.config)
    except IntegrityError as exc:  # pragma: no cover - race on unique name
        raise APIError("name_exists", "A group with this name already exists", 409) from exc
    await session.commit()
    return group


@router.patch("/groups/{group_id}", response_model=UserGroupOut)
async def update_group(
    group_id: uuid.UUID,
    payload: UserGroupUpdate,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> UserGroup:
    group = await _get_group(group_id, session)
    if payload.name is not None and payload.name != group.name:
        if await UserGroupRepository(session).get_by_name(payload.name) is not None:
            raise APIError("name_exists", "A group with this name already exists", 409)
        group.name = payload.name
    if payload.config is not None:
        group.config = payload.config
    await session.commit()
    return group


@router.post("/users/{user_id}/group", response_model=UserGroupOut)
async def assign_user_group(
    user_id: uuid.UUID,
    payload: AssignUserGroup,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> UserGroup:
    user = await UserRepository(session).get_by_id(user_id)
    if user is None:
        raise APIError("not_found", "User not found", 404)
    group = await _get_group(payload.group_id, session)
    user.group_id = group.id
    await session.commit()
    return group
