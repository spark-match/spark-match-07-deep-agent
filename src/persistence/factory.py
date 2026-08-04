"""Persistence factory — builds checkpointer + store per SPARK_PERSISTENCE_BACKEND.

Sprint 6: conversational memory. Selects among 3 profiles via
``settings.persistence_backend``. ``memory`` and ``sqlite`` never touch AWS
(hard rule #7 in AGENTS.md — the TFP evaluator must run this repo locally
without an AWS account). ``postgres`` is production-only and requires
Secrets Manager DSN resolution (roadmap task 6.A.3), not implemented yet.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

from src.config import PersistenceBackend, get_settings


@dataclass(slots=True)
class Persistence:
    """Bundles what ``create_deep_agent`` needs for conversational memory.

    ``checkpointer`` is short-term, per-``thread_id`` (conversation turns).
    ``store`` is long-term, meant to be partitioned per-``user_id`` (profile,
    preferences, memory files) once Sprint 7 wires real ``user_id``s.
    """

    checkpointer: BaseCheckpointSaver[Any]
    store: BaseStore


@contextlib.asynccontextmanager
async def build_persistence() -> AsyncIterator[Persistence]:
    """Build checkpointer + store for ``settings.persistence_backend``.

    Use as an async context manager from the FastAPI lifespan so any
    underlying connection pools (sqlite/postgres) close cleanly on shutdown.
    """
    settings = get_settings()

    if settings.persistence_backend is PersistenceBackend.MEMORY:
        yield Persistence(checkpointer=InMemorySaver(), store=InMemoryStore())
        return

    if settings.persistence_backend is PersistenceBackend.SQLITE:
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        db_path = Path(settings.sqlite_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
            await saver.setup()
            # InMemoryStore for long-term memory under the sqlite profile:
            # LangGraph has no native sqlite-backed BaseStore. Profile/prefs
            # data will not survive a process restart under this profile —
            # only the checkpointer (conversation turns) does.
            yield Persistence(checkpointer=saver, store=InMemoryStore())
        return

    # PersistenceBackend.POSTGRES
    raise NotImplementedError(
        "El perfil 'postgres' requiere resolver el DSN via Secrets Manager "
        "(tarea 6.A.3 del ROADMAP-2026-08.md), aun no implementado. Usa "
        "SPARK_PERSISTENCE_BACKEND=memory o sqlite mientras tanto."
    )
