"""FastAPI application with AG-UI streaming endpoint."""

import logging
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager

from ag_ui_langgraph import LangGraphAgent
from ag_ui_langgraph.endpoint import EventEncoder, RunAgentInput
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from src.agent import create_spark_agent
from src.budget import reset_session_budget, set_active_session
from src.config import get_settings
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

    # Create the Deep Agent (compiled LangGraph state graph).
    graph = create_spark_agent()

    # Wrap the compiled graph in a LangGraphAgent so AG-UI knows how to
    # stream events from it (messages, tool calls, reasoning, state updates,
    # subagent streams).
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

    yield

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
    async def ag_ui_endpoint(input_data: RunAgentInput, request: Request) -> StreamingResponse:
        """AG-UI streaming endpoint.

        The frontend (04-frontend) sends messages here and receives an SSE
        stream of typed events: messages, tool calls, reasoning, state updates.

        The ``thread_id`` from the request body identifies the session and is
        used to scope the budget counters in :mod:`src.budget`.
        """
        agent: LangGraphAgent = app.state.langgraph_agent
        accept_header = request.headers.get("accept")
        encoder = EventEncoder(accept=accept_header)

        # Activate the session and reset its budget before invoking the agent.
        # Each request gets its own counters; concurrent requests on different
        # thread_ids are isolated by the ContextVar in src.budget.
        set_active_session(input_data.thread_id)
        reset_session_budget(input_data.thread_id)

        # Clone the agent so each request gets isolated per-request state.
        request_agent = agent.clone()

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
