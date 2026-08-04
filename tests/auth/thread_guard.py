"""Tests for the thread-ownership guard (Sprint 7, task 7.B)."""

import pytest
from fastapi import HTTPException
from langgraph.store.memory import InMemoryStore

from src.auth.thread_guard import assert_thread_ownership, derive_thread_id


class TestDeriveThreadId:
    def test_deterministic_for_same_pair(self):
        a = derive_thread_id("user-1", "client-thread-a")
        b = derive_thread_id("user-1", "client-thread-a")
        assert a == b

    def test_different_users_never_collide(self):
        a = derive_thread_id("user-1", "same-client-value")
        b = derive_thread_id("user-2", "same-client-value")
        assert a != b

    def test_different_client_ids_never_collide(self):
        a = derive_thread_id("user-1", "thread-a")
        b = derive_thread_id("user-1", "thread-b")
        assert a != b


class TestAssertThreadOwnership:
    async def test_none_store_is_a_noop(self):
        # Must not raise: graphs built without persistence have nowhere to
        # register ownership and nothing durable to protect either.
        await assert_thread_ownership(None, "t_abc", "user-1")

    async def test_first_call_registers_owner(self):
        store = InMemoryStore()
        await assert_thread_ownership(store, "t_abc", "user-1")
        item = store.get(("spark-match", "_threads"), "t_abc")
        assert item is not None
        assert item.value["user_id"] == "user-1"

    async def test_same_owner_second_call_is_allowed(self):
        store = InMemoryStore()
        await assert_thread_ownership(store, "t_abc", "user-1")
        await assert_thread_ownership(store, "t_abc", "user-1")  # must not raise

    async def test_different_owner_is_rejected(self):
        store = InMemoryStore()
        await assert_thread_ownership(store, "t_abc", "user-1")
        with pytest.raises(HTTPException) as exc_info:
            await assert_thread_ownership(store, "t_abc", "user-2")
        assert exc_info.value.status_code == 403
