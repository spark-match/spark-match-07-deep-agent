"""Web search handler - Tavily (primary) + DuckDuckGo (fallback).

Pure business logic for searching the web. No @tool decorator.
Uses the budget guard from src.budget to refuse calls that exceed the
per-session limit.

Structured return schema:
    {
        "status": "success" | "budget_exhausted" | "error",
        "data": {"results": [...], "provider": "tavily" | "duckduckgo"} | None,
        "errors": [<error_message>] | None,
    }
"""

import logging
from typing import Any, Literal

from duckduckgo_search import DDGS
from tavily import TavilyClient

from src import budget
from src.config import get_settings

logger = logging.getLogger(__name__)

SearchProvider = Literal["tavily", "duckduckgo"]


def _search_tavily(query: str, max_results: int) -> list[dict[str, Any]]:
    """Search using Tavily API (LLM-optimized results)."""
    settings = get_settings()
    if not settings.tavily_api_key:
        raise ValueError("TAVILY_API_KEY not configured")

    client = TavilyClient(api_key=settings.tavily_api_key.get_secret_value())
    response = client.search(
        query=query,
        max_results=max_results,
        search_depth="basic",
        include_answer=True,
    )

    results: list[dict[str, Any]] = []
    for item in response.get("results", []):
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
            }
        )

    answer = response.get("answer")
    if answer:
        results.insert(
            0,
            {
                "title": "AI Summary",
                "url": "",
                "content": answer,
            },
        )

    return results


def _search_duckduckgo(query: str, max_results: int) -> list[dict[str, Any]]:
    """Search using DuckDuckGo (free, no API key needed)."""
    with DDGS() as ddgs:
        raw_results = ddgs.text(query, max_results=max_results)

    results: list[dict[str, Any]] = []
    for item in raw_results:
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("href", ""),
                "content": item.get("body", ""),
            }
        )

    return results


def _refuse_budget_exceeded() -> dict[str, Any]:
    """Helper to build the budget_exhausted response."""
    settings = get_settings()
    return {
        "status": "budget_exhausted",
        "data": None,
        "errors": [
            f"Web search budget exceeded ({settings.max_web_searches_per_session} per session)."
        ],
    }


def web_search_handler(query: str, max_results: int = 5) -> dict[str, Any]:
    """Search the web with budget enforcement.

    Pure business logic - no @tool decorator. Testable without LLM.

    Budget enforcement (Sprint 2 Ã‚Â§4.2): if the active session has already
    consumed ``settings.max_web_searches_per_session`` web searches, refuse
    the call with ``status="budget_exhausted"`` instead of burning more
    Tavily quota.

    Args:
        query: Search query
        max_results: Max results to return (default: 5)

    Returns:
        Structured dict with status, data, errors.
    """
    settings = get_settings()

    # Budget guard - check before doing any work
    current_count = budget.get_web_search_count()
    if current_count >= settings.max_web_searches_per_session:
        return _refuse_budget_exceeded()

    if not query or not query.strip():
        return {
            "status": "error",
            "data": None,
            "errors": ["query must be a non-empty string"],
        }

    if not isinstance(max_results, int) or max_results < 1:
        max_results = 5

    # Try Tavily first (better results for LLM consumption)
    try:
        results = _search_tavily(query, max_results)
        budget.increment_web_search()
        if results:
            logger.info("Web search completed via Tavily: %d results", len(results))
            return {
                "status": "success",
                "data": {"results": results, "provider": "tavily"},
                "errors": None,
            }
    except Exception as e:
        logger.warning("Tavily search failed, falling back to DuckDuckGo: %s", e)

    # Fallback to DuckDuckGo
    try:
        results = _search_duckduckgo(query, max_results)
        budget.increment_web_search()
        logger.info(
            "Web search completed via DuckDuckGo (fallback): %d results",
            len(results),
        )
        return {
            "status": "success",
            "data": {"results": results, "provider": "duckduckgo"},
            "errors": None,
        }
    except Exception as e:
        logger.error("Both search providers failed: %s", e)
        return {
            "status": "error",
            "data": None,
            "errors": [f"Search unavailable: {e}"],
        }
