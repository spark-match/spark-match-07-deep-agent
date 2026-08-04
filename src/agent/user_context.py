"""``user_id`` resolution for memory namespacing.

AGENTS.md hard rule #4 requires every memory namespace to be partitioned by
``user_id``. Since Sprint 7, the real ``user_id`` comes from the validated
JWT via ``runtime.context`` (``AgentContext``, wired by
``src.api.app.ag_ui_endpoint`` through
``config["configurable"]["user_id"]`` -> ``ag_ui_langgraph``'s
``base_context.update(...)`` -> ``runtime.context.user_id``).

:data:`DEFAULT_USER_ID` remains as the fallback for code paths that invoke
the compiled graph directly without going through the authenticated
``/ag-ui`` endpoint — most unit tests, and any future non-HTTP entry point.
It is never used for a real, authenticated request.
"""

from __future__ import annotations

from typing import Any

DEFAULT_USER_ID = "local-user"


def get_user_id(runtime: Any) -> str:
    """Best-effort ``user_id`` for the current run.

    Reads ``runtime.context.user_id`` (dict or attribute access, since
    ``context_schema`` can be a plain dict or a dataclass/BaseModel).
    Falls back to :data:`DEFAULT_USER_ID` when the context is absent or
    doesn't carry a non-empty ``user_id`` (unauthenticated direct graph
    invocation, e.g. most unit tests).
    """
    ctx = getattr(runtime, "context", None)
    if ctx is None:
        return DEFAULT_USER_ID

    user_id = ctx.get("user_id") if isinstance(ctx, dict) else getattr(ctx, "user_id", None)
    return user_id if isinstance(user_id, str) and user_id else DEFAULT_USER_ID


__all__ = ["DEFAULT_USER_ID", "get_user_id"]
