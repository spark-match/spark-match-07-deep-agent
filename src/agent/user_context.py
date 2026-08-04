"""Temporary ``user_id`` resolution for memory namespacing (Sprint 6).

AGENTS.md hard rule #4 requires every memory namespace to be partitioned by
``user_id``. The real ``user_id`` comes from the validated JWT via
``AgentContext`` (``context_schema``), wired in Sprint 7. Until then this
module provides a single documented placeholder so the partitioning
machinery (backends, profile hydration/persistence, preference tools) is
exercised end-to-end today, without pretending we already have real
per-user isolation.

Sprint 7 will replace :data:`DEFAULT_USER_ID` usage by making
``runtime.context.user_id`` always present (``require_auth`` rejects
unauthenticated requests before the graph ever runs), at which point
:func:`get_user_id` keeps working unchanged — it already prefers the real
context value when present.
"""

from __future__ import annotations

from typing import Any

DEFAULT_USER_ID = "local-user"


def get_user_id(runtime: Any) -> str:
    """Best-effort ``user_id`` for the current run.

    Reads ``runtime.context.user_id`` (dict or attribute access, since
    ``context_schema`` can be a plain dict or a dataclass/BaseModel).
    Falls back to :data:`DEFAULT_USER_ID` when the context is absent or
    doesn't carry a non-empty ``user_id`` — today, always, until Sprint 7.
    """
    ctx = getattr(runtime, "context", None)
    if ctx is None:
        return DEFAULT_USER_ID

    user_id = ctx.get("user_id") if isinstance(ctx, dict) else getattr(ctx, "user_id", None)
    return user_id if isinstance(user_id, str) and user_id else DEFAULT_USER_ID


__all__ = ["DEFAULT_USER_ID", "get_user_id"]
