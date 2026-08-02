"""Pydantic request/response schemas for authentication."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

# Lightweight email pattern (avoids an extra `email-validator` dependency).
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class UserRegister(BaseModel):
    email: str = Field(pattern=_EMAIL_PATTERN, max_length=320)
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    email: str = Field(pattern=_EMAIL_PATTERN, max_length=320)
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: uuid.UUID


class ApiTokenCreate(BaseModel):
    name: str = Field(default="default", min_length=1, max_length=120)


class ApiTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    # Only populated on creation; never returned by list endpoints.
    token: str | None = None


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    created_at: datetime
