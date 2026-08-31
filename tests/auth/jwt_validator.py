"""Tests for JWT validation (Sprint 7, task 7.A)."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest

from src.auth.jwt_validator import (
    JWT_AUDIENCE,
    JWT_ISSUER,
    AuthError,
    decode_token,
)

SECRET = b"test-signing-secret-bytes"


def _make_token(**overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "user-123",
        "email": "student@example.com",
        "role": "student",
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    claims.update(overrides)
    return jwt.encode(claims, SECRET, algorithm="HS256")


class TestDecodeToken:
    def test_valid_token_decodes(self):
        token = _make_token()
        claims = decode_token(token, SECRET)
        assert claims["sub"] == "user-123"
        assert claims["role"] == "student"

    def test_wrong_secret_rejected(self):
        token = _make_token()
        with pytest.raises(AuthError):
            decode_token(token, b"wrong-secret")

    def test_expired_token_rejected(self):
        now = datetime.now(UTC)
        token = _make_token(iat=now - timedelta(hours=2), exp=now - timedelta(hours=1))
        with pytest.raises(AuthError):
            decode_token(token, SECRET)

    def test_wrong_issuer_rejected(self):
        token = _make_token(iss="someone-else")
        with pytest.raises(AuthError):
            decode_token(token, SECRET)

    def test_wrong_audience_rejected(self):
        token = _make_token(aud="someone-else-api")
        with pytest.raises(AuthError):
            decode_token(token, SECRET)

    def test_missing_sub_rejected(self):
        now = datetime.now(UTC)
        claims = {
            "iss": JWT_ISSUER,
            "aud": JWT_AUDIENCE,
            "iat": now,
            "exp": now + timedelta(hours=1),
        }
        token = jwt.encode(claims, SECRET, algorithm="HS256")
        with pytest.raises(AuthError):
            decode_token(token, SECRET)
