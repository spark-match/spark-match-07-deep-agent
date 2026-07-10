"""FastAPI application with AG-UI streaming endpoint."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from ag_ui_langgraph import create_endpoint
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.agent import create_spark_agent
from src.config import get_settings
from src.utils import setup_logging

logger = logging.getLogger(__name__)


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

    # Create the Deep Agent
    agent = create_spark_agent()

    # Create AG-UI endpoint handler
    # This handles SSE streaming with typed events:
    # - messages (token streaming)
    # - tool calls (start, progress, result)
    # - reasoning/thinking steps
    # - state updates
    # - subagent streams
    ag_ui_handler = create_endpoint(agent)

    # Store in app state for route access
    app.state.agent = agent
    app.state.ag_ui_handler = ag_ui_handler
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

    # Mount AG-UI endpoint
    # This exposes the SSE streaming endpoint at POST /ag-ui
    # The AG-UI client in the frontend connects here
    @app.post("/ag-ui")
    async def ag_ui_endpoint(request):
        """AG-UI streaming endpoint.

        The frontend sends messages here and receives an SSE stream
        of typed events: messages, tool calls, reasoning, state updates.
        """
        return await app.state.ag_ui_handler(request)

    # Health check
    @app.get("/health")
    async def health():
        return {
            "status": "healthy",
            "agent": settings.agent_name,
            "environment": settings.environment.value,
            "model": settings.model_string,
        }

    return app
