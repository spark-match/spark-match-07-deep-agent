"""Unit tests for the per-user thread index (src/threads/registry.py)."""

import pytest
from langgraph.store.memory import InMemoryStore

from src.auth.thread_guard import THREAD_OWNER_NAMESPACE, derive_thread_id
from src.threads.registry import (
    DEFAULT_TITLE,
    MAX_TITLE_LENGTH,
    build_title,
    forget_thread,
    list_threads,
    record_thread_activity,
    thread_index_namespace,
)


@pytest.fixture
def store():
    return InMemoryStore()


class TestBuildTitle:
    def test_uses_the_opening_message(self):
        assert build_title("¿Qué carreras van con matemáticas?") == (
            "¿Qué carreras van con matemáticas?"
        )

    def test_collapses_whitespace(self):
        assert build_title("  hola\n\n  mundo  ") == "hola mundo"

    def test_truncates_long_messages_with_an_ellipsis(self):
        title = build_title("a" * 200)

        assert len(title) == MAX_TITLE_LENGTH
        assert title.endswith("…")

    @pytest.mark.parametrize("seed", [None, "", "   ", "\n\t"])
    def test_falls_back_to_a_default(self, seed):
        assert build_title(seed) == DEFAULT_TITLE


class TestRecordThreadActivity:
    async def test_creates_an_entry_keyed_by_derived_id(self, store):
        await record_thread_activity(store, "u1", "t_abc", "client-1", "hola mundo")

        item = await store.aget(thread_index_namespace("u1"), "t_abc")

        assert item is not None
        assert item.value["client_thread_id"] == "client-1"
        assert item.value["title"] == "hola mundo"

    async def test_keeps_the_client_side_id(self, store):
        """The derived id is one-way, so without this the frontend could
        never reopen a listed conversation."""
        await record_thread_activity(store, "u1", "t_abc", "client-1", "hola")

        [thread] = await list_threads(store, "u1")

        assert thread["thread_id"] == "client-1"

    async def test_refreshes_updated_at_on_later_turns(self, store):
        await record_thread_activity(store, "u1", "t_abc", "client-1", "primera")
        first = (await store.aget(thread_index_namespace("u1"), "t_abc")).value["updated_at"]

        await record_thread_activity(store, "u1", "t_abc", "client-1", "segunda")
        second = (await store.aget(thread_index_namespace("u1"), "t_abc")).value["updated_at"]

        assert second >= first

    async def test_does_not_rewrite_the_title_on_later_turns(self, store):
        """A sidebar label that kept changing as the conversation went on
        would be worse than a slightly stale one."""
        await record_thread_activity(store, "u1", "t_abc", "client-1", "pregunta original")
        await record_thread_activity(store, "u1", "t_abc", "client-1", "algo distinto")

        item = await store.aget(thread_index_namespace("u1"), "t_abc")

        assert item.value["title"] == "pregunta original"

    async def test_backfills_a_default_title_once_text_arrives(self, store):
        await record_thread_activity(store, "u1", "t_abc", "client-1", None)
        await record_thread_activity(store, "u1", "t_abc", "client-1", "ya hay texto")

        item = await store.aget(thread_index_namespace("u1"), "t_abc")

        assert item.value["title"] == "ya hay texto"

    async def test_none_store_is_a_noop(self):
        await record_thread_activity(None, "u1", "t_abc", "client-1", "hola")


class TestListThreads:
    async def test_orders_by_most_recent_activity(self, store):
        await record_thread_activity(store, "u1", "t_old", "old", "vieja")
        await record_thread_activity(store, "u1", "t_new", "new", "nueva")
        # Touch the older one so it becomes the most recent.
        await record_thread_activity(store, "u1", "t_old", "old", "vieja")

        threads = await list_threads(store, "u1")

        assert [t["thread_id"] for t in threads] == ["old", "new"]

    async def test_does_not_leak_other_users_threads(self, store):
        await record_thread_activity(store, "u1", "t_mine", "mine", "mía")
        await record_thread_activity(store, "u2", "t_theirs", "theirs", "suya")

        threads = await list_threads(store, "u1")

        assert [t["thread_id"] for t in threads] == ["mine"]

    async def test_never_returns_the_derived_id(self, store):
        """It is the checkpointer key; the client has no business with it."""
        await record_thread_activity(store, "u1", "t_abc", "client-1", "hola")

        [thread] = await list_threads(store, "u1")

        assert "derived_thread_id" in thread  # stripped at the HTTP layer
        assert thread["thread_id"] != thread["derived_thread_id"]

    async def test_paginates(self, store):
        for i in range(5):
            await record_thread_activity(store, "u1", f"t_{i}", f"c{i}", f"conv {i}")

        page = await list_threads(store, "u1", limit=2, offset=2)

        assert len(page) == 2

    async def test_skips_entries_without_a_client_id(self, store):
        """Corrupt or pre-index entries are unusable — the frontend could
        not reopen them — so they are dropped rather than shown broken."""
        await store.aput(thread_index_namespace("u1"), "t_broken", {"title": "sin id"})
        await record_thread_activity(store, "u1", "t_ok", "ok", "buena")

        threads = await list_threads(store, "u1")

        assert [t["thread_id"] for t in threads] == ["ok"]

    async def test_empty_for_a_user_with_no_threads(self, store):
        assert await list_threads(store, "nobody") == []

    async def test_none_store_returns_empty(self):
        assert await list_threads(None, "u1") == []


class TestForgetThread:
    async def test_removes_the_index_entry(self, store):
        await record_thread_activity(store, "u1", "t_abc", "client-1", "hola")

        await forget_thread(store, "u1", "t_abc")

        assert await list_threads(store, "u1") == []

    async def test_releases_the_ownership_record(self, store):
        """Leaving it behind would keep the derived id permanently claimed,
        so reusing the same client-side id after a delete would 403 the
        student on their own thread."""
        thread_id = derive_thread_id("u1", "client-1")
        await store.aput(THREAD_OWNER_NAMESPACE, thread_id, {"user_id": "u1"})
        await record_thread_activity(store, "u1", thread_id, "client-1", "hola")

        await forget_thread(store, "u1", thread_id)

        assert await store.aget(THREAD_OWNER_NAMESPACE, thread_id) is None

    async def test_none_store_is_a_noop(self):
        await forget_thread(None, "u1", "t_abc")
