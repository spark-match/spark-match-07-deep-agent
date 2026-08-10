"""FastAPI application with AG-UI streaming endpoint."""

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from ag_ui.core.events import EventType, RunErrorEvent
from ag_ui_langgraph import LangGraphAgent
from ag_ui_langgraph.endpoint import EventEncoder, RunAgentInput
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from src.agent import create_spark_agent
from src.api.profile import router as profile_router
from src.api.rate_limit import limiter
from src.api.runs import TurnosEnVuelo, eventos_del_turno
from src.api.security_headers import SecurityHeadersMiddleware
from src.api.sse import with_heartbeat
from src.api.threads import router as threads_router
from src.auth import (
    AuthContext,
    assert_thread_ownership,
    check_and_increment_daily_budget,
    derive_thread_id,
    require_auth,
)
from src.budget import reset_session_budget, set_active_session
from src.config import get_settings
from src.memory import build_reflection_executor
from src.observability.langsmith import configure_langsmith
from src.persistence import build_persistence
from src.threads import acquire_run_lease, record_thread_activity, release_run_lease
from src.utils import setup_logging

logger = logging.getLogger(__name__)

# Path under which the AG-UI streaming endpoint is mounted. Frontend
# (04-frontend) connects here over SSE.
AG_UI_PATH = "/ag-ui"

# Concatenacion y no f-string, a proposito. Sonar (python:S8411) busca `{...}`
# en la ruta de un endpoint para saber que path parameters declara, y lo hace
# sobre el TEXTO: con `f"{AG_UI_PATH}/health"` ve las llaves de la
# interpolacion y reclama un parametro llamado "AG_UI_PATH" que faltaria en la
# firma de la funcion. En ejecucion eso vale "/ag-ui/health" y no hay parametro
# ninguno, asi que el aviso es falso -- pero llegaba al Quality Gate como bug.
#
# Sacar la f-string a una constante no bastaba: Sonar propaga el valor y se
# queda con el texto original, llaves incluidas. Concatenando no hay llaves en
# ningun sitio y la ruta se sigue derivando de AG_UI_PATH, que es lo que
# importa para que las dos no se separen nunca.
AG_UI_HEALTH_PATH = AG_UI_PATH + "/health"

