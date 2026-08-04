"""Tests for the web_search handler.

These tests exercise the pure handler directly (no @tool decorator, no
LLM dependency). Tavily/DuckDuckGo calls are monkeypatched at the module
level — no real network access or API keys required.
"""

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


class TestWebSearchHandlerValidation:
    """Input validation, independent of any provider."""

    def test_empty_query_returns_error(self):
        result = web_search_handler(query="")
        assert result["status"] == "error"
        assert result["data"] is None
        assert "non-empty" in result["errors"][0]

    def test_whitespace_only_query_returns_error(self):
        result = web_search_handler(query="   ")
        assert result["status"] == "error"

    def test_invalid_max_results_falls_back_to_default(self, monkeypatch):
        """max_results < 1 or non-int should not crash — falls back to 5."""
        captured = {}

        def fake_tavily(query, max_results):
            captured["max_results"] = max_results
            return _ok_results()

        monkeypatch.setattr(web_search_module, "_search_tavily", fake_tavily)
        result = web_search_handler(query="python", max_results=0)
        assert result["status"] == "success"
        assert captured["max_results"] == 5


class TestWebSearchHandlerBudget:
    """Budget enforcement (B2/B3): cap<=0 is unlimited, no double increment."""

    def test_budget_exhausted_refuses_call(self, monkeypatch):
        monkeypatch.setenv("SPARK_MAX_WEB_SEARCHES_PER_SESSION", "1")
        from src.config import get_settings

        get_settings.cache_clear()
        budget.increment_web_search()  # consume the only slot

        called = {"tavily": False}
        monkeypatch.setattr(
            web_search_module,
            "_search_tavily",
            lambda *a: called.__setitem__("tavily", True) or _ok_results(),
        )
        result = web_search_handler(query="python")
        assert result["status"] == "budget_exhausted"
        assert called["tavily"] is False  # refused before doing any work

    def test_budget_zero_is_unlimited(self, monkeypatch):
        """B2: a cap of 0 must mean unlimited, not 'always exhausted'."""
        monkeypatch.setenv("SPARK_MAX_WEB_SEARCHES_PER_SESSION", "0")
        from src.config import get_settings

        get_settings.cache_clear()
        # Simulate a session that has already "used" far more than any
        # positive cap would allow — with cap=0 it must still succeed.
        for _ in range(50):
            budget.increment_web_search()

        monkeypatch.setattr(web_search_module, "_search_tavily", lambda *a: _ok_results())
        result = web_search_handler(query="python")
        assert result["status"] == "success"

    def test_no_double_increment_when_tavily_returns_empty(self, monkeypatch):
        """B3: Tavily returning [] (not raising) must not charge the budget
        twice once DuckDuckGo's fallback also runs."""
        monkeypatch.setattr(web_search_module, "_search_tavily", lambda *a: [])
        monkeypatch.setattr(web_search_module, "_search_duckduckgo", lambda *a: _ok_results())

        result = web_search_handler(query="python")
        assert result["status"] == "success"
        assert result["data"]["provider"] == "duckduckgo"
        assert budget.get_web_search_count() == 1

    def test_single_increment_on_tavily_success(self, monkeypatch):
        monkeypatch.setattr(web_search_module, "_search_tavily", lambda *a: _ok_results())
        result = web_search_handler(query="python")
        assert result["status"] == "success"
        assert budget.get_web_search_count() == 1

    def test_single_increment_on_duckduckgo_fallback(self, monkeypatch):
        def raising_tavily(*a):
            raise ValueError("no api key")

        monkeypatch.setattr(web_search_module, "_search_tavily", raising_tavily)
        monkeypatch.setattr(web_search_module, "_search_duckduckgo", lambda *a: _ok_results())
        result = web_search_handler(query="python")
        assert result["status"] == "success"
        assert budget.get_web_search_count() == 1


class TestWebSearchHandlerProviders:
    """Provider selection and fallback behavior."""

    def test_tavily_success_returns_tavily_provider(self, monkeypatch):
        monkeypatch.setattr(web_search_module, "_search_tavily", lambda *a: _ok_results(3))
        result = web_search_handler(query="python")
        assert result["status"] == "success"
        assert result["data"]["provider"] == "tavily"
        assert len(result["data"]["results"]) == 3

    def test_tavily_failure_falls_back_to_duckduckgo(self, monkeypatch):
        def raising_tavily(*a):
            raise RuntimeError("tavily down")

        monkeypatch.setattr(web_search_module, "_search_tavily", raising_tavily)
        monkeypatch.setattr(web_search_module, "_search_duckduckgo", lambda *a: _ok_results())
        result = web_search_handler(query="python")
        assert result["status"] == "success"
        assert result["data"]["provider"] == "duckduckgo"

    def test_both_providers_fail_returns_error(self, monkeypatch):
        def raising_tavily(*a):
            raise RuntimeError("tavily down")

        def raising_ddg(*a):
            raise RuntimeError("ddg down")

        monkeypatch.setattr(web_search_module, "_search_tavily", raising_tavily)
        monkeypatch.setattr(web_search_module, "_search_duckduckgo", raising_ddg)
        result = web_search_handler(query="python")
        assert result["status"] == "error"
        assert result["data"] is None
        assert "unavailable" in result["errors"][0].lower()
        # Neither attempt should have charged the budget.
        assert budget.get_web_search_count() == 0
