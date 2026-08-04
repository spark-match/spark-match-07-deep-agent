"""Composite filesystem backend for the agent (Sprint 6, task 6.B).

Routes ``/memories/*`` to a per-user :class:`~deepagents.backends.StoreBackend`
(persistent, long-term), while everything else stays on the ephemeral
:class:`~deepagents.backends.StateBackend` (scratchpad for the current run).
This is what makes ``read_file``/``write_file``/``edit_file`` over
``/memories/...`` durable across sessions without touching the rest of the
agent's filesystem semantics.
"""

from __future__ import annotations

from typing import Any

from deepagents.backends import CompositeBackend, StateBackend, StoreBackend
from langgraph.runtime import Runtime

from src.agent.user_context import get_user_id

MEMORY_ROOT = "/memories/"


def _memory_namespace(runtime: Runtime[Any]) -> tuple[str, ...]:
    """Per-user namespace for the memory-files store route.

    No wildcards (forbidden by ``StoreBackend``); the placeholder
    ``user_id`` (Sprint 6, see :mod:`src.agent.user_context`) still yields a
    well-formed, non-wildcard namespace.
    """
    return ("spark-match", get_user_id(runtime), "files")


def build_backend() -> CompositeBackend:
    """Build the agent's composite filesystem backend.

    ``/memories/...`` -> persistent per-user store.
    everything else -> ephemeral state (cleared at the end of the run).
    """
    return CompositeBackend(
        default=StateBackend(),
        routes={MEMORY_ROOT: StoreBackend(namespace=_memory_namespace)},
    )


__all__ = ["MEMORY_ROOT", "build_backend"]
