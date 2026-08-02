"""Auth and API-token endpoints: /api/v1/auth/* and /api/v1/tokens/*."""

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth.dependencies import get_current_user, get_session
from ...auth.schemas import (
    ApiTokenCreate,
    ApiTokenOut,
    TokenResponse,
    UserLogin,
    UserOut,
    UserRegister,
)
from ...auth.security import (
    create_access_token,
    generate_api_token,
    hash_password,
    verify_password,
)
from ...config import get_settings
from ...persistence.models import User
from ...persistence.repositories import ApiTokenRepository, UserRepository
from ..errors import APIError

router = APIRouter(tags=["auth"])


@router.post("/auth/register", response_model=UserOut, status_code=201)
async def register(
    payload: UserRegister,
    session: AsyncSession = Depends(get_session),
) -> User:
    repo = UserRepository(session)
    if await repo.get_by_email(payload.email) is not None:
        raise APIError("email_exists", "A user with this email already exists", 409)
    user = await repo.create(payload.email, hash_password(payload.password))
    await session.commit()
    return user


@router.post("/auth/login", response_model=TokenResponse)
async def login(
    payload: UserLogin,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    user = await UserRepository(session).get_by_email(payload.email)
    if user is None or not verify_password(payload.password, user.password_hash):
        raise APIError("invalid_credentials", "Invalid email or password", 401)
    if not user.is_active:
        raise APIError("user_inactive", "User account is inactive", 403)
    token = create_access_token(user.id)
    return TokenResponse(
        access_token=token,
        expires_in=get_settings().jwt_expiry_minutes * 60,
        user_id=user.id,
    )


@router.post("/tokens", response_model=ApiTokenOut, status_code=201)
async def create_token(
    payload: ApiTokenCreate,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ApiTokenOut:
    plaintext, token_hash = generate_api_token()
    token = await ApiTokenRepository(session).create(user.id, payload.name, token_hash)
    await session.commit()
    return ApiTokenOut(
        id=token.id,
        name=token.name,
        created_at=token.created_at,
        token=plaintext,
    )


@router.get("/tokens", response_model=list[ApiTokenOut])
async def list_tokens(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[ApiTokenOut]:
    tokens = await ApiTokenRepository(session).list_for_user(user.id)
    return [
        ApiTokenOut(
            id=t.id,
            name=t.name,
            created_at=t.created_at,
            last_used_at=t.last_used_at,
            revoked_at=t.revoked_at,
        )
        for t in tokens
    ]


@router.delete("/tokens/{token_id}", status_code=204)
async def revoke_token(
    token_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    repo = ApiTokenRepository(session)
    tokens = await repo.list_for_user(user.id)
    match = next((t for t in tokens if t.id == token_id), None)
    if match is None:
        raise APIError("not_found", "API token not found", 404)
    await repo.revoke(match)
    await session.commit()
