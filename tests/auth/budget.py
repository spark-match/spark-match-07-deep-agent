"""Tests for the per-user daily request budget (Sprint 7, task 7.E.4)."""

import pytest
from fastapi import HTTPException
from langgraph.store.memory import InMemoryStore

from src.auth.budget import check_and_increment_daily_budget


class TestCheckAndIncrementDailyBudget:
    async def test_none_store_is_a_noop(self):
        # Must not raise: graphs built without persistence have nowhere
        # durable to track the budget.
        await check_and_increment_daily_budget(None, "user-1", max_per_day=1)

    async def test_zero_max_disables_the_cap(self):
        store = InMemoryStore()
        for _ in range(5):
            await check_and_increment_daily_budget(store, "user-1", max_per_day=0)

    async def test_under_the_cap_is_allowed_and_increments(self):
        store = InMemoryStore()
        await check_and_increment_daily_budget(store, "user-1", max_per_day=3)
        await check_and_increment_daily_budget(store, "user-1", max_per_day=3)

        namespace = ("spark-match", "user-1", "budget")
        from datetime import UTC, datetime

        today = datetime.now(UTC).date().isoformat()
        item = store.get(namespace, today)
        assert item is not None
        assert item.value["count"] == 2

    async def test_exceeding_the_cap_raises_429(self):
        store = InMemoryStore()
        await check_and_increment_daily_budget(store, "user-1", max_per_day=2)
        await check_and_increment_daily_budget(store, "user-1", max_per_day=2)

        with pytest.raises(HTTPException) as exc_info:
            await check_and_increment_daily_budget(store, "user-1", max_per_day=2)
        assert exc_info.value.status_code == 429

    async def test_different_users_have_independent_budgets(self):
        store = InMemoryStore()
        await check_and_increment_daily_budget(store, "user-1", max_per_day=1)

        # user-2 must not be affected by user-1 having exhausted its budget.
        await check_and_increment_daily_budget(store, "user-2", max_per_day=1)
