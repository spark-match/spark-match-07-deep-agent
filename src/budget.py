"""Budget guards for agent invocations.

Tracks per-session counters that prevent runaway costs / loops. Inspired by
``src/research/app/exploration_budget.py`` in the Paul Iusztin workshop.

Currently enforced:

- **Web search budget** — caps the number of web searches a single agent
  invocation can make, so a runaway planner can't burn the Tavily quota.
  Reset at the start of each agent invocation via :func:`reset_session_budget`.

- **Turn budget** — caps the total number of LLM turns per session via the
  :data:`MAX_TURNS` re-export from settings. The LangGraph ``MessagesState``
  tracks turn count; the factory wires a ``should_continue`` edge to ``END``
  when this cap is reached.

Usage in the factory::

    from src.budget import reset_session_budget
    reset_session_budget()  # call before each agent.invoke() / stream()

The counters live in a module-level dict keyed by session_id, which the AG-UI
endpoint passes via ``config["configurable"]["thread_id"]`` (LangGraph
convention). For tests that don't pass a thread_id, we fall back to
``"_default"``.
"""

import logging
from contextvars import ContextVar

logger = logging.getLogger(__name__)

# Default session id when no thread_id is provided (tests, smoke runs).
DEFAULT_SESSION_ID = "_default"

# Per-session counters, keyed by session_id.
# Module-level so they survive across tool invocations within a session.
_session_counters: dict[str, int] = {}

# Context var so tools running in async tasks can pick up the active session
# without each tool needing a session_id parameter.
_active_session: ContextVar[str] = ContextVar("active_session", default=DEFAULT_SESSION_ID)


def set_active_session(session_id: str) -> None:
    """Set the active session id for the current async context.

    Called by the AG-UI endpoint before invoking the agent, so tool calls
    during this invocation can identify which session they belong to.
    """
    _active_session.set(session_id)


def get_active_session() -> str:
    """Return the active session id (set via :func:`set_active_session`)."""
    return _active_session.get()


def reset_session_budget(session_id: str | None = None) -> None:
    """Reset budget counters for the given session (or the active one).

    Call at the start of every agent invocation so each session starts fresh.
    """
    sid = session_id or get_active_session()
    _session_counters.pop(sid, None)
    logger.debug("Budget counters reset for session=%s", sid)


def increment_web_search(session_id: str | None = None) -> int:
    """Increment the web-search counter for the session and return the new value.

    The caller (the ``web_search`` tool) compares the returned value against
    the configured budget to decide whether to perform the search or refuse.
    """
    sid = session_id or get_active_session()
    current = _session_counters.get(sid, 0)
    _session_counters[sid] = current + 1
    return _session_counters[sid]


def get_web_search_count(session_id: str | None = None) -> int:
    """Return current web-search count for the session (read-only)."""
    sid = session_id or get_active_session()
    return _session_counters.get(sid, 0)


__all__ = [
    "DEFAULT_SESSION_ID",
    "get_active_session",
    "get_web_search_count",
    "increment_web_search",
    "reset_session_budget",
    "set_active_session",
]
