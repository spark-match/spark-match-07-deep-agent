"""Catalog handler - career catalog search (pure business logic).

Pure business logic for searching the career catalog.
Loads the catalog from data/careers/*.md (Sprint 2 section 3.4) and provides
keyword/field filtering without any @tool decorator or LLM dependency.

Structured return schema:
    {
        "status": "success" | "error",
        "data": {"careers": [...], "total": int, "fallback_used": bool} | None,
        "errors": [<error_message>] | None,
    }
"""

from typing import Any

from src.tools.catalog.loader import Career, load_career_catalog


def _search_in_catalog(careers: list[Career], query: str, field: str | None) -> list[Career]:
    """Filter catalog by query (substring on searchable text) and field."""
    query_lower = query.lower()
    results: list[Career] = []

    for career in careers:
        # Match against name, body (description + resources), field
        searchable = " ".join(
            [
                career["name"].lower(),
                career["body"].lower(),
                career["field"].lower(),
            ]
        )
        if query_lower in searchable and (
            field is None or field.lower() in career["field"].lower()
        ):
            results.append(career)

    return results


def search_careers_handler(query: str, field: str | None = None) -> dict[str, Any]:
    """Search the career catalog by keyword or field.

    Pure business logic - no @tool decorator. Testable without LLM.

    Strategy:
        1. Try exact match on query in name/body/field.
        2. If no match and field is specified, return all careers in that field.
        3. If still no results, return top 5 as suggestions.

    Args:
        query: Search query (career name, skill, or description keywords)
        field: Optional filter by field (e.g., 'TecnologÃƒÂ­a', 'Salud')

    Returns:
        Structured dict with status, data, errors.
    """
    catalog = load_career_catalog()

    if not catalog:
        return {
            "status": "error",
            "data": None,
            "errors": ["career catalog is empty - check data/careers/"],
        }

    query_clean = (query or "").strip()
    field_clean = (field or "").strip() or None

    matches = _search_in_catalog(catalog, query_clean, field_clean)
    fallback_used = False

    # Fallback 1: all careers in field if no exact match
    if not matches and field_clean:
        matches = [c for c in catalog if field_clean.lower() in c["field"].lower()]
        fallback_used = bool(matches)

    # Fallback 2: top 5 as suggestions
    if not matches:
        matches = catalog[:5]
        fallback_used = True

    return {
        "status": "success",
        "data": {"careers": matches, "total": len(matches), "fallback_used": fallback_used},
        "errors": None,
    }
