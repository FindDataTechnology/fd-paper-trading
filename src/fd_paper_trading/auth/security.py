"""Password hashing, JWT access tokens, and revocable API tokens."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from passlib.context import CryptContext

from ..config import Settings, get_settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---- Passwords ----

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return pwd_context.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


# ---- JWT access tokens ----

def create_access_token(user_id: uuid.UUID, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "iss": settings.jwt_issuer,
        "sub": str(user_id),
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expiry_minutes),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


class TokenError(Exception):
    """Raised when a JWT cannot be decoded or is malformed."""


def decode_access_token(token: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"require": ["exp", "sub", "type"]},
        )
    except JWTError as exc:  # pragma: no cover - exercised via tests
        raise TokenError(str(exc)) from exc
    if payload.get("type") != "access":
        raise TokenError("not an access token")
    return payload


# ---- API tokens (long-lived, revocable) ----

# Prefix lets us distinguish API tokens from JWTs without a DB lookup.
API_TOKEN_PREFIX = "fdt_"


def generate_api_token() -> tuple[str, str]:
    """Return ``(plaintext, hash)``. The plaintext is shown to the user once."""
    raw = API_TOKEN_PREFIX + secrets.token_urlsafe(32)
    return raw, hash_api_token(raw)


def hash_api_token(raw: str) -> str:
    """API tokens are long random strings, so SHA-256 is sufficient."""
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"sha256${digest}"


def looks_like_api_token(token: str) -> bool:
    return token.startswith(API_TOKEN_PREFIX)
