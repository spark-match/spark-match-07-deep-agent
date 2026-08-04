"""Tests for the web_search handler.

These tests exercise the pure handler directly (no @tool decorator, no
LLM dependency). Tavily/DuckDuckGo calls are monkeypatched at the module
level — no real network access or API keys required.

``web_search_handler`` is async (Sprint 8, task 8.1): ``_search_tavily``
is awaited directly (its fakes below are ``async def``), while
``_search_duckduckgo`` stays a plain sync fake since it's invoked through
``asyncio.to_thread`` at the call site, not awaited directly.
"""

import asyncio
import time

import pytest

from src import budget
from src.tools.web_search import handler as web_search_module
from src.tools.web_search.handler import web_search_handler


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Fresh settings cache and a clean budget counter for every test."""
    from src.config import get_settings

    get_settings.cache_clear()
    budget.reset_session_budget()
    yield
    get_settings.cache_clear()
    budget.reset_session_budget()


def _ok_results(n: int = 2) -> list[dict[str, str]]:
    return [{"title": f"r{i}", "url": f"https://example.com/{i}", "content": "x"} for i in range(n)]


async def _async_ok_results(*_a: object, n: int = 2) -> list[dict[str, str]]:
    return _ok_results(n)


class TestWebSearchHandlerValidation:
    """Input validation, independent of any provider."""

    async def test_empty_query_returns_error(self):
        result = await web_search_handler(query="")
        assert result["status"] == "error"
        assert result["data"] is None
        assert "non-empty" in result["errors"][0]

    async def test_whitespace_only_query_returns_error(self):
        result = await web_search_handler(query="   ")
        assert result["status"] == "error"

    async def test_invalid_max_results_falls_back_to_default(self, monkeypatch):
        """max_results < 1 or non-int should not crash — falls back to 5."""
        captured = {}

        async def fake_tavily(query, max_results):
            captured["max_results"] = max_results
            return _ok_results()

        monkeypatch.setattr(web_search_module, "_search_tavily", fake_tavily)
        result = await web_search_handler(query="python", max_results=0)
        assert result["status"] == "success"
        assert captured["max_results"] == 5


class TestWebSearchHandlerBudget:
    """Budget enforcement (B2/B3): cap<=0 is unlimited, no double increment."""

    async def test_budget_exhausted_refuses_call(self, monkeypatch):
        monkeypatch.setenv("SPARK_MAX_WEB_SEARCHES_PER_SESSION", "1")
        from src.config import get_settings

        get_settings.cache_clear()
        budget.increment_web_search()  # consume the only slot

        called = {"tavily": False}

        async def fake_tavily(*a):
            called["tavily"] = True
            return _ok_results()

        monkeypatch.setattr(web_search_module, "_search_tavily", fake_tavily)
        result = await web_search_handler(query="python")
        assert result["status"] == "budget_exhausted"
        assert called["tavily"] is False  # refused before doing any work

    async def test_budget_zero_is_unlimited(self, monkeypatch):
        """B2: a cap of 0 must mean unlimited, not 'always exhausted'."""
        monkeypatch.setenv("SPARK_MAX_WEB_SEARCHES_PER_SESSION", "0")
        from src.config import get_settings

        get_settings.cache_clear()
        # Simulate a session that has already "used" far more than any
        # positive cap would allow — with cap=0 it must still succeed.
        for _ in range(50):
            budget.increment_web_search()

        monkeypatch.setattr(web_search_module, "_search_tavily", _async_ok_results)
        result = await web_search_handler(query="python")
        assert result["status"] == "success"

    async def test_no_double_increment_when_tavily_returns_empty(self, monkeypatch):
        """B3: Tavily returning [] (not raising) must not charge the budget
        twice once DuckDuckGo's fallback also runs."""

        async def empty_tavily(*a):
            return []

        monkeypatch.setattr(web_search_module, "_search_tavily", empty_tavily)
        monkeypatch.setattr(web_search_module, "_search_duckduckgo", lambda *a: _ok_results())

        result = await web_search_handler(query="python")
        assert result["status"] == "success"
        assert result["data"]["provider"] == "duckduckgo"
        assert budget.get_web_search_count() == 1

    async def test_single_increment_on_tavily_success(self, monkeypatch):
        monkeypatch.setattr(web_search_module, "_search_tavily", _async_ok_results)
        result = await web_search_handler(query="python")
        assert result["status"] == "success"
        assert budget.get_web_search_count() == 1

    async def test_single_increment_on_duckduckgo_fallback(self, monkeypatch):
        async def raising_tavily(*a):
            raise ValueError("no api key")

        monkeypatch.setattr(web_search_module, "_search_tavily", raising_tavily)
        monkeypatch.setattr(web_search_module, "_search_duckduckgo", lambda *a: _ok_results())
        result = await web_search_handler(query="python")
        assert result["status"] == "success"
        assert budget.get_web_search_count() == 1


