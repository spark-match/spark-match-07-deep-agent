"""Web search tool - thin @tool wrapper around web_search_handler."""

from typing import Any, cast

from langchain_core.tools import tool

from src.tools.web_search.handler import web_search_handler


@tool
def web_search(query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """Search the web for current information.

    Uses Tavily as primary search (LLM-optimized results) and falls back
    to DuckDuckGo if Tavily is unavailable or returns an error.

    Subject to the per-session web-search budget
    (``settings.max_web_searches_per_session``). When the budget is
    exhausted the call is refused with a clear error message instead of
    burning more API quota.

    Useful for:
    - Finding current career information, salaries, job market data
    - Looking up courses, certifications, and educational resources
    - Verifying career requirements and job descriptions
    - Researching industry trends and professional opportunities

    Args:
        query: Search query (be specific for better results)
        max_results: Maximum number of results to return (default: 5)
    """
    result = web_search_handler(query=query, max_results=max_results)

    if result["status"] != "success":
        # Surface status info to the LLM as a single dict.
        return [
            {
                "status": result["status"],
                "errors": result["errors"],
            }
        ]

    return cast(list[dict[str, Any]], result["data"]["results"])
