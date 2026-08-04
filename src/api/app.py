"""FastAPI application with AG-UI streaming endpoint."""

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from ag_ui_langgraph import LangGraphAgent
from ag_ui_langgraph.endpoint import EventEncoder, RunAgentInput
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from src.agent import create_spark_agent
from src.auth import AuthContext, assert_thread_ownership, derive_thread_id, require_auth
from src.budget import reset_session_budget, set_active_session
from src.config import get_settings
from src.memory import build_reflection_executor
from src.observability.langsmith import configure_langsmith
from src.persistence import build_persistence
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

            yield
        finally:
            if reflection_executor is not None:
                reflection_executor.shutdown(wait=True, cancel_futures=True)

    logger.info("Spark Match agent stopped")


def create_app() -> FastAPI:
    """Create the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Spark Match Agent API",
        description=(
            "Deep Agent API for vocational guidance and career planning. "
            "Supports AG-UI protocol for real-time streaming of agent reasoning, "
            "tool calls, and subagent delegation."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS — allow Angular frontend
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

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

    # AG-UI streaming endpoint. Registered here (not in lifespan) so we can
    # wire the per-session budget guard: we extract thread_id from the
    # request body and set it as the active session for tool calls.
    @app.post(AG_UI_PATH)
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
        conversation.
        """
        agent: LangGraphAgent = app.state.langgraph_agent
        accept_header = request.headers.get("accept")
        encoder = EventEncoder(accept=accept_header)

        thread_id = derive_thread_id(auth.user_id, input_data.thread_id)
        await assert_thread_ownership(app.state.store, thread_id, auth.user_id)
        input_data.thread_id = thread_id

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
            event_generator(),
            media_type=encoder.get_content_type(),
        )

    @app.get(f"{AG_UI_PATH}/health")
    async def ag_ui_health() -> dict[str, str]:
        """Health check at /ag-ui/health (mirrors the workshop convention)."""
        return {"status": "ok", "agent": settings.agent_name}

    return app
