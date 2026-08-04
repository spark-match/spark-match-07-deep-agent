"""Thread ownership guard (Sprint 7, task 7.B).

The client-supplied ``thread_id`` is untrusted input (hard rule #5): it
arrives from the frontend without validation, and LangGraph's checkpointer
uses it verbatim to key conversation history. Without this guard, any
caller who guesses or replays another user's ``thread_id`` reads that
user's conversation.

Two complementary measures, both applied:

1. **Derivation** — the *effective* thread id used for the checkpointer is
   ``sha256(f"{user_id}:{client_thread_id}")``, not the raw client value.
   This alone prevents cross-user collisions even if the registry below is
   ever bypassed, but it isn't auditable on its own (two different
   ``(user_id, thread_id)`` pairs never collide, yet you can't list "which
   threads belong to user X" from the derived id alone).
2. **Registration** — the first time a derived thread id is seen, its owning
   ``user_id`` is recorded in the store. Every subsequent request re-checks
   the recorded owner and rejects (403) any mismatch. This is what actually
   detects and audits an ownership violation, rather than merely preventing
   the accidental collision that derivation alone protects against.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from fastapi import HTTPException, status

if TYPE_CHECKING:
    from langgraph.store.base import BaseStore

THREAD_OWNER_NAMESPACE = ("spark-match", "_threads")


def derive_thread_id(user_id: str, client_thread_id: str) -> str:
    """Derive the effective thread id from the caller's user_id.

    Deterministic per ``(user_id, client_thread_id)`` pair, so the same
    client-side conversation always maps to the same derived id for a given
    authenticated user, and two different users can never collide even if
    they pick the same client-side value.
    """
    digest = hashlib.sha256(f"{user_id}:{client_thread_id}".encode()).hexdigest()
    return f"t_{digest}"


async def assert_thread_ownership(store: BaseStore | None, thread_id: str, user_id: str) -> None:
    """Register or verify ownership of ``thread_id``.

    First call for a given ``thread_id`` registers ``user_id`` as its owner.
    Subsequent calls raise ``403`` if a *different* ``user_id`` attempts to
    use the same ``thread_id`` — which, given :func:`derive_thread_id`,
    would only happen from a raw sha256 collision or a bug elsewhere, but is
    checked explicitly anyway since this is the actual auditable record.

    A ``None`` store (e.g. graph built without persistence, most unit
    tests) makes this a no-op: there's nowhere durable to register
    ownership, and no persisted history to protect either.
    """
    if store is None:
        return

    item = await store.aget(THREAD_OWNER_NAMESPACE, thread_id)
    if item is None:
        await store.aput(
            THREAD_OWNER_NAMESPACE,
            thread_id,
            {"user_id": user_id, "created_at": datetime.now(UTC).isoformat()},
        )
        return

    owner_id = item.value.get("user_id") if isinstance(item.value, dict) else None
    if owner_id != user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Thread does not belong to the caller")


__all__: list[str] = ["THREAD_OWNER_NAMESPACE", "assert_thread_ownership", "derive_thread_id"]
