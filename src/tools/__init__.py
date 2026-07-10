"""Tools for the Spark Match Agent.

Each tool is split into:
- ``<tool>/handler.py``: pure business logic (no @tool, no LLM dependency).
- ``<tool>/tool.py``: thin @tool wrapper that delegates to the handler.

The handlers are testable without instantiating the LLM or any external
service. The wrappers carry the @tool decorator, docstring, and type
hints that the LangGraph runtime needs.
"""

from src.tools.assessment import evaluate_riasec_profile
from src.tools.catalog import search_careers
from src.tools.matching import calculate_affinity
from src.tools.web_search import web_search

__all__ = [
    "calculate_affinity",
    "evaluate_riasec_profile",
    "search_careers",
    "web_search",
]