class TestWebSearchHandlerProviders:
    """Provider selection and fallback behavior."""

    async def test_tavily_success_returns_tavily_provider(self, monkeypatch):
        async def fake_tavily(*a):
            return _ok_results(3)

        monkeypatch.setattr(web_search_module, "_search_tavily", fake_tavily)
        result = await web_search_handler(query="python")
        assert result["status"] == "success"
        assert result["data"]["provider"] == "tavily"
        assert len(result["data"]["results"]) == 3

    async def test_tavily_failure_falls_back_to_duckduckgo(self, monkeypatch):
        async def raising_tavily(*a):
            raise RuntimeError("tavily down")

        monkeypatch.setattr(web_search_module, "_search_tavily", raising_tavily)
        monkeypatch.setattr(web_search_module, "_search_duckduckgo", lambda *a: _ok_results())
        result = await web_search_handler(query="python")
        assert result["status"] == "success"
        assert result["data"]["provider"] == "duckduckgo"

    async def test_both_providers_fail_returns_error(self, monkeypatch):
        async def raising_tavily(*a):
            raise RuntimeError("tavily down")

        def raising_ddg(*a):
            raise RuntimeError("ddg down")

        monkeypatch.setattr(web_search_module, "_search_tavily", raising_tavily)
        monkeypatch.setattr(web_search_module, "_search_duckduckgo", raising_ddg)
        result = await web_search_handler(query="python")
        assert result["status"] == "error"
        assert result["data"] is None
        assert "unavailable" in result["errors"][0].lower()
        # Neither attempt should have charged the budget.
        assert budget.get_web_search_count() == 0


