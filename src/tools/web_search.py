"""Web search tools — Tavily (primary) + DuckDuckGo (fallback).

Tavily provides LLM-optimized search results (clean snippets, no scraping needed).
DuckDuckGo is used as a zero-cost fallback when Tavily is unavailable or quota is exhausted.

Each call is counted against :data:`src.config.settings.max_web_searches_per_session`
via the per-session budget tracker in :mod:`src.budget`. Once the cap is reached
the tool returns a clear "budget exceeded" response instead of performing another
external search — this protects the Tavily quota from a runaway agent loop.
"""

import logging

from duckduckgo_search import DDGS
from langchain_core.tools import tool
from tavily import TavilyClient

from src.budget import get_active_session, get_web_search_count, increment_web_search
from src.config import get_settings

logger = logging.getLogger(__name__)


def _search_tavily(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search using Tavily API (LLM-optimized results)."""
    settings = get_settings()

    if not settings.tavily_api_key:
        raise ValueError("TAVILY_API_KEY not configured")

    # SecretStr.get_secret_value() returns the raw str for upstream auth.
    client = TavilyClient(api_key=settings.tavily_api_key.get_secret_value())
    response = client.search(
        query=query,
        max_results=max_results,
        search_depth="basic",
        include_answer=True,
    )

    results = []
    for item in response.get("results", []):
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "content": item.get("content", ""),
            }
        )

    # Include Tavily's AI-generated answer if available
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


def _search_duckduckgo(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search using DuckDuckGo (free, no API key needed)."""
    with DDGS() as ddgs:
        raw_results = ddgs.text(query, max_results=max_results)

    results = []
    for item in raw_results:
        results.append(
            {
                "title": item.get("title", ""),
                "url": item.get("href", ""),
                "content": item.get("body", ""),
            }
        )

    return results


@tool
def web_search(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Search the web for current information.

    Uses Tavily as primary search (LLM-optimized results) and falls back
    to DuckDuckGo if Tavily is unavailable or returns an error.

    Useful for:
    - Finding current career information, salaries, job market data
    - Looking up courses, certifications, and educational resources
    - Verifying career requirements and job descriptions
    - Researching industry trends and professional opportunities

    Args:
        query: Search query (be specific for better results)
        max_results: Maximum number of results to return (default: 5)
    """
    settings = get_settings()
    budget_cap = settings.max_web_searches_per_session

    # Budget guard: refuse to search if the session has exhausted its quota.
    current_count = get_web_search_count()
    if current_count >= budget_cap:
        msg = (
            f"Web search budget exhausted for this session "
            f"({current_count}/{budget_cap} calls used). "
            "Use cached knowledge or finalize the response."
        )
        logger.warning("Refusing web_search for session=%s: %s", get_active_session(), msg)
        return [{"title": "Budget exceeded", "url": "", "content": msg}]

    # Increment BEFORE performing the search so even failed calls count
    # against the budget (prevents retry storms).
    new_count = increment_web_search()
    logger.info(
        "Web search call %d/%d (session=%s)",
        new_count,
        budget_cap,
        get_active_session(),
    )

    # Try Tavily first (better results for LLM consumption)
    try:
        results = _search_tavily(query, max_results)
        if results:
            logger.info("Web search completed via Tavily: %d results", len(results))
            return results
    except Exception as e:
        logger.warning("Tavily search failed, falling back to DuckDuckGo: %s", e)

    # Fallback to DuckDuckGo (free, no API key)
    try:
        results = _search_duckduckgo(query, max_results)
        logger.info(
            "Web search completed via DuckDuckGo (fallback): %d results",
            len(results),
        )
        return results
    except Exception as e:
        logger.error("Both search providers failed: %s", e)
        return [{"title": "Error", "url": "", "content": f"Search unavailable: {e}"}]
