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
import json
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


def _sse_events(raw: str) -> list[dict]:
    """Parse the ``data:`` lines of an SSE body into AG-UI event dicts.

    Lines that don't start with ``data:`` — the keep-alive comments from
    src/api/sse.py among them — are dropped exactly as a conforming
    client would drop them.
    """
    return [
        json.loads(line.removeprefix("data: "))
        for line in raw.splitlines()
        if line.startswith("data: ")
    ]


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


class TestStreamHygiene:
    """The stream is the product. Anything that reaches the browser as a
    TEXT_MESSAGE_* sequence is rendered to a student as the advisor
    speaking, so internal model calls must not appear there.
    """

    def test_only_the_real_reply_is_streamed_as_a_message(self, client):
        """ContentFilterMiddleware calls a model inside the graph on every
        turn. Its tokens travel the same astream_events channel as the
        answer, so without an explicit opt-out the safety verdict is
        emitted as its own complete assistant message before the reply.

        This asserts exactly one message sequence per turn. Reverting the
        `emit-messages: False` config in src/agent/content_filter.py makes
        it fail with two.
        """
        response = client.post(
            "/ag-ui",
            json=_ag_ui_body(thread_id="hygiene-thread"),
            headers={"Authorization": f"Bearer {_make_token()}"},
        )
        assert response.status_code == 200

        types = [event["type"] for event in _sse_events(response.text)]

        assert types.count("TEXT_MESSAGE_START") == 1
        assert types.count("TEXT_MESSAGE_END") == 1

    def test_stream_still_parses_with_the_keepalive_wrapper_in_place(self, client):
        """The keep-alive injects raw bytes into the SSE body. If it ever
        emitted something other than a comment line, every event after it
        would be corrupted -- so assert the envelope still parses and the
        run brackets are intact."""
        response = client.post(
            "/ag-ui",
            json=_ag_ui_body(thread_id="keepalive-thread"),
            headers={"Authorization": f"Bearer {_make_token()}"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")

        types = [event["type"] for event in _sse_events(response.text)]

        assert types[0] == "RUN_STARTED"
        assert types[-1] == "RUN_FINISHED"


class TestThreadsApi:
    """Sesiones de chat: listar, releer y borrar.

    El streaming por si solo no es un producto de chat: al recargar la
    pagina el navegador no tiene con que repoblar la conversacion, la
    barra lateral no tiene que listar, y una conversacion no se puede
    borrar. Los datos siempre estuvieron ahi -- el checkpointer guarda cada
    turno y el store el indice -- lo que faltaba era la puerta.
    """

    def _post_turn(self, client, thread_id, token, text="hola"):
        body = _ag_ui_body(thread_id=thread_id)
        body["messages"] = [{"id": "m1", "role": "user", "content": text}]
        return client.post("/ag-ui", json=body, headers={"Authorization": f"Bearer {token}"})

    def test_requires_auth(self, client):
        assert client.get("/threads").status_code == 401

    def test_lists_conversations_after_a_turn(self, client):
        token = _make_token(user_id="u-list")
        self._post_turn(client, "chat-1", token, "¿qué carreras van con matemáticas?")

        response = client.get("/threads", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        threads = response.json()["threads"]
        assert len(threads) == 1
        assert threads[0]["thread_id"] == "chat-1"
        assert threads[0]["title"] == "¿qué carreras van con matemáticas?"

    def test_never_exposes_the_derived_thread_id(self, client):
        """Es la clave del checkpointer; el cliente direcciona por la suya."""
        token = _make_token(user_id="u-derived")
        self._post_turn(client, "chat-1", token)

        [thread] = client.get("/threads", headers={"Authorization": f"Bearer {token}"}).json()[
            "threads"
        ]

        assert "derived_thread_id" not in thread

    def test_one_user_never_sees_another_users_conversations(self, client):
        token_a = _make_token(user_id="user-a")
        token_b = _make_token(user_id="user-b")
        self._post_turn(client, "chat-a", token_a, "conversacion de A")
        self._post_turn(client, "chat-b", token_b, "conversacion de B")

        threads_a = client.get("/threads", headers={"Authorization": f"Bearer {token_a}"}).json()[
            "threads"
        ]

        assert [t["thread_id"] for t in threads_a] == ["chat-a"]

    def test_rehydrates_the_message_history(self, client):
        token = _make_token(user_id="u-history")
        self._post_turn(client, "chat-1", token, "mi pregunta")

        response = client.get(
            "/threads/chat-1/messages", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        messages = response.json()["messages"]
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "mi pregunta"
        assert any(m["role"] == "assistant" for m in messages)

    def test_history_never_includes_system_messages(self, client):
        """ProfileHydrationMiddleware inyecta el perfil del estudiante como
        SystemMessage y se persiste en el mismo canal."""
        token = _make_token(user_id="u-nosys")
        self._post_turn(client, "chat-1", token)

        messages = client.get(
            "/threads/chat-1/messages", headers={"Authorization": f"Bearer {token}"}
        ).json()["messages"]

        assert all(m["role"] in {"user", "assistant"} for m in messages)

    def test_history_of_an_unknown_thread_is_empty(self, client):
        token = _make_token(user_id="u-unknown")

        response = client.get(
            "/threads/never-existed/messages", headers={"Authorization": f"Bearer {token}"}
        )

        assert response.status_code == 200
        assert response.json()["messages"] == []

    def test_delete_removes_the_conversation_and_its_history(self, client):
        token = _make_token(user_id="u-delete")
        self._post_turn(client, "chat-1", token, "algo que quiero borrar")
        headers = {"Authorization": f"Bearer {token}"}

        assert client.delete("/threads/chat-1", headers=headers).status_code == 204

        assert client.get("/threads", headers=headers).json()["threads"] == []
        assert client.get("/threads/chat-1/messages", headers=headers).json()["messages"] == []

    def test_the_same_client_id_is_reusable_after_a_delete(self, client):
        """El borrado libera tambien el registro de ownership: si no, el id
        derivado quedaria reclamado para siempre y el estudiante recibiria
        un 403 sobre su propio hilo."""
        token = _make_token(user_id="u-reuse")
        headers = {"Authorization": f"Bearer {token}"}
        self._post_turn(client, "chat-1", token, "primera vida")
        client.delete("/threads/chat-1", headers=headers)

        response = self._post_turn(client, "chat-1", token, "segunda vida")

        assert response.status_code == 200
        [thread] = client.get("/threads", headers=headers).json()["threads"]
        assert thread["title"] == "segunda vida"


class TestRawEventsAreNotStreamed:
    """ag_ui_langgraph emits a RawEvent for EVERY LangGraph event, with no
    upstream flag to turn it off. Those carry the verbatim internals — the
    coordinator's system prompt, the content-safety classifier's prompt,
    every intermediate state.

    Measured on a real dev turn before this was filtered: 168 of 297
    events, 60% of the stream's bytes. Registration is public, so any
    student could read exactly what the safety filter checks for.
    """

    def test_raw_events_never_reach_the_client(self, client):
        response = client.post(
            "/ag-ui",
            json=_ag_ui_body(thread_id="raw-thread"),
            headers={"Authorization": f"Bearer {_make_token(user_id='u-raw-off')}"},
        )

        types = [event["type"] for event in _sse_events(response.text)]

        # Asserted first on purpose: "RAW not in []" is vacuously true, so
        # without this the test would pass just as happily on an empty
        # stream -- which is exactly what a 429 from the shared per-user
        # rate limiter produces.
        assert types, "the stream was empty; the assertion below would be vacuous"
        assert "RAW" not in types

    def test_the_typed_protocol_events_still_flow(self, client):
        """The filter must not take the actual protocol with it."""
        response = client.post(
            "/ag-ui",
            json=_ag_ui_body(thread_id="typed-thread"),
            headers={"Authorization": f"Bearer {_make_token(user_id='u-raw-typed')}"},
        )

        types = {event["type"] for event in _sse_events(response.text)}

        assert {"RUN_STARTED", "RUN_FINISHED", "TEXT_MESSAGE_CONTENT"} <= types

    def test_raw_events_can_be_re_enabled_for_local_debugging(self, client, monkeypatch):
        monkeypatch.setenv("SPARK_SSE_EMIT_RAW_EVENTS", "true")
        get_settings.cache_clear()

        response = client.post(
            "/ag-ui",
            json=_ag_ui_body(thread_id="raw-on-thread"),
            headers={"Authorization": f"Bearer {_make_token(user_id='u-raw-on')}"},
        )

        types = [event["type"] for event in _sse_events(response.text)]

        assert "RAW" in types
