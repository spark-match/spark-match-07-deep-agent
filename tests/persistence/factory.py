"""Persistence factory — build_persistence() smoke and behavior tests.

Only exercises the ``memory`` and ``sqlite`` profiles for real: both must
work fully offline (hard rule #7 in AGENTS.md). ``postgres`` necesita un RDS,
asi que aca solo se verifica el cableado -- que lea el override local del DSN,
que construya el DSN correcto desde el JSON de Secrets Manager, y que use el
schema `agent` -- con dobles en lugar de una base real.
"""

from __future__ import annotations

import json

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.memory import InMemoryStore

from src.config import get_settings
from src.persistence.factory import build_persistence
from src.persistence.secrets import _dsn_from_secret, resolve_postgres_dsn

#: Shape que escribe modules/rds-postgres en spark-match-02-infrastructure.
_CREDS = {
    "host": "db.example.com",
    "port": 5432,
    "database": "sparkmatch",
    "username": "identity",
    "password": "shh",
}


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    """Each test gets a fresh Settings cache (env changes invalidate it)."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestMemoryProfile:
    """SPARK_PERSISTENCE_BACKEND=memory — the trivial, fully-offline default."""

    async def test_yields_in_memory_checkpointer_and_store(self, monkeypatch):
        monkeypatch.setenv("SPARK_PERSISTENCE_BACKEND", "memory")
        get_settings.cache_clear()

        async with build_persistence() as persistence:
            assert isinstance(persistence.checkpointer, InMemorySaver)
            assert isinstance(persistence.store, InMemoryStore)


class TestSqliteProfile:
    """SPARK_PERSISTENCE_BACKEND=sqlite — must work without AWS (hard rule #7)."""

    async def test_yields_sqlite_checkpointer_and_creates_the_db_file(self, monkeypatch, tmp_path):
        db_path = tmp_path / "nested" / "checkpoints.sqlite"
        monkeypatch.setenv("SPARK_PERSISTENCE_BACKEND", "sqlite")
        monkeypatch.setenv("SPARK_SQLITE_PATH", str(db_path))
        get_settings.cache_clear()

        async with build_persistence() as persistence:
            assert isinstance(persistence.checkpointer, AsyncSqliteSaver)
            assert isinstance(persistence.store, InMemoryStore)
            assert db_path.exists()

    async def test_two_turns_with_the_same_thread_id_share_checkpoint_history(
        self, monkeypatch, tmp_path
    ):
        """The whole point of Sprint 6: a real round trip through sqlite.

        Regression guard for 6.G — proves the checkpointer genuinely
        persists conversation state keyed by ``thread_id``, not just that
        the object is constructed.
        """
        db_path = tmp_path / "checkpoints.sqlite"
        monkeypatch.setenv("SPARK_PERSISTENCE_BACKEND", "sqlite")
        monkeypatch.setenv("SPARK_SQLITE_PATH", str(db_path))
        get_settings.cache_clear()

        config = {"configurable": {"thread_id": "shared-thread", "checkpoint_ns": ""}}
        checkpoint_1 = {
            "v": 1,
            "id": "1",
            "ts": "2026-08-04T00:00:00+00:00",
            "channel_values": {"turn": 1},
            "channel_versions": {},
            "versions_seen": {},
        }

        async with build_persistence() as persistence:
            await persistence.checkpointer.aput(config, checkpoint_1, {}, {})

        # Re-open a saver against the same file — simulates a second HTTP
        # request hitting a fresh Persistence built by the same lifespan.
        async with AsyncSqliteSaver.from_conn_string(str(db_path)) as saver:
            tuple_ = await saver.aget_tuple(config)
            assert tuple_ is not None
            assert tuple_.checkpoint["channel_values"]["turn"] == 1


class TestPostgresDsnResolution:
    """resolve_postgres_dsn() — override local vs. camino AWS."""

    def test_prefers_the_local_override_and_never_touches_aws(self, monkeypatch):
        monkeypatch.setenv("SPARK_POSTGRES_DSN", "postgresql://u:p@localhost:5432/agent")
        get_settings.cache_clear()

        assert resolve_postgres_dsn() == "postgresql://u:p@localhost:5432/agent"

    def test_builds_the_dsn_from_the_secrets_manager_payload(self, monkeypatch):
        monkeypatch.delenv("SPARK_POSTGRES_DSN", raising=False)
        get_settings.cache_clear()
        monkeypatch.setattr(
            "src.persistence.secrets._fetch_from_aws",
            lambda: _dsn_from_secret(json.dumps(_CREDS)),
        )

        dsn = resolve_postgres_dsn()

        assert dsn.startswith("postgresql://identity:")
        assert "@db.example.com:5432/sparkmatch" in dsn

    def test_requires_tls_because_rds_pg15_rejects_plaintext(self):
        assert "sslmode=require" in _dsn_from_secret(json.dumps(_CREDS))

    def test_pins_the_search_path_to_the_agent_schema(self):
        # Sin esto, las tablas de LangGraph caen en `public`, donde viven las
        # migraciones del backend. Comparten base; no deben compartir namespace.
        assert "options=-csearch_path%3Dagent" in _dsn_from_secret(json.dumps(_CREDS))

    def test_percent_encodes_credentials_with_reserved_characters(self):
        creds = {**_CREDS, "password": "p@ss:w/rd?"}

        dsn = _dsn_from_secret(json.dumps(creds))

        # La contrasena cruda partiria el DSN en el `@` y en el `:`.
        assert "p%40ss%3Aw%2Frd%3F" in dsn
        assert "@db.example.com" in dsn

    def test_rejects_a_payload_missing_keys(self):
        with pytest.raises(ValueError, match="faltan claves"):
            _dsn_from_secret(json.dumps({"host": "h", "port": 5432}))

    def test_rejects_a_non_json_payload(self):
        with pytest.raises(ValueError, match="no es JSON valido"):
            _dsn_from_secret("not-json{")
