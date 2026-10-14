"""FastAPI dependencies for authentication and per-request sessions."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..persistence.base import Database
from ..persistence.models import User
from ..persistence.repositories import ApiTokenRepository, UserRepository
from .security import (
    TokenError,
    decode_access_token,
    hash_api_token,
    looks_like_api_token,
)

_AUTH_HEADER = "Authorization"


async def get_session(request: Request) -> AsyncSession:
    """Yield a session from the app-level ``Database`` and close it after."""
    db: Database = request.app.state.db
    async with db.session_factory() as session:
        yield session


def _bearer_token(request: Request) -> str | None:
    header = request.headers.get(_AUTH_HEADER, "")
    if header.startswith("Bearer "):
        return header[len("Bearer ") :].strip()
    return None


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    """Resolve the requesting user from a JWT or API token.

    Accepts either a JWT access token or a long-lived API token in the
    ``Authorization: Bearer <token>`` header. Returns ``401`` otherwise.
    """
    token = _bearer_token(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user: User | None = None

    if looks_like_api_token(token):
        user = await _user_from_api_token(session, token)
    else:
        user = await _user_from_jwt(session, token)

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def _user_from_jwt(session: AsyncSession, token: str) -> User | None:
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
    except (TokenError, ValueError, KeyError):
        return None
    return await UserRepository(session).get_by_id(user_id)


async def _user_from_api_token(session: AsyncSession, token: str) -> User | None:
    repo = ApiTokenRepository(session)
    stored = await repo.get_by_hash(hash_api_token(token))
    if stored is None or stored.revoked_at is not None:
        return None
    # last_used_at is only a diagnostics field; rewrite it at most every
    # N minutes so high-frequency API-token traffic isn't a write per request.
    last = stored.last_used_at
    if last is None or datetime.now(timezone.utc) - last > timedelta(
        minutes=get_settings().api_token_touch_minutes
    ):
        await repo.touch(stored)
    return await UserRepository(session).get_by_id(stored.user_id)


def require_user(user: User = Depends(get_current_user)) -> User:
    """Convenience alias for routes that only need an authenticated user."""
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Require an admin user (403 otherwise)."""
    if not user.is_admin:
        from ..api.errors import APIError

        raise APIError("forbidden", "Admin access required", 403)
    return user
