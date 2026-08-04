"""Tests for the ``require_auth`` FastAPI dependency (Sprint 7, task 7.A)."""

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from src.auth import secret_loader
from src.auth.dependencies import require_auth
from src.auth.jwt_validator import JWT_AUDIENCE, JWT_ISSUER
from src.config import get_settings

SECRET = "unit-test-jwt-secret"


def _make_token(secret: str = SECRET, **overrides: object) -> str:
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
    return jwt.encode(claims, secret, algorithm="HS256")


def _bare_request(aws_event: dict | None = None) -> Request:
    scope: dict = {"type": "http", "headers": []}
    if aws_event is not None:
        scope["aws.event"] = aws_event
    return Request(scope)


@pytest.fixture(autouse=True)
def _configure_jwt_secret(monkeypatch):
    monkeypatch.setenv("SPARK_JWT_SECRET", SECRET)
    get_settings.cache_clear()
    secret_loader.clear_cache()
    yield
    get_settings.cache_clear()
    secret_loader.clear_cache()


class TestRequireAuthBearerPath:
    async def test_missing_credentials_raises_401(self):
        with pytest.raises(HTTPException) as exc_info:
            await require_auth(_bare_request(), None)
        assert exc_info.value.status_code == 401

    async def test_valid_token_resolves_auth_context(self):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=_make_token())
        auth = await require_auth(_bare_request(), creds)
        assert auth.user_id == "user-123"
        assert auth.email == "student@example.com"
        assert auth.role == "student"

    async def test_wrong_signature_raises_401(self):
        creds = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=_make_token(secret="not-the-real-secret")
        )
        with pytest.raises(HTTPException) as exc_info:
            await require_auth(_bare_request(), creds)
        assert exc_info.value.status_code == 401

    async def test_expired_token_raises_401(self):
        now = datetime.now(UTC)
        creds = HTTPAuthorizationCredentials(
            scheme="Bearer",
            credentials=_make_token(iat=now - timedelta(hours=2), exp=now - timedelta(hours=1)),
        )
        with pytest.raises(HTTPException) as exc_info:
            await require_auth(_bare_request(), creds)
        assert exc_info.value.status_code == 401

    async def test_non_bearer_scheme_raises_401(self):
        creds = HTTPAuthorizationCredentials(scheme="Basic", credentials="dXNlcjpwYXNz")
        with pytest.raises(HTTPException) as exc_info:
            await require_auth(_bare_request(), creds)
        assert exc_info.value.status_code == 401

    async def test_unrecognized_role_falls_back_to_default(self):
        creds = HTTPAuthorizationCredentials(
            scheme="Bearer", credentials=_make_token(role="superadmin")
        )
        auth = await require_auth(_bare_request(), creds)
        assert auth.role == "student"


class TestRequireAuthLambdaAuthorizerPath:
    async def test_authorizer_context_takes_precedence(self):
        aws_event = {
            "requestContext": {
                "authorizer": {"lambda": {"sub": "user-456", "email": "a@b.com", "role": "admin"}}
            }
        }
        auth = await require_auth(_bare_request(aws_event), None)
        assert auth.user_id == "user-456"
        assert auth.role == "admin"

    async def test_missing_sub_in_authorizer_context_raises_401(self):
        aws_event = {"requestContext": {"authorizer": {"lambda": {"role": "admin"}}}}
        with pytest.raises(HTTPException) as exc_info:
            await require_auth(_bare_request(aws_event), None)
        assert exc_info.value.status_code == 401
