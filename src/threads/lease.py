"""One turn at a time per conversation.

Two tabs open on the same conversation can both send a message, and until
now both ran. The Postgres checkpointer has no concurrency control of any
kind to stop them: every write is

    INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, ...)
    ON CONFLICT (thread_id, checkpoint_ns, checkpoint_id) DO UPDATE ...

and ``checkpoint_id`` is a *fresh* UUID6 per write, so two concurrent runs
never collide — no conflict, no lock, no error. Current state is then read
back with ``ORDER BY checkpoint_id DESC LIMIT 1``.

The consequence is silent: both runs read the same state, both append, and
whichever finishes last becomes the head. The other turn's messages are
still in the table but no longer on the path from the head, so they are
never seen again. The student watches an answer being written and then
finds it gone.

This module makes a turn take a **lease** on its conversation. A second
turn that finds a live lease is refused (409) instead of racing.

## Why the store and not a lock in the process

An ``asyncio.Lock`` would be exact — and only within one process. Today
that is the whole fleet (a single Fargate task), but the ALB has no
sticky sessions, so the day a second task exists two tabs can land on
different ones and the lock stops meaning anything. The store is where
thread ownership and the daily budget already live, for the same reason.

What the store cannot do is compare-and-set: this reads, then writes.
Two requests landing within the same few milliseconds can both see a free
conversation. That race is not closed here and is not worth closing —
this exists for two tabs driven by a person, seconds apart, which it does
close. The same trade-off is already documented in
:mod:`src.auth.budget`.

## Why leases expire

A process that dies mid-turn never releases. Without an expiry that
conversation would be locked forever, and the student's only recourse
would be to start a new one. The TTL is sized to outlast any real turn
(a subagent plus a web search can run for minutes), so it only ever comes
into play after a crash.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph.store.base import BaseStore

RUN_LEASE_NAMESPACE = ("spark-match", "_runs")


@dataclass(frozen=True, slots=True)
class RunLease:
    """A turn in flight on a conversation."""

    run_id: str
    started_at: datetime
    expires_at: datetime


def _parse(value: Any) -> datetime | None:
    """A stored ISO timestamp, or ``None`` if it is unreadable.

    Unreadable covers a lease written by an older build and a value
    corrupted by hand. Both are treated as "no usable lease" rather than
    as an error: refusing every turn on a conversation because its lease
    row is malformed would be a worse failure than allowing one.
    """
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _lease_from(value: Any, now: datetime) -> RunLease | None:
    """A live lease from a stored record, or ``None`` if it expired."""
    if not isinstance(value, dict):
        return None

    expires_at = _parse(value.get("expires_at"))
    if expires_at is None or expires_at <= now:
        return None

    run_id = value.get("run_id")
    return RunLease(
        run_id=str(run_id) if run_id else "",
        started_at=_parse(value.get("started_at")) or now,
        expires_at=expires_at,
    )


async def active_run(store: BaseStore | None, thread_id: str) -> RunLease | None:
    """The turn currently running on ``thread_id``, if any."""
    if store is None:
        return None

    item = await store.aget(RUN_LEASE_NAMESPACE, thread_id)
    return None if item is None else _lease_from(item.value, datetime.now(UTC))


async def acquire_run_lease(
    store: BaseStore | None,
    thread_id: str,
    run_id: str,
    ttl_seconds: float,
) -> RunLease | None:
    """Take the lease on ``thread_id``.

    ``None`` means one thing only: **another turn holds it**. That keeps
    the call site down to one question, instead of asking it and then
    re-deriving whether leasing was even on.

    A ``None`` store (graph built without persistence, most unit tests) or
    a non-positive TTL disables leasing, and then this returns a lease
    that was never written: the turn proceeds and releasing is a no-op.
    Same escape hatches as ``assert_thread_ownership`` and the daily
    budget, so a local run without a database behaves as it always did.
    """
    now = datetime.now(UTC)
    lease = RunLease(
        run_id=run_id,
        started_at=now,
        expires_at=now + timedelta(seconds=max(ttl_seconds, 0.0)),
    )

    if store is None or ttl_seconds <= 0:
        return lease

    existing = await store.aget(RUN_LEASE_NAMESPACE, thread_id)
    if existing is not None and _lease_from(existing.value, now) is not None:
        return None

    await store.aput(
        RUN_LEASE_NAMESPACE,
        thread_id,
        {
            "run_id": lease.run_id,
            "started_at": lease.started_at.isoformat(),
            "expires_at": lease.expires_at.isoformat(),
        },
    )
    return lease


async def release_run_lease(store: BaseStore | None, thread_id: str, run_id: str) -> None:
    """Give the lease back, if it is still ours.

    The ``run_id`` check is what keeps a turn that overran its TTL from
    releasing the lease of the turn that legitimately took over
    afterwards. Releasing is best-effort by design: this runs in a
    ``finally``, and a failure here must not replace whatever error was
    already on its way to the student. The lease expires on its own.
    """
    if store is None:
        return

    item = await store.aget(RUN_LEASE_NAMESPACE, thread_id)
    if item is None:
        return

    stored = item.value.get("run_id") if isinstance(item.value, dict) else None
    if stored == run_id:
        await store.adelete(RUN_LEASE_NAMESPACE, thread_id)


__all__ = [
    "RUN_LEASE_NAMESPACE",
    "RunLease",
    "acquire_run_lease",
    "active_run",
    "release_run_lease",
]
