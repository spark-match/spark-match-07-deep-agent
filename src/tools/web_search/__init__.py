"""Web search package - Tavily (primary) + DuckDuckGo (fallback)."""

from src.tools.web_search.handler import web_search_handler
from src.tools.web_search.tool import web_search

__all__ = [
    "web_search",
    "web_search_handler",
]
