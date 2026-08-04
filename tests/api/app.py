"""Integration tests for the FastAPI app — auth wiring (Sprint 7).

Exercises ``create_app()`` end-to-end through ``TestClient`` with a fake
chat model (no AWS credentials needed, hard rule #7) and the ``memory``
persistence backend, so every request is fully in-process and fast.

Covers most of the Sprint 7 DoD (ROADMAP-2026-08.md lines ~1065-1072):
- ``POST /ag-ui`` returns 401 without ``Authorization``.
- A JWT signed with the correct iss/aud/secret validates OK end-to-end.
- ``/health`` stays public.
- Store namespaces end up keyed by the *real* ``user_id`` from the JWT, not
  the Sprint 6 placeholder (``DEFAULT_USER_ID``) — proving
  ``runtime.context.user_id`` reached the middlewares that read it.

The "user_a cannot read user_b's thread_id -> 403" and "runtime.context.user_id
available inside a tool" DoD items are covered at the unit level in
``tests/auth/thread_guard.py`` and ``tests/agent/memory_middleware.py``
respectively — see their docstrings. Reproducing them here through the full
HTTP + streaming stack would mostly duplicate those tests without adding
new signal, since ``derive_thread_id`` (task 7.B) namespaces different
users into different derived thread ids by construction: two different
users can never collide on the same ``thread_id`` to begin with, so a true
end-to-end 403 would require deliberately bypassing derivation.
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from langchain_core.messages import AIMessage
from starlette.testclient import TestClient

from src.agent.user_context import DEFAULT_USER_ID
from src.auth import secret_loader
from src.auth.jwt_validator import JWT_AUDIENCE, JWT_ISSUER
from src.config import get_settings

JWT_SECRET = "integration-test-jwt-secret-value"

# langmem's LocalReflectionExecutor has an internal race: if its worker
# thread starts executing a queued reflection at roughly the same moment
# app.py's lifespan calls executor.shutdown(cancel_futures=True) on test
# teardown, the worker can still try to deliver a result/exception to a
# Future that shutdown() already marked cancelled, raising
# concurrent.futures.InvalidStateError inside that background thread.
# It's cosmetic (surfaces only as a pytest warning about an unhandled
# thread exception, never fails an assertion) and upstream, not something
# this project's code controls — see AGENTS.md §1.1 / ROADMAP-2026-08.md
# Sprint 6 notes on ReflectionExecutor's non-daemon worker thread.
pytestmark = pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")


def _make_token(user_id: str = "real-user-789", **overrides: object) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": user_id,
        "email": "student@example.com",
        "role": "student",
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(hours=1),
    }
    claims.update(overrides)
    return jwt.encode(claims, JWT_SECRET, algorithm="HS256")


@pytest.fixture
def client(monkeypatch):
    """A TestClient wired to a fake model and the in-memory persistence profile."""
    from langchain_core.language_models import GenericFakeChatModel

    from src.agent.factory import create_spark_agent as real_create_spark_agent
    from src.memory import build_reflection_executor as real_build_reflection_executor

    monkeypatch.setenv("SPARK_PERSISTENCE_BACKEND", "memory")
    monkeypatch.setenv("SPARK_JWT_SECRET", JWT_SECRET)
    # Long enough that the background reflection timer never actually fires
    # within a test's lifetime: letting it fire and then get cancelled by
    # the lifespan's executor.shutdown() races with langmem's own internal
    # future bookkeeping (an upstream ReflectionExecutor teardown quirk),
    # producing noisy-but-harmless "PytestUnhandledThreadExceptionWarning"
    # output. Not firing it at all sidesteps that race entirely.
    monkeypatch.setenv("SPARK_REFLECTION_DELAY_SECONDS", "3600")
    get_settings.cache_clear()
    secret_loader.clear_cache()

    class ToolCallingFakeChatModel(GenericFakeChatModel):
        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            return self

    def _fake_create_spark_agent(**kwargs):
        kwargs.pop("model", None)
        # itertools.cycle (not a single-item iter) so tests that invoke
        # /ag-ui more than once against the same app instance — e.g.
        # TestRateLimit, which needs several requests to trip the limiter
        # — don't exhaust the fake model's queued responses.
        fake = ToolCallingFakeChatModel(messages=itertools.cycle([AIMessage(content="hola!")]))
        return real_create_spark_agent(model=fake, **kwargs)

    def _fake_build_reflection_executor(store):
        # Real Bedrock creds aren't available here (hard rule #7); inject a
        # fake model so the background extractor never actually calls AWS,
        # instead of letting it fail noisily on a background thread after
        # the test process moves on. Needs bind_tools support since langmem
        # (trustcall) always binds a tool for structured extraction.
        fake = ToolCallingFakeChatModel(messages=iter([AIMessage(content="{}")]))
        return real_build_reflection_executor(store, model=fake)

    monkeypatch.setattr("src.api.app.create_spark_agent", _fake_create_spark_agent)
    monkeypatch.setattr("src.api.app.build_reflection_executor", _fake_build_reflection_executor)

    from src.api.app import create_app

    with TestClient(create_app()) as test_client:
        yield test_client

    get_settings.cache_clear()
    secret_loader.clear_cache()


def _ag_ui_body(thread_id: str = "client-thread-1") -> dict:
    return {
        "threadId": thread_id,
        "runId": "run-1",
        "state": {},
        "messages": [{"id": "m1", "role": "user", "content": "hola"}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


class TestHealthIsPublic:
    def test_health_does_not_require_auth(self, client):
        response = client.get("/health")
        assert response.status_code == 200


class TestSecurityHeaders:
    """Sprint 7, task 7.E.2 — every response carries the fixed header set."""

    def test_security_headers_present_on_public_route(self, client):
        response = client.get("/health")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "no-referrer"

    def test_security_headers_present_on_401_response(self, client):
        response = client.post("/ag-ui", json=_ag_ui_body())
        assert response.status_code == 401
        assert response.headers["x-content-type-options"] == "nosniff"


class TestRateLimit:
    """Sprint 7, task 7.E.3 — per-user_id burst limiter on POST /ag-ui."""

    def test_exceeding_the_per_minute_limit_returns_429(self, client, monkeypatch):
        monkeypatch.setenv("SPARK_RATE_LIMIT_PER_MINUTE", "2")
        get_settings.cache_clear()
        token = _make_token(user_id="rate-limit-user")

        for _ in range(2):
            response = client.post(
                "/ag-ui",
                json=_ag_ui_body(),
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 200

        response = client.post(
            "/ag-ui",
            json=_ag_ui_body(),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 429

        from src.api.rate_limit import limiter

        limiter.reset()
        get_settings.cache_clear()


class TestAgUiRequiresAuth:
    def test_missing_authorization_header_is_401(self, client):
        response = client.post("/ag-ui", json=_ag_ui_body())
        assert response.status_code == 401

    def test_invalid_signature_is_401(self, client):
        bad_token = jwt.encode(
            {
                "sub": "user-x",
                "iss": JWT_ISSUER,
                "aud": JWT_AUDIENCE,
                "iat": datetime.now(UTC),
                "exp": datetime.now(UTC) + timedelta(hours=1),
            },
            "wrong-secret",
            algorithm="HS256",
        )
        response = client.post(
            "/ag-ui",
            json=_ag_ui_body(),
            headers={"Authorization": f"Bearer {bad_token}"},
        )
        assert response.status_code == 401

    def test_expired_token_is_401(self, client):
        now = datetime.now(UTC)
        expired = _make_token(iat=now - timedelta(hours=2), exp=now - timedelta(hours=1))
        response = client.post(
            "/ag-ui",
            json=_ag_ui_body(),
            headers={"Authorization": f"Bearer {expired}"},
        )
        assert response.status_code == 401


class TestAgUiHappyPath:
    def test_valid_token_is_accepted(self, client):
        token = _make_token()
        response = client.post(
            "/ag-ui",
            json=_ag_ui_body(),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

    def test_thread_owner_is_registered_under_the_real_user_id(self, client):
        """Proves runtime.context.user_id (from the JWT) reached the store,
        not the Sprint 6 DEFAULT_USER_ID placeholder.
        """
        token = _make_token(user_id="real-user-789")
        response = client.post(
            "/ag-ui",
            json=_ag_ui_body(thread_id="some-client-thread"),
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200

        from src.api.app import AG_UI_PATH  # noqa: F401 (documents the endpoint under test)
        from src.auth.thread_guard import THREAD_OWNER_NAMESPACE, derive_thread_id

        store = client.app.state.store
        derived_id = derive_thread_id("real-user-789", "some-client-thread")
        item = store.get(THREAD_OWNER_NAMESPACE, derived_id)
        assert item is not None
        assert item.value["user_id"] == "real-user-789"
        assert item.value["user_id"] != DEFAULT_USER_ID
