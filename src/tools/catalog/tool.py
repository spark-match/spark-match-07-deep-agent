"""Catalog tool - thin @tool wrapper around the search_careers_handler."""

from typing import Any, cast

from langchain_core.tools import tool

from src.tools.catalog.handler import search_careers_handler


@tool
def search_careers(query: str, field: str | None = None) -> list[dict[str, Any]]:
    """Search the career catalog by keyword or field.

    In the MVP, performs simple text matching on the local catalog.
    In production, this will use pgvector semantic search.

    Args:
        query: Search query (career name, skill, or description keywords)
        field: Optional filter by field (e.g., 'Tecnologia', 'Salud')

    Returns:
        List of career dicts. If the catalog is empty, an error dict.
    """
    result = search_careers_handler(query=query, field=field)

    if result["status"] == "error":
        # Surface error to the LLM so it can recover gracefully.
        return [{"error": e} for e in result["errors"] or ["unknown error"]]

    return cast(list[dict[str, Any]], result["data"]["careers"])
