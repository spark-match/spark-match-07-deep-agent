"""Spark Match Agent — top-level package.

Re-exports the public API for callers that prefer a flat import surface,
e.g. ``from src import create_spark_agent, get_settings``.

Internal layout:

    src/
    ├── agent/      # LangGraph factory + subagents
    ├── api/        # FastAPI app + AG-UI endpoint
    ├── budget.py   # Per-session counters (web_search cap)
    ├── config/     # Pydantic settings (env-driven)
    ├── memory/     # langmem profile extraction
    ├── models/     # Pydantic schemas (StudentProfile)
    ├── prompts/    # Coordinator system prompt
    ├── tools/      # @tool-decorated functions exposed to the LLM
    └── utils/      # Logging setup
"""

from src.agent import create_spark_agent
from src.config import Settings, get_settings

__all__ = ["Settings", "create_spark_agent", "get_settings"]
