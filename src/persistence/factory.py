"""Persistence factory — builds checkpointer + store per SPARK_PERSISTENCE_BACKEND.

Sprint 6: conversational memory. Selects among 3 profiles via
``settings.persistence_backend``. ``memory`` and ``sqlite`` never touch AWS
(hard rule #7 in AGENTS.md — the TFP evaluator must run this repo locally
without an AWS account). ``postgres`` is the production profile: resuelve el
DSN via SSM -> Secrets Manager (ver :mod:`src.persistence.secrets`) y es el
unico donde el store de largo plazo tambien sobrevive un restart.
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
from src.persistence.secrets import AGENT_SCHEMA


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
    #
    # Unico perfil donde el store de largo plazo tambien sobrevive un
    # restart: sqlite solo persiste el checkpointer porque LangGraph no
    # tiene un BaseStore sobre sqlite.
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres.aio import AsyncPostgresStore

    from src.persistence.secrets import resolve_postgres_dsn

    dsn = resolve_postgres_dsn()

    async with (
        AsyncPostgresSaver.from_conn_string(dsn) as saver,
        AsyncPostgresStore.from_conn_string(dsn) as store,
    ):
        # El schema lo crea el agente, no una migracion del backend: el
        # backend es dueno de `public` y no deberia conocer las tablas de
        # LangGraph. El DSN ya trae search_path=agent, pero `setup()` corre
        # DDL y el schema tiene que existir antes.
        await _ensure_schema(saver)
        await saver.setup()
        await store.setup()
        yield Persistence(checkpointer=saver, store=store)


async def _ensure_schema(saver: Any) -> None:
    """``CREATE SCHEMA IF NOT EXISTS agent`` sobre la conexion del saver.

    Se reutiliza el pool del checkpointer en vez de abrir una conexion
    aparte: es una sola sentencia idempotente en el arranque.
    """
    async with saver.conn.cursor() as cur:
        await cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{AGENT_SCHEMA}"')
