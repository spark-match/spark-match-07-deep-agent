# =============================================================================
# Spark Match Deep Agent - Dockerfile
# =============================================================================
# Sprint 10, task 10.A.
#
# Design choices (see ROADMAP-2026-08.md SS10.A):
#   - Multi-stage build: uv for dependency resolution + python:3.14-slim for
#     the runtime image. Keeps the runtime image free of build tooling.
#   - Non-root user (spark:1001). Required by AGENTS.md hard rule that
#     runtime containers must not run as root.
#   - linux/arm64 platform (the deploy target documented in 10.B -- ECS
#     Fargate on Graviton).
#   - /health endpoint on port 8080. Port 8080, not 8000: the frontend
#     already reserves localhost:8000 for the backend service (see
#     ROADMAP-2026-08.md SS2.5), so the agent container MUST NOT collide.
#     The default is overridable via SPARK_API_PORT env var for tests.
#   - HEALTHCHECK against /health (defined in src/api/app.py:147, returns
#     200 JSON with agent_name + environment). Curl would add a runtime
#     dep we don't need; the Python stdlib urllib.request is sufficient
#     and matches the "minimal runtime image" goal.
#
# The builder stage uses `uv sync --frozen --no-dev` against the committed
# uv.lock. --no-install-project in the first step lets uv resolve and
# install dependencies BEFORE the project source is copied (better layer
# cache when only src/ changes).
#
# .dockerignore (122 lines) excludes tests/, .venv/, .git/, secrets,
# IDE state, etc. -- see that file for the full list.
# =============================================================================

# ---- Stage 1: builder ------------------------------------------------------
FROM --platform=linux/arm64 ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Copy ONLY the dependency manifests first so Docker caches this layer
# separately from src/ changes -- a code edit doesn't invalidate the
# (slow) `uv sync` step.
COPY pyproject.toml uv.lock .python-version ./

# Install dependencies (no project yet). --frozen pins to uv.lock so the
# build is reproducible; --no-dev excludes test-only deps from the
# runtime image (pytest, ruff, mypy, etc.).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Now copy the application source and skills/data.
#
# README.md va aca y no en el COPY de manifests de arriba a proposito: el
# `uv sync` de la linea siguiente instala el proyecto en si, y como
# pyproject.toml declara `readme = "README.md"`, el build hatchling aborta con
#   OSError: Readme file does not exist: README.md
# si el fichero no esta en la imagen. Dejarlo en este grupo evita invalidar la
# capa (lenta) de dependencias cada vez que se edita el README.
COPY README.md ./
COPY src/ ./src/
COPY skills/ ./skills/
COPY data/ ./data/

# Install the project itself (links src/ as an importable package
# without re-fetching deps thanks to --no-install-project above).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- Stage 2: runtime -----------------------------------------------------
FROM --platform=linux/arm64 python:3.14-slim-bookworm AS runtime

# Create non-root user. 1001 is a fixed UID/GID so volume permissions
# are reproducible across rebuilds (important for AgentCore / ECR
# layered volumes).
RUN groupadd --gid 1001 spark \
    && useradd --uid 1001 --gid spark --create-home --shell /bin/bash spark

WORKDIR /app

# Copy the full app from the builder -- uv already linked the venv
# in /app/.venv, so copying the whole /app is the simplest correct
# approach (uv.lock + .venv + src/ + skills/ + data/).
COPY --from=builder --chown=spark:spark /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Default API port is 8080 (see module docstring). Overridable at
    # `docker run --env SPARK_API_PORT=9000 ...` for local tests.
    SPARK_API_PORT=8080 \
    SPARK_API_HOST=0.0.0.0

USER spark

EXPOSE 8080

# stdlib-only healthcheck -- avoids adding curl to the runtime image.
# Matches the JSON shape of /health defined in src/api/app.py:147.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"

# `python -m src` runs src/__main__.py which calls src.api.server.main
# which calls uvicorn.run with the SPARK_API_PORT env var respected
# via src.config.settings (api_port env-binds to SPARK_API_PORT via
# pydantic-settings' SPARK_ prefix convention).
CMD ["python", "-m", "src"]
