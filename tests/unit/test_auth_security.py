"""Unit tests for password hashing, JWT access tokens, and API tokens.

Passes ``settings`` explicitly to exercise the injected path (no global cache).
"""

import uuid

import pytest

from fd_trade.auth.security import (
    TokenError,
    create_access_token,
    decode_access_token,
    generate_api_token,
    hash_api_token,
    hash_password,
    looks_like_api_token,
    verify_password,
)
from fd_trade.config import Settings


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


def test_password_hash_and_verify():
    h = hash_password("s3cret")
    assert h != "s3cret"
    assert verify_password("s3cret", h) is True
    assert verify_password("wrong", h) is False


def test_jwt_round_trip():
    settings = _settings(jwt_secret="test-secret")
    user_id = uuid.uuid4()
    token = create_access_token(user_id, settings=settings)
    payload = decode_access_token(token, settings=settings)
    assert payload["sub"] == str(user_id)
    assert payload["type"] == "access"


def test_jwt_tampered_token_rejected():
    settings = _settings(jwt_secret="test-secret")
    token = create_access_token(uuid.uuid4(), settings=settings)
    with pytest.raises(TokenError):
        decode_access_token(token[:-2] + "XX", settings=settings)


def test_jwt_wrong_secret_rejected():
    token = create_access_token(uuid.uuid4(), settings=_settings(jwt_secret="secret-a"))
    with pytest.raises(TokenError):
        decode_access_token(token, settings=_settings(jwt_secret="secret-b"))


def test_expired_token_rejected():
    # Negative expiry produces a token whose exp is in the past.
    settings = _settings(jwt_secret="test-secret", jwt_expiry_minutes=-1)
    token = create_access_token(uuid.uuid4(), settings=settings)
    with pytest.raises(TokenError):
        decode_access_token(token, settings=_settings(jwt_secret="test-secret"))


def test_api_token_generation_and_hashing():
    plaintext, token_hash = generate_api_token()
    assert plaintext.startswith("fdt_")
    assert token_hash.startswith("sha256$")
    assert token_hash != plaintext
    assert looks_like_api_token(plaintext) is True
    assert looks_like_api_token("not-a-token") is False
    # Hashing is deterministic.
    assert hash_api_token(plaintext) == token_hash
