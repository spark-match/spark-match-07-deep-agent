"""Persistence factory — build_persistence() smoke and behavior tests.

Only exercises the ``memory`` and ``sqlite`` profiles for real: both must
work fully offline (hard rule #7 in AGENTS.md). ``postgres`` is asserted to
fail loudly with a clear message instead of silently misbehaving, since it
needs Secrets Manager DSN resolution (roadmap task 6.A.3) not implemented yet.
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.memory import InMemoryStore

from src.config import get_settings
from src.persistence.factory import build_persistence


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


class TestPostgresProfile:
    """SPARK_PERSISTENCE_BACKEND=postgres — not implemented yet (task 6.A.3)."""

    async def test_raises_not_implemented_with_a_clear_message(self, monkeypatch):
        monkeypatch.setenv("SPARK_PERSISTENCE_BACKEND", "postgres")
        get_settings.cache_clear()

        with pytest.raises(NotImplementedError, match=r"6\.A\.3"):
            async with build_persistence():
                pass
