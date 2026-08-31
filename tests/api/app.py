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
from src.api.app import STREAM_FAILURE_MESSAGE
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

    def _rename(self, client, client_thread_id, token, title):
        return client.patch(
            f"/threads/{client_thread_id}",
            json={"title": title},
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_renames_a_conversation(self, client):
        """El titulo automatico sirve para reconocer una conversacion recien
        tenida, no para encontrarla dentro de tres semanas entre otras diez
        que empiezan igual."""
        token = _make_token(user_id="u-rename")
        self._post_turn(client, "chat-1", token, "hola que tal")

        response = self._rename(client, "chat-1", token, "Mis opciones en Arequipa")

        assert response.status_code == 200
        assert response.json()["title"] == "Mis opciones en Arequipa"

    def test_the_listing_shows_the_new_name(self, client):
        token = _make_token(user_id="u-rename2")
        headers = {"Authorization": f"Bearer {token}"}
        self._post_turn(client, "chat-1", token, "hola")

        self._rename(client, "chat-1", token, "Becas y costos")

        [thread] = client.get("/threads", headers=headers).json()["threads"]
        assert thread["title"] == "Becas y costos"

    def test_the_next_turn_does_not_overwrite_it(self, client):
        token = _make_token(user_id="u-rename3")
        headers = {"Authorization": f"Bearer {token}"}
        self._post_turn(client, "chat-1", token, "hola")
        self._rename(client, "chat-1", token, "Becas y costos")

        self._post_turn(client, "chat-1", token, "otra pregunta")

        [thread] = client.get("/threads", headers=headers).json()["threads"]
        assert thread["title"] == "Becas y costos"

    def test_never_exposes_the_derived_id_when_renaming(self, client):
        token = _make_token(user_id="u-rename4")
        self._post_turn(client, "chat-1", token, "hola")

        body = self._rename(client, "chat-1", token, "Otro nombre").json()

        assert "derived_thread_id" not in body
        assert body["thread_id"] == "chat-1"

    def test_an_empty_title_is_refused(self, client):
        token = _make_token(user_id="u-rename5")
        self._post_turn(client, "chat-1", token, "hola")

        assert self._rename(client, "chat-1", token, "   ").status_code == 422

    def test_a_conversation_that_never_happened_is_a_404(self, client):
        # No se crea la entrada aqui: se crea al hablar, y esto no es hablar.
        token = _make_token(user_id="u-rename6")

        assert self._rename(client, "nunca-existio", token, "algo").status_code == 404

    def test_renaming_requires_auth(self, client):
        assert client.patch("/threads/chat-1", json={"title": "algo"}).status_code == 401

    def test_delete_removes_the_conversation_and_its_history(self, client):
        token = _make_token(user_id="u-delete")
        self._post_turn(client, "chat-1", token, "algo que quiero borrar")
        headers = {"Authorization": f"Bearer {token}"}

        assert client.delete("/threads/chat-1", headers=headers).status_code == 204

        assert client.get("/threads", headers=headers).json()["threads"] == []
        assert client.get("/threads/chat-1/messages", headers=headers).json()["messages"] == []

    def _ocupa(self, client, user_id, client_thread_id, run_id="run-de-la-otra-pestaña"):
        """Deja un turno «en curso» sobre esa conversacion.

        Se planta el arrendamiento en el store en vez de lanzar un turno de
        verdad en paralelo: TestClient es sincrono y consume el stream
        entero, asi que un turno real siempre habria soltado antes de que
        empezara el segundo -- que es justo lo que no se quiere probar.
        """
        from src.auth import derive_thread_id
        from src.threads.lease import RUN_LEASE_NAMESPACE

        now = datetime.now(UTC)
        client.app.state.store.put(
            RUN_LEASE_NAMESPACE,
            derive_thread_id(user_id, client_thread_id),
            {
                "run_id": run_id,
                "started_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=5)).isoformat(),
            },
        )

    def test_a_second_turn_on_a_busy_conversation_is_refused(self, client):
        """Dos pestañas sobre el mismo hilo se pisaban EN SILENCIO.

        El checkpointer no tiene control de concurrencia: las dos leen el
        mismo estado, las dos escriben, y los mensajes de la que termina
        antes dejan de estar en el camino desde la cabeza.
        """
        token = _make_token(user_id="u-busy")
        self._post_turn(client, "chat-1", token, "primera pregunta")
        self._ocupa(client, "u-busy", "chat-1")

        response = self._post_turn(client, "chat-1", token, "segunda pregunta")

        assert response.status_code == 409

    def test_another_conversation_is_not_blocked_by_it(self, client):
        token = _make_token(user_id="u-busy2")
        self._post_turn(client, "chat-1", token)
        self._ocupa(client, "u-busy2", "chat-1")

        assert self._post_turn(client, "chat-2", token).status_code == 200

    def test_a_finished_turn_leaves_the_conversation_free(self, client):
        """El arrendamiento se suelta en el `finally` del stream."""
        token = _make_token(user_id="u-serial")
        assert self._post_turn(client, "chat-1", token, "una").status_code == 200

        assert self._post_turn(client, "chat-1", token, "y otra").status_code == 200

    def test_the_history_reports_a_turn_in_flight(self, client):
        """Es lo que deja al frontend decir «estoy respondiendo» en vez de
        enseñar la pregunta sin respuesta y provocar que se repita."""
        token = _make_token(user_id="u-running")
        self._post_turn(client, "chat-1", token)
        self._ocupa(client, "u-running", "chat-1")

        body = client.get(
            "/threads/chat-1/messages", headers={"Authorization": f"Bearer {token}"}
        ).json()

        assert body["running"] is True

    def test_a_quiet_conversation_is_not_running(self, client):
        token = _make_token(user_id="u-quiet")
        self._post_turn(client, "chat-1", token)

        body = client.get(
            "/threads/chat-1/messages", headers={"Authorization": f"Bearer {token}"}
        ).json()

        assert body["running"] is False

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


class TestATurnThatDiesMidStream:
    """Un fallo dentro del grafo tiene que llegar al navegador como evento.

    Antes no llegaba: la excepcion salia como "Exception in ASGI
    application" y el SSE se cortaba sin emitir nada. El frontend terminaba
    su bucle sin error, asi que el estudiante veia su pregunta y despues
    nada -- ni respuesta, ni aviso. Medido en dev el 2026-08-08 con un
    historial que Bedrock rechazaba: la persona reintento cuatro veces
    seguidas porque no habia forma de saber que estaba fallando.
    """

    @staticmethod
    def _exploding_agent(boom: str):
        from ag_ui.core.events import RunStartedEvent

        class ExplodingAgent:
            """Arranca el turno y revienta a mitad, como el fallo real."""

            config: dict = {}

            def clone(self):
                return self

            async def run(self, _input):
                yield RunStartedEvent(thread_id="t", run_id="r")
                raise RuntimeError(boom)

        return ExplodingAgent()

    def test_emits_run_error_instead_of_ending_the_stream_in_silence(self, client):
        client.app.state.langgraph_agent = self._exploding_agent("boom")

        response = client.post(
            "/ag-ui",
            json=_ag_ui_body(thread_id="dead-turn"),
            headers={"Authorization": f"Bearer {_make_token(user_id='u-dead-turn')}"},
        )

        types = [event["type"] for event in _sse_events(response.text)]

        assert response.status_code == 200
        assert types == ["RUN_STARTED", "RUN_ERROR"]

    def test_the_student_never_reads_the_internals_of_the_failure(self, client):
        # El mensaje real de este fallo lleva ids internos y el indice del
        # mensaje del historial. Es exactamente lo que no debe salir por
        # pantalla, por la misma razon por la que se filtran los RAW.
        detalle = (
            "messages.22: `tool_use` ids were found without `tool_result` "
            "blocks immediately after: toolu_bdrk_013v2T9o6kDS2QarNA7DVroF"
        )
        client.app.state.langgraph_agent = self._exploding_agent(detalle)

        response = client.post(
            "/ag-ui",
            json=_ag_ui_body(thread_id="dead-turn-detail"),
            headers={"Authorization": f"Bearer {_make_token(user_id='u-dead-detail')}"},
        )

        error = next(e for e in _sse_events(response.text) if e["type"] == "RUN_ERROR")

        assert error["message"] == STREAM_FAILURE_MESSAGE
        assert "toolu_bdrk" not in response.text
        assert "messages.22" not in response.text


class TestProfileDoesNotPolluteTheMessageList:
    """Regression for a failure that only appeared once memory worked.

    ProfileHydrationMiddleware used to append the student's profile to
    state as a SystemMessage. Nothing complained until a student actually
    had an extracted profile, and then every turn died before the model:

        ValueError: Received multiple non-consecutive system messages.
        During task with name 'model'

    langchain_aws's Anthropic adapter requires system messages to be
    contiguous at the front, and appending puts the profile behind the
    human turn. The adapter is not exercisable here (no Bedrock in CI), so
    this pins the invariant that caused it instead: after a real turn
    through the real graph, the persisted history holds no SystemMessage.
    """

    def test_no_system_message_is_persisted_into_the_history(self, client):
        import anyio

        from src.auth.thread_guard import derive_thread_id

        user_id = "u-profile-pollution"
        store = client.app.state.store
        store.put(
            ("spark-match", user_id, "profile"),
            "profile",
            {"name": "Juan", "realistic": 8},
        )

        response = client.post(
            "/ag-ui",
            json=_ag_ui_body(thread_id="profile-thread"),
            headers={"Authorization": f"Bearer {_make_token(user_id=user_id)}"},
        )
        assert response.status_code == 200

        async def read_state():
            snapshot = await client.app.state.graph.aget_state(
                {"configurable": {"thread_id": derive_thread_id(user_id, "profile-thread")}}
            )
            return (snapshot.values or {}).get("messages") or []

        messages = anyio.run(read_state)

        assert messages, "the turn produced no persisted messages"
        assert not any(m.type == "system" for m in messages)
        assert not any("Perfil vocacional" in str(m.content) for m in messages)


class TestElArranqueDiceSiPuedeEmitirInformes:
    """El aviso de `comprobar_el_render_de_pdf` (ADR-019, D11).

    Sin esto, una imagen a la que le falten pango/cairo arranca sana, pasa el
    health check y conversa con normalidad; lo unico que no puede hacer es
    emitir un informe, y eso no se descubre hasta que un estudiante pide el
    suyo. Y como `upload_report` renderiza antes de subir nada, ese estudiante
    no se queda sin PDF: se queda sin informe.

    El caso malo se prueba forzandolo, porque el runner de CI SI tiene las
    bibliotecas -- por eso los tests de `tests/reports/pdf.py` no se saltan
    alli -- y sin forzarlo esta rama no se ejercitaria nunca en el unico sitio
    donde importa que este probada.
    """

    @pytest.fixture
    def sin_render_de_pdf(self, monkeypatch):
        """Una imagen a la que le faltan las bibliotecas nativas."""
        from src.api import app as app_module

        monkeypatch.setattr(app_module, "pdf_rendering_available", lambda: False)

    def test_lo_avisa_en_error_cuando_no_se_puede_renderizar(self, monkeypatch, caplog):
        import logging as _logging

        from src.api import app as app_module

        monkeypatch.setattr(app_module, "pdf_rendering_available", lambda: False)

        with caplog.at_level(_logging.INFO, logger=app_module.__name__):
            disponible = app_module.comprobar_el_render_de_pdf()

        assert disponible is False
        errores = [r for r in caplog.records if r.levelno == _logging.ERROR]
        assert len(errores) == 1
        # El mensaje tiene que decir QUE instalar y DONDE: quien se lo
        # encuentra esta mirando logs de ECS, sin este codigo delante.
        mensaje = errores[0].getMessage()
        assert "libpango-1.0-0" in mensaje
        assert "Dockerfile" in mensaje

    def test_no_gasta_un_error_cuando_si_se_puede(self, monkeypatch, caplog):
        import logging as _logging

        from src.api import app as app_module

        monkeypatch.setattr(app_module, "pdf_rendering_available", lambda: True)

        with caplog.at_level(_logging.INFO, logger=app_module.__name__):
            disponible = app_module.comprobar_el_render_de_pdf()

        assert disponible is True
        assert not [r for r in caplog.records if r.levelno >= _logging.ERROR]

    def test_una_imagen_sin_las_bibliotecas_arranca_igual(self, sin_render_de_pdf, client):
        """La comprobacion avisa, no tumba el arranque.

        `sin_render_de_pdf` va ANTES que `client` en la firma a proposito:
        pytest instancia las fixtures del mismo scope en ese orden, y el
        lifespan -- donde vive la llamada -- corre al construirse `client`.
        Al reves, el parche llegaria tarde y el test no probaria nada.

        Que el health check responda con el render caido es lo que separa
        "se pierde una funcion" de "se cae el producto".
        """
        assert client.get("/health").status_code == 200


class TestElTurnoTieneMargenParaTerminar:
    """`recursion_limit` en el config del turno (medido en dev, 2026-08-11).

    `MaxTurnsMiddleware` existe para cortar limpio cuando el agente se va de
    vueltas: emite un mensaje al estudiante y termina el turno. Nunca llegaba
    a dispararse. `middleware.py` documentaba que "we set a high value to let
    our guard fire first" y no lo hacia nadie, asi que LangGraph usaba su
    default de 25 -- la MITAD de `max_turns=50`.

    El resultado no era un corte limpio sino `GraphRecursionError` a mitad del
    stream, que deja la respuesta cortada a media palabra en la pantalla del
    estudiante. Le paso a un turno real que intentaba emitir un informe.
    """

    def test_el_tope_del_grafo_deja_llegar_al_guard(self):
        """El invariante que estaba roto: 25 < 50.

        Sin margen, subir `max_turns` vuelve a dejar el guard inalcanzable sin
        que nada falle, que es como se cronifico la primera vez.
        """
        from src.config import get_settings

        settings = get_settings()
        assert settings.graph_recursion_limit > settings.max_turns

    def test_va_en_la_raiz_del_config_y_no_en_configurable(self, client, monkeypatch):
        """Dentro de `configurable`, LangGraph la ignora EN SILENCIO.

        Es el fallo mas facil de reintroducir: el diccionario de al lado es
        `configurable`, poner la clave ahi parece lo natural, y no falla nada
        -- simplemente se vuelve al default de 25 y las respuestas se cortan
        otra vez.
        """
        from src.config import get_settings

        capturado = {}
        agent = client.app.state.langgraph_agent
        clone_real = agent.clone

        def clone_espia():
            copia = clone_real()
            capturado["agente"] = copia
            return copia

        monkeypatch.setattr(agent, "clone", clone_espia)

        respuesta = client.post(
            "/ag-ui",
            json=_ag_ui_body(thread_id="hilo-con-margen"),
            headers={"Authorization": f"Bearer {_make_token()}"},
        )
        assert respuesta.status_code == 200

        config = capturado["agente"].config
        assert config["recursion_limit"] == get_settings().graph_recursion_limit
        assert "recursion_limit" not in config["configurable"]
