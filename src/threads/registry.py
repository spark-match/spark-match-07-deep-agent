"""Per-user index of chat threads.

``src/auth/thread_guard.py`` already records who owns a thread, but that
record is deliberately shaped for one job: answering "is this caller
allowed to touch this derived id?" in a single O(1) lookup. It is keyed by
the *derived* thread id in a global namespace and holds nothing else, so
it cannot answer the question a chat sidebar actually asks — "what
conversations does this student have, most recent first?"

Two things block that. First, the namespace is global, so listing it would
walk every user's threads. Second, the derived id is a one-way
``sha256(user_id:client_thread_id)``: the server cannot recover the id the
client uses to address the conversation, so even a correct listing would
return values the frontend can't do anything with.

This module adds the missing half — a per-user, listable index that keeps
the client-side id, a human title and a last-activity timestamp. Ownership
enforcement stays where it is: this is an index, not a permission check,
and the two should not be confused. Every read here is still gated by
``assert_thread_ownership`` upstream.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph.store.base import BaseStore

logger = logging.getLogger(__name__)

# Longest title kept from the opening message. Sized for a sidebar entry:
# long enough to tell two conversations about careers apart, short enough
# not to wrap.
MAX_TITLE_LENGTH = 60

DEFAULT_TITLE = "Nueva conversación"

# The store's `asearch` has no ordering guarantee, so "most recent first"
# has to be done here, which means reading the whole index before paging
# it. This caps that read. A student with more threads than this keeps
# every one of them -- nothing is deleted -- but the tail stops appearing
# in the sidebar, and the truncation is logged rather than silently
# pretending the list is complete.
MAX_INDEXED_THREADS = 500


def thread_index_namespace(user_id: str) -> tuple[str, ...]:
    """Namespace holding one entry per conversation owned by ``user_id``."""
    return ("spark-match", user_id, "threads")


def build_title(seed: str | None) -> str:
    """Turn the opening message into a sidebar label.

    Deliberately mechanical: the first thing the student typed, collapsed
    and truncated. Generating a nicer title with a model is possible (the
    fast model is already wired for the content filter) but it would put a
    second inference call in the path of every new conversation, and a
    truncated first message is what a student recognizes anyway.
    """
    if not seed:
        return DEFAULT_TITLE

    collapsed = " ".join(seed.split())
    if not collapsed:
        return DEFAULT_TITLE
    if len(collapsed) <= MAX_TITLE_LENGTH:
        return collapsed
    return collapsed[: MAX_TITLE_LENGTH - 1].rstrip() + "…"


async def record_thread_activity(
    store: BaseStore | None,
    user_id: str,
    thread_id: str,
    client_thread_id: str,
    title_seed: str | None = None,
) -> None:
    """Create or refresh this thread's index entry.

    Called on every turn: ``updated_at`` is what orders the sidebar, so it
    has to move even when nothing else about the conversation changed. The
    title is written once, on creation, and then left alone — a
    conversation whose label kept changing under the student as it went on
    would be worse than a slightly stale one.

    A ``None`` store makes this a no-op, matching
    :func:`~src.auth.thread_guard.assert_thread_ownership`: with no durable
    store there is nothing to index and nothing to list.
    """
    if store is None:
        return

    now = datetime.now(UTC).isoformat()
    namespace = thread_index_namespace(user_id)

    existing = await store.aget(namespace, thread_id)
    if existing is not None and isinstance(existing.value, dict):
        entry = dict(existing.value)
        entry["updated_at"] = now
        # Backfill for entries written before a title could be derived --
        # a turn that arrived with no readable text, say.
        if not entry.get("title") or entry.get("title") == DEFAULT_TITLE:
            entry["title"] = build_title(title_seed)
    else:
        entry = {
            "client_thread_id": client_thread_id,
            "title": build_title(title_seed),
            "created_at": now,
            "updated_at": now,
        }

    await store.aput(namespace, thread_id, entry)


def _to_summary(key: str, value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    client_thread_id = value.get("client_thread_id")
    if not isinstance(client_thread_id, str) or not client_thread_id:
        # Written by an older version, or corrupt. Without the client-side
        # id the entry is unusable: the frontend could not reopen it.
        return None
    return {
        "thread_id": client_thread_id,
        "title": value.get("title") or DEFAULT_TITLE,
        "created_at": value.get("created_at", ""),
        "updated_at": value.get("updated_at", ""),
        "derived_thread_id": key,
    }


async def list_threads(
    store: BaseStore | None,
    user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return ``user_id``'s conversations, most recently active first."""
    if store is None:
        return []

    items = await store.asearch(thread_index_namespace(user_id), limit=MAX_INDEXED_THREADS)
    if len(items) >= MAX_INDEXED_THREADS:
        logger.warning(
            "thread_index_truncated user_id=%r cap=%d — older conversations are stored "
            "but will not appear in the listing",
            user_id,
            MAX_INDEXED_THREADS,
        )

    summaries = [s for item in items if (s := _to_summary(item.key, item.value)) is not None]
    summaries.sort(key=lambda entry: entry["updated_at"], reverse=True)
    return summaries[offset : offset + limit]


async def forget_thread(store: BaseStore | None, user_id: str, thread_id: str) -> None:
    """Drop the index entry and the ownership record for ``thread_id``.

    The ownership record goes too, deliberately. Leaving it behind would
    keep a derived id permanently claimed, so a student who deleted a
    conversation and then started a new one under the same client-side id
    would be met with a 403 on their own thread.
    """
    if store is None:
        return

    from src.auth.thread_guard import THREAD_OWNER_NAMESPACE

    await store.adelete(thread_index_namespace(user_id), thread_id)
    await store.adelete(THREAD_OWNER_NAMESPACE, thread_id)


__all__ = [
    "DEFAULT_TITLE",
    "MAX_INDEXED_THREADS",
    "MAX_TITLE_LENGTH",
    "build_title",
    "forget_thread",
    "list_threads",
    "record_thread_activity",
    "thread_index_namespace",
]
