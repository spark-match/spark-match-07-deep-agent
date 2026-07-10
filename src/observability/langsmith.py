"""LangSmith tracing wiring for the Spark Match Agent.

LangChain reads ``LANGSMITH_*`` environment variables and auto-instruments
every model call, tool call, and chain run. The convention is well
documented: https://docs.smith.langchain.com/

Activation matrix (evaluated at first model/tool call):

============================ =================== ==============================
``LANGSMITH_TRACING=true``   ``LANGSMITH_API_KEY``  Result
============================ =================== ==============================
false                       (any)                Tracing OFF, no overhead.
true                        unset                Tracing OFF (with WARNING logged).
true                        set                  Tracing ON to ``LANGSMITH_PROJECT``.
============================ =================== ==============================

We don't manually construct a ``LangChainTracer`` because langchain-aws /
deepagents already pick up the env vars. This module just:

1. Sets the env vars from our ``Settings`` (so users only need to fill in
   ``SPARK_*`` in ``.env``).
2. Logs a one-line confirmation on startup so it's obvious whether tracing
   is on or off.
3. Exposes :func:`is_langsmith_enabled` for tests and runtime guards.
"""

import logging
import os

from src.config import get_settings

logger = logging.getLogger(__name__)

# Names of the env vars LangSmith looks for. Kept as constants so callers
# can assert against them in tests.
ENV_TRACING = "LANGSMITH_TRACING"
ENV_API_KEY = "LANGSMITH_API_KEY"
ENV_PROJECT = "LANGSMITH_PROJECT"
ENV_ENDPOINT = "LANGSMITH_ENDPOINT"  # for self-hosted / EU regions


def configure_langsmith() -> bool:
    """Push Spark settings into LangSmith's env vars.

    Returns True if tracing will be active, False otherwise. Idempotent —
    safe to call multiple times.

    Does NOT verify the API key against LangSmith. Connection validation
    happens on the first trace. Set ``SPARK_LANGSMITH_TRACING=true`` and
    ``SPARK_LANGSMITH_API_KEY`` to enable.
    """
    settings = get_settings()

    if not settings.langsmith_tracing:
        logger.info("LangSmith tracing disabled (SPARK_LANGSMITH_TRACING=false)")
        return False

    if settings.langsmith_api_key is None:
        logger.warning(
            "LangSmith tracing requested (SPARK_LANGSMITH_TRACING=true) but "
            "SPARK_LANGSMITH_API_KEY is not set. Traces will NOT be sent. "
            "Get a free key at https://smith.langchain.com"
        )
        return False

    # Push to os.environ — LangChain's tracer picks these up automatically.
    os.environ[ENV_TRACING] = "true"
    os.environ[ENV_API_KEY] = settings.langsmith_api_key.get_secret_value()
    os.environ[ENV_PROJECT] = settings.langsmith_project

    logger.info(
        "LangSmith tracing ENABLED (project=%s)",
        settings.langsmith_project,
    )
    return True


def is_langsmith_enabled() -> bool:
    """Return True if tracing is currently active in this process."""
    return os.environ.get(ENV_TRACING, "").lower() == "true" and bool(os.environ.get(ENV_API_KEY))


__all__ = [
    "ENV_API_KEY",
    "ENV_ENDPOINT",
    "ENV_PROJECT",
    "ENV_TRACING",
    "configure_langsmith",
    "is_langsmith_enabled",
]