# Lo que se le manda al navegador cuando el turno revienta a mitad del
# stream. Fijo y sin el detalle de la excepcion a proposito: ese detalle
# lleva nombres de modulos, ids internos y a veces trozos del historial --
# la misma razon por la que se filtran los eventos RAW. El detalle va al log.
STREAM_FAILURE_MESSAGE = "El orientador no pudo terminar de responder. Intentalo de nuevo."


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Application lifespan — initialize logging and agent on startup."""
    settings = get_settings()

    # Centralized logging (idempotent). Runs before agent construction so
    # any deprecation warnings / import errors are captured.
    setup_logging(level=settings.log_level)
    logger.info(
        "Starting Spark Match agent (environment=%s, model=%s)",
        settings.environment.value,
        settings.model_string,
    )

    # LangSmith tracing. Idempotent: pushes SPARK_* settings into the
    # LANGSMITH_* env vars that langchain-aws / deepagents read automatically.
    configure_langsmith()

    # Checkpointer (short-term, per-thread_id) + store (long-term, per-user
    # once Sprint 7 wires real user_ids). Built once for the app lifetime so
    # sqlite/postgres connection pools are shared and closed cleanly here.
    async with build_persistence() as persistence:
        # Background StudentProfile extraction (langmem ReflectionExecutor,
        # Sprint 6 tasks 6.D/6.E). Its worker thread is non-daemon, so we own
        # its shutdown here rather than inside create_spark_agent — see the
        # docstring on create_spark_agent's reflection_executor param.
        reflection_executor = (
            build_reflection_executor(persistence.store) if persistence.store is not None else None
        )
        try:
            # Create the Deep Agent (compiled LangGraph state graph).
            graph = create_spark_agent(
                checkpointer=persistence.checkpointer,
                store=persistence.store,
                reflection_executor=reflection_executor,
            )

            # Wrap the compiled graph in a LangGraphAgent so AG-UI knows how to
            # stream events from it (messages, tool calls, reasoning, state
            # updates, subagent streams).
            langgraph_agent = LangGraphAgent(
                name=settings.agent_name,
                graph=graph,
                description=(
                    "Spark Match vocational advisor: conversational RIASEC "
                    "assessment, career matching, and personalized action plans."
                ),
            )

            # Store references for health checks / introspection.
            app.state.graph = graph
            app.state.langgraph_agent = langgraph_agent
            app.state.settings = settings
            app.state.store = persistence.store
            # Needed by the /threads routes: reading a conversation back
            # and deleting one both go straight to the checkpointer, which
            # is the only place the turn-by-turn history lives.
            app.state.checkpointer = persistence.checkpointer
            # Los turnos que siguen corriendo sin nadie mirando. Vive en el
            # app y no en un global para que dos apps en el mismo proceso
            # -- cada test monta la suya -- no compartan turnos.
            app.state.turnos = TurnosEnVuelo()

            yield
        finally:
            # Antes de cerrar la persistencia: un turno en vuelo sigue
            # escribiendo en el checkpointer, y tirarle el pool debajo lo
            # mataria con la mitad del turno escrita. Acotado porque ECS
            # manda SIGKILL al agotarse el stopTimeout del servicio, y
            # pasarse de ese plazo no gana nada.
            await app.state.turnos.esperar(settings.shutdown_grace_seconds)

            if reflection_executor is not None:
                reflection_executor.shutdown(wait=True, cancel_futures=True)

    logger.info("Spark Match agent stopped")


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    settings = get_settings()

    # Fuera de local, /docs, /redoc y /openapi.json quedan apagados: publican
    # el esquema completo de la API sin pedir autenticacion. La listener rule
    # del ALB tambien los bloquea (modules/agent-service), pero eso protege
    # solo la ruta por el ALB -- apagarlos aca cubre cualquier otra.
    docs_enabled = settings.is_local

    app = FastAPI(
        title="Spark Match Agent API",
        description=(
            "Deep Agent API for vocational guidance and career planning. "
            "Supports AG-UI protocol for real-time streaming of agent reasoning, "
            "tool calls, and subagent delegation."
        ),
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )

    # Security response headers (task 7.E.2) — added to every response.
    app.add_middleware(SecurityHeadersMiddleware)

    # CORS — allow Angular frontend. Origins are validated at startup
    # (Settings._validate_cors_origins, task 7.E.1): a wildcard combined
    # with allow_credentials=True is rejected before the app even starts.
    #
    # VA EL ULTIMO A PROPOSITO, y no es estilo. `add_middleware` inserta al
    # PRINCIPIO de la pila, asi que el ultimo en añadirse es el que queda mas
    # afuera. Con CORS añadido primero quedaba por DENTRO de los demas: si algo
    # reventaba en una capa exterior, la respuesta salia sin
    # `Access-Control-Allow-Origin` y el navegador la presentaba como un fallo
    # de CORS. El error real —un 500, un middleware roto— quedaba tapado detras
    # de un mensaje que apunta al sitio equivocado, que es la peor forma de
    # perder una tarde. Cualquier middleware nuevo va ENCIMA de este bloque.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rate limiting (task 7.E.3) — per-user_id (falls back to IP) burst
    # limiter on the costliest endpoint. slowapi raises RateLimitExceeded,
    # translated to a 429 by the handler below.
    app.state.limiter = limiter

    # `async` sin `await` a proposito. Starlette envuelve los handlers
    # sincronos en `run_in_threadpool`; esta funcion solo construye una
    # respuesta y no bloquea nada, asi que declararla async le ahorra el salto
    # de hilo. Quitarle el `async` seria mas lento, no mas simple.
    async def _handle_rate_limit_exceeded(  # NOSONAR
        request: Request, exc: Exception
    ) -> Response:
        # Thin adapter: slowapi's own handler types its second parameter as
        # RateLimitExceeded specifically, which mypy rejects as narrower
        # than the Exception FastAPI's add_exception_handler expects.
        assert isinstance(exc, RateLimitExceeded)
        return _rate_limit_exceeded_handler(request, exc)

    app.add_exception_handler(RateLimitExceeded, _handle_rate_limit_exceeded)

    # Health check — does NOT depend on the agent being constructed
    # (the lifespan runs after the route is registered, so this is safe).
    @app.get("/health")
    async def health() -> dict[str, str]:
        return {
            "status": "healthy",
            "agent": settings.agent_name,
            "environment": settings.environment.value,
            "model": settings.model_string,
        }

    # AG-UI streaming endpoint. Registered via add_api_route against the
    # module-level ag_ui_endpoint (see its docstring for why it isn't a
    # closure defined here).
    app.add_api_route(AG_UI_PATH, ag_ui_endpoint, methods=["POST"])

    @app.get(AG_UI_HEALTH_PATH)
    async def ag_ui_health() -> dict[str, str]:
        """Health check at /ag-ui/health (mirrors the workshop convention)."""
        return {"status": "ok", "agent": settings.agent_name}

    # Session management (list / read / delete). Streaming a turn is only
    # part of a chat product; see src/api/threads.py.
    app.include_router(threads_router)
    # Leer el perfil y corregir sus cuatro preferencias de busqueda. Hasta
    # ahora el perfil solo tenia entrada (el extractor conversacional) y
    # ninguna salida: el estudiante no podia ver ni arreglar lo que el sistema
    # creia saber de el. Ver src/api/profile.py.
    app.include_router(profile_router)

    return app


def _is_internal_raw_event(event: object, emit_raw_events: bool) -> bool:
    """Whether this event is a RAW passthrough that must not leave the server.

    ``ag_ui_langgraph`` emits a ``RawEvent`` for *every* LangGraph event,
    unconditionally — there is no flag upstream to turn it off. Those events
    carry the verbatim internals: the coordinator's system prompt, the
    content-safety classifier's prompt, every intermediate state. Measured
    on a real dev turn, 168 of 297 events and 60% of the stream's bytes.

    That is a disclosure, not just noise. Registration is public, so any
    student can read exactly what the safety filter checks for — which is
    most of the work of evading it. The typed AG-UI events (TEXT_MESSAGE_*,
    STEP_*, STATE_SNAPSHOT, MESSAGES_SNAPSHOT) are the protocol surface the
    frontend actually consumes; RAW is a debugging convenience.
    """
    return not emit_raw_events and getattr(event, "type", None) == EventType.RAW


def _first_user_message_text(input_data: RunAgentInput) -> str | None:
    """Text of the first user message in the payload, for the thread title.

    The *first* rather than the last: the title is written once, when the
    conversation is created, and what names a conversation is how it
    opened. Returns None when the payload carries no readable user text
    (a resumed tool loop, say), which leaves the entry with its default
    title for a later turn to fill in.
    """
    for message in input_data.messages:
        if getattr(message, "role", None) != "user":
            continue
        content = getattr(message, "content", None)
        if isinstance(content, str) and content.strip():
            return content
    return None


# Module-level (not a closure inside create_app()) and decorated exactly
# once at import time. slowapi's @limiter.limit registers this route's
# limit under a key derived from the function's *qualified name*
# (f"{func.__module__}.{func.__name__}"), in the module-level `limiter`
# singleton's process-wide bookkeeping — redefining/redecorating a
# same-named closure on every create_app() call (as tests do, once per
# test) would silently accumulate duplicate limit entries for that one
# name and over-count hits on every subsequent request. Defining it once
# here and registering it onto each app via add_api_route (instead of
# @app.post + @limiter.limit inside create_app()) sidesteps that
# entirely. Uses `request.app.state.*` instead of a closed-over `app`
# variable for the same reason.
@limiter.limit(lambda: f"{get_settings().rate_limit_per_minute}/minute")
async def ag_ui_endpoint(
    input_data: RunAgentInput,
    request: Request,
    auth: AuthContext = Depends(require_auth),  # noqa: B008
) -> StreamingResponse:
    """AG-UI streaming endpoint.

    The frontend (04-frontend) sends messages here and receives an SSE
    stream of typed events: messages, tool calls, reasoning, state updates.

    Requires a valid JWT (``require_auth``, Sprint 7 task 7.A). The
    client-supplied ``thread_id`` is untrusted (hard rule #5): it is
    replaced by a derivation from ``(auth.user_id, thread_id)`` and its
    ownership is checked/registered before the graph ever runs (task
    7.B), so one user can never read or continue another user's
    conversation. Also subject to a per-user_id rate limit (task 7.E.3)
    and daily request budget (task 7.E.4), both enforced before the
    agent is invoked.
    """
    settings = get_settings()
    agent: LangGraphAgent = request.app.state.langgraph_agent
    store = request.app.state.store
    accept_header = request.headers.get("accept")
    encoder = EventEncoder(accept=accept_header)

    await check_and_increment_daily_budget(
        store, auth.user_id, settings.budget_max_requests_per_user_per_day
    )

    client_thread_id = input_data.thread_id
    thread_id = derive_thread_id(auth.user_id, client_thread_id)
    await assert_thread_ownership(store, thread_id, auth.user_id)
    input_data.thread_id = thread_id

    # Un turno a la vez por conversacion. Sin esto, dos pestañas abiertas
    # sobre el mismo hilo corren las dos y se pisan EN SILENCIO: el
    # checkpointer no tiene control de concurrencia ninguno, asi que las dos
    # leen el mismo estado, las dos escriben, y los mensajes de la que
    # termine antes dejan de estar en el camino desde la cabeza. Ver
    # src/threads/lease.py.
    #
    # Antes de indexar y no despues, por la misma razon por la que indexar va
    # despues del ownership: un turno rechazado no debe escribir nada. Con el
    # orden al reves, el 409 movia igualmente la conversacion a lo alto del
    # sidebar por un turno que no llego a existir.
    lease = await acquire_run_lease(
        store, thread_id, input_data.run_id, settings.run_lease_ttl_seconds
    )
    if lease is None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "A turn is already running on this conversation.",
        )

    # Index the conversation so it can be listed later. Runs on every turn,
    # not just the first: `updated_at` is what orders the sidebar. Done
    # after the ownership check so a rejected request never writes.
    await record_thread_activity(
        store,
        auth.user_id,
        thread_id,
        client_thread_id,
        title_seed=_first_user_message_text(input_data),
    )

    # Activate the session and reset its budget before invoking the agent.
    # Each request gets its own counters; concurrent requests on different
    # thread_ids are isolated by the ContextVar in src.budget.
    set_active_session(thread_id)
    reset_session_budget(thread_id)

    # Clone the agent so each request gets isolated per-request state,
    # then attach this request's auth context. ag_ui_langgraph forwards
    # config["configurable"] into runtime.context on every node/tool
    # call (base_context.update(config["configurable"])), so
    # user_id/role/email become available as runtime.context.* and as
    # the "{user_id}" langmem namespace substitution everywhere.
    request_agent = agent.clone()
    request_agent.config = {
        "configurable": {
            "thread_id": thread_id,
            "user_id": auth.user_id,
            "role": auth.role,
            "email": auth.email,
        }
    }

    async def eventos_publicables() -> AsyncIterator[Any]:
        async for event in request_agent.run(input_data):
            if not _is_internal_raw_event(event, settings.sse_emit_raw_events):
                yield event

    def avisar_del_fallo(error: Exception) -> RunErrorEvent:
        # Sin esto, cualquier excepcion dentro del grafo sale como
        # "Exception in ASGI application" y el SSE se corta sin emitir NADA.
        # El frontend termina su bucle sin error, asi que el estudiante se
        # queda mirando su pregunta: ni respuesta, ni aviso, ni forma de
        # saber que paso. Medido en dev el 2026-08-08 con un historial
        # invalido (ver src/agent/tool_call_repair.py): el turno moria en
        # silencio y el estudiante lo reintentaba una y otra vez. RUN_ERROR
        # es el evento que el protocolo tiene para esto y el frontend ya lo
        # maneja.
        logger.exception("El turno fallo (thread_id=%s)", thread_id, exc_info=error)
        return RunErrorEvent(message=STREAM_FAILURE_MESSAGE, code="agent_stream_failed")

    async def soltar_la_conversacion() -> None:
        await release_run_lease(store, thread_id, lease.run_id)

    # El turno se conduce en una tarea aparte y los eventos llegan por una
    # cola. Antes lo conducia este mismo generador, asi que cuando el
    # cliente se iba `with_heartbeat` lo cerraba y el run moria con el: si
    # te ibas mientras el modelo escribia, su mensaje no se persistia y
    # volvias a encontrar tu pregunta sin respuesta. Ver src/api/runs.py.
    turno = request.app.state.turnos.lanzar(
        eventos_publicables, avisar_del_fallo, soltar_la_conversacion
    )

    async def event_generator() -> AsyncIterator[str]:
        async for event in eventos_del_turno(turno):
            yield encoder.encode(event)

    return StreamingResponse(
        # Wrapped so a long silence inside a turn (classifier call, main
        # model TTFT, a slow tool) doesn't look like a dead connection to
        # the CloudFront distribution in front of the ALB — see
        # src/api/sse.py for why 60s is the number that matters.
        with_heartbeat(event_generator(), settings.sse_heartbeat_seconds),
        media_type=encoder.get_content_type(),
    )
