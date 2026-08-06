"""FastAPI application with AG-UI streaming endpoint."""

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from ag_ui_langgraph import LangGraphAgent
from ag_ui_langgraph.endpoint import EventEncoder, RunAgentInput
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from src.agent import create_spark_agent
from src.api.rate_limit import limiter
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
from src.threads import record_thread_activity
from src.utils import setup_logging

logger = logging.getLogger(__name__)

# Path under which the AG-UI streaming endpoint is mounted. Frontend
# (04-frontend) connects here over SSE.
AG_UI_PATH = "/ag-ui"


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

            yield
        finally:
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

    # CORS — allow Angular frontend. Origins are validated at startup
    # (Settings._validate_cors_origins, task 7.E.1): a wildcard combined
    # with allow_credentials=True is rejected before the app even starts.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Security response headers (task 7.E.2) — added to every response.
    app.add_middleware(SecurityHeadersMiddleware)

    # Rate limiting (task 7.E.3) — per-user_id (falls back to IP) burst
    # limiter on the costliest endpoint. slowapi raises RateLimitExceeded,
    # translated to a 429 by the handler below.
    app.state.limiter = limiter

    async def _handle_rate_limit_exceeded(request: Request, exc: Exception) -> Response:
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

    @app.get(f"{AG_UI_PATH}/health")
    async def ag_ui_health() -> dict[str, str]:
        """Health check at /ag-ui/health (mirrors the workshop convention)."""
        return {"status": "ok", "agent": settings.agent_name}

    # Session management (list / read / delete). Streaming a turn is only
    # part of a chat product; see src/api/threads.py.
    app.include_router(threads_router)

    return app


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

    async def event_generator() -> AsyncIterator[str]:
        async for event in request_agent.run(input_data):
            yield encoder.encode(event)

    return StreamingResponse(
        # Wrapped so a long silence inside a turn (classifier call, main
        # model TTFT, a slow tool) doesn't look like a dead connection to
        # the CloudFront distribution in front of the ALB — see
        # src/api/sse.py for why 60s is the number that matters.
        with_heartbeat(event_generator(), settings.sse_heartbeat_seconds),
        media_type=encoder.get_content_type(),
    )