class TestWebSearchHandlerTypedTavilyErrors:
    """Sprint 8, task 8.2: distinguish 401 (API key) from 429/timeout/network.

    Only the API-key category (401-equivalent: InvalidAPIKeyError /
    MissingAPIKeyError) must skip the DuckDuckGo fallback — it's our own
    misconfiguration, not something a different search provider can fix.
    Every other Tavily failure (429, timeout, network, or anything
    unclassified) must keep falling back to DuckDuckGo, exactly like
    before this task.
    """

    async def test_invalid_api_key_does_not_fall_back_to_duckduckgo(self, monkeypatch):
        from tavily.errors import InvalidAPIKeyError

        async def raising_tavily(*a):
            raise InvalidAPIKeyError("invalid key")

        called = {"ddg": False}

        def spy_ddg(*a):
            called["ddg"] = True
            return _ok_results()

        monkeypatch.setattr(web_search_module, "_search_tavily", raising_tavily)
        monkeypatch.setattr(web_search_module, "_search_duckduckgo", spy_ddg)

        result = await web_search_handler(query="python")

        assert result["status"] == "error"
        assert called["ddg"] is False
        assert "API key" in result["errors"][0]
        assert budget.get_web_search_count() == 0

    async def test_missing_api_key_does_not_fall_back_to_duckduckgo(self, monkeypatch):
        from tavily.errors import MissingAPIKeyError

        async def raising_tavily(*a):
            raise MissingAPIKeyError()

        called = {"ddg": False}

        def spy_ddg(*a):
            called["ddg"] = True
            return _ok_results()

        monkeypatch.setattr(web_search_module, "_search_tavily", raising_tavily)
        monkeypatch.setattr(web_search_module, "_search_duckduckgo", spy_ddg)

        result = await web_search_handler(query="python")

        assert result["status"] == "error"
        assert called["ddg"] is False

    async def test_rate_limit_falls_back_to_duckduckgo(self, monkeypatch):
        """429 must still trigger the DuckDuckGo fallback."""
        from tavily.errors import UsageLimitExceededError

        async def raising_tavily(*a):
            raise UsageLimitExceededError("rate limited")

        monkeypatch.setattr(web_search_module, "_search_tavily", raising_tavily)
        monkeypatch.setattr(web_search_module, "_search_duckduckgo", lambda *a: _ok_results())

        result = await web_search_handler(query="python")

        assert result["status"] == "success"
        assert result["data"]["provider"] == "duckduckgo"

    async def test_timeout_falls_back_to_duckduckgo(self, monkeypatch):
        """Tavily's own TimeoutError (wrapping httpx.TimeoutException) must
        still trigger the DuckDuckGo fallback."""
        from tavily.errors import TimeoutError as TavilyTimeoutError

        async def raising_tavily(*a):
            raise TavilyTimeoutError(60)

        monkeypatch.setattr(web_search_module, "_search_tavily", raising_tavily)
        monkeypatch.setattr(web_search_module, "_search_duckduckgo", lambda *a: _ok_results())

        result = await web_search_handler(query="python")

        assert result["status"] == "success"
        assert result["data"]["provider"] == "duckduckgo"

    async def test_network_error_falls_back_to_duckduckgo(self, monkeypatch):
        """A raw httpx.TransportError (DNS/connection failure, not wrapped
        by tavily) must still trigger the DuckDuckGo fallback."""
        import httpx

        async def raising_tavily(*a):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(web_search_module, "_search_tavily", raising_tavily)
        monkeypatch.setattr(web_search_module, "_search_duckduckgo", lambda *a: _ok_results())

        result = await web_search_handler(query="python")

        assert result["status"] == "success"
        assert result["data"]["provider"] == "duckduckgo"

    async def test_forbidden_error_falls_back_to_duckduckgo(self, monkeypatch):
        """ForbiddenError (403) isn't explicitly named in the roadmap's
        401/429/timeout/network list — it falls into the generic catch-all,
        preserving the pre-8.2 safe default of falling back rather than
        failing hard on an unclassified category."""
        from tavily.errors import ForbiddenError

        async def raising_tavily(*a):
            raise ForbiddenError("forbidden")

        monkeypatch.setattr(web_search_module, "_search_tavily", raising_tavily)
        monkeypatch.setattr(web_search_module, "_search_duckduckgo", lambda *a: _ok_results())

        result = await web_search_handler(query="python")

        assert result["status"] == "success"
        assert result["data"]["provider"] == "duckduckgo"


class TestWebSearchHandlerDoesNotBlockEventLoop:
    """Sprint 8, task 8.1 DoD: web_search must not block the event loop.

    DuckDuckGo (``_search_duckduckgo``) is a plain sync function with no
    async client upstream — ``web_search_handler`` must run it via
    ``asyncio.to_thread`` so a slow DDG call can't starve other concurrent
    coroutines. We prove this by making DDG block with ``time.sleep`` (a
    real, un-yielding block) and running several calls concurrently: if
    they were actually serialized on the event loop, wall time would be
    roughly N * sleep_seconds; offloaded to threads, it stays close to a
    single sleep_seconds regardless of N (well under the default thread
    pool size).
    """

    async def test_concurrent_duckduckgo_fallback_runs_in_worker_threads(self, monkeypatch):
        sleep_seconds = 0.2
        concurrency = 4

        async def raising_tavily(*a):
            raise RuntimeError("tavily down")

        def blocking_ddg(*a):
            time.sleep(sleep_seconds)
            return _ok_results()

        monkeypatch.setattr(web_search_module, "_search_tavily", raising_tavily)
        monkeypatch.setattr(web_search_module, "_search_duckduckgo", blocking_ddg)

        start = time.monotonic()
        results = await asyncio.gather(
            *(web_search_handler(query=f"python {i}") for i in range(concurrency))
        )
        elapsed = time.monotonic() - start

        assert all(r["status"] == "success" for r in results)
        # Serialized on the loop: ~= concurrency * sleep_seconds (0.8s here).
        # Offloaded to threads: ~= sleep_seconds regardless of concurrency.
        # Generous margin for CI jitter, still well below the serialized bound.
        assert elapsed < sleep_seconds * concurrency * 0.75
