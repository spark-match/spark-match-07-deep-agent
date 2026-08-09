"""Catalog tool - thin @tool wrapper around the search_careers_handler."""

from typing import Any, cast

from langchain_core.tools import tool

from src.tools.catalog.handler import search_careers_handler


@tool
def search_careers(query: str, field: str | None = None) -> list[dict[str, Any]]:
    """Describe UNA carrera en general: en qué consiste, para qué perfil RIASEC
    encaja, cómo está la demanda laboral. Un catálogo curado de 20 fichas
    genéricas, no un listado — no lo uses para "qué carreras hay" ni para
    explorar opciones.

    NO uses esta herramienta para nombrar universidades, institutos, costos,
    sueldos o tasas de admisión del Perú: no tiene esos datos. Para eso, o
    para cualquier pregunta que empiece por "qué carreras hay" o "qué
    opciones tengo", usa `search_programs` — 6208 combinaciones reales de
    carrera e institución del portal Ponte en Carrera (MINEDU).

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
