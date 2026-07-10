"""Matching tool - thin @tool wrapper around calculate_affinity_handler."""

from typing import Any, cast

from langchain_core.tools import tool

from src.tools.matching.handler import calculate_affinity_handler


@tool
def calculate_affinity(riasec_code: str, top_n: int = 5) -> list[dict[str, Any]]:
    """Calculate affinity scores between a RIASEC profile and all careers in the catalog.

    Returns the top-N careers sorted by affinity score with explanations.

    Args:
        riasec_code: The student's 3-letter RIASEC code (e.g., 'IAS', 'RIC')
        top_n: Number of top careers to return (default: 5)
    """
    result = calculate_affinity_handler(riasec_code=riasec_code, top_n=top_n)

    if result["status"] == "error":
        return [{"error": e} for e in result["errors"] or ["unknown error"]]

    return cast(list[dict[str, Any]], result["data"]["matches"])
