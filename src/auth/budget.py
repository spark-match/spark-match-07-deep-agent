"""Per-user request budget, backed by the store (Sprint 7, task 7.E.4).

``src/budget.py`` caps *web searches within a single agent turn* using an
in-process dict + ``ContextVar``, keyed by session/thread — by design (it
resets per invocation, and the tool call site is synchronous; converting it
to be store-backed is Sprint 8 scope, task 8.1, when ``web_search`` moves
to an async handler that can actually ``await`` a store call).

This module adds a second, complementary cap: a **daily invocation budget
per authenticated user_id**, checked once per ``POST /ag-ui`` call before
the agent runs. Unlike the session-scoped web-search counter, this one
must survive process restarts and be consistent across ``--workers > 1``
(hard rule #4: partitioned by ``user_id``), so it lives in the same
``BaseStore`` used for thread ownership (``src/auth/thread_guard.py``) and
long-term memory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

if TYPE_CHECKING:
    from langgraph.store.base import BaseStore

BUDGET_NAMESPACE_SUFFIX = "budget"


def _today_key() -> str:
    """UTC calendar day bucket, e.g. ``2026-08-04``."""
    return datetime.now(UTC).date().isoformat()


async def check_and_increment_daily_budget(
    store: BaseStore | None,
    user_id: str,
    max_per_day: int,
) -> None:
    """Raise ``429`` if ``user_id`` has exceeded its daily request budget.

    Otherwise records this call and lets the request proceed. A ``None``
    store (graph built without persistence, most unit tests) or a
    ``max_per_day`` of ``0`` (explicitly disabled) makes this a no-op,
    mirroring the escape hatches already used by
    ``assert_thread_ownership`` and ``max_web_searches_per_session``.
    """
    if store is None or max_per_day <= 0:
        return

    namespace = ("spark-match", user_id, BUDGET_NAMESPACE_SUFFIX)
    key = _today_key()

    item = await store.aget(namespace, key)
    current = item.value.get("count", 0) if item is not None and isinstance(item.value, dict) else 0

    if current >= max_per_day:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Daily request budget exceeded ({max_per_day} per day).",
        )

    await store.aput(namespace, key, {"count": current + 1, "date": key})


__all__ = ["BUDGET_NAMESPACE_SUFFIX", "check_and_increment_daily_budget"]
