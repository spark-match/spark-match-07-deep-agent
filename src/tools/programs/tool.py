"""Herramienta @tool sobre el catalogo real de Ponte en Carrera."""

from typing import Any

from langchain_core.tools import tool

from src.tools.programs.handler import DEFAULT_LIMIT, search_programs_handler


@tool
def search_programs(
    career: str | None = None,
    riasec_profile: str | None = None,
    location: str | None = None,
    institution_type: str | None = None,
    management_type: str | None = None,
    max_annual_cost: float | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Busca carreras REALES en universidades e institutos del Peru.

    Usa datos del portal Ponte en Carrera del MINEDU: 6208 combinaciones de
    carrera e institucion, con duracion, ingreso mensual de los egresados,
    costo anual y tasa de admision. Es la unica fuente de cifras concretas
    del Peru que tienes; `search_careers` es esta misma fuente vista por
    carrera (las 554, con familia y RIASEC) y no sabe nada de universidades,
    sueldos ni costos.

    REGLA OBLIGATORIA al presentar los resultados: cada programa trae una
    lista `estimated`. Los campos que aparecen ahi NO son datos medidos de
    ese programa, son la mediana de su familia de carrera. Si los mencionas,
    di que son estimados; si el estudiante te pide una cifra exacta y esta
    estimada, dilo en vez de darla como buena. Los campos que NO aparecen en
    `estimated` si son datos reales de ese programa.

    El codigo RIASEC de cada carrera lo asigno un modelo de lenguaje, no el
    MINEDU. Sirve para orientar la busqueda, no como clasificacion oficial.

    Args:
        career: Texto a buscar en el nombre de la carrera, la familia o la
            institucion. Acepta acentos o no ("ingenieria" encuentra
            "Ingeniería").
        riasec_profile: Codigo RIASEC del estudiante (ej. "IRC"). Filtra a
            las carreras que comparten al menos una letra.
        location: Departamento del Peru (ej. "Lima", "Áncash").
        institution_type: "Universidad" o "Instituto".
        management_type: "Pública" o "Privada".
        max_annual_cost: Costo anual maximo en soles.
        limit: Cuantos programas devolver (maximo 25).

    Returns:
        Dict con `programs`, `total_matches` y `source`. Si el catalogo no
        esta disponible, un dict con `error`.
    """
    result = search_programs_handler(
        career=career,
        riasec_profile=riasec_profile,
        location=location,
        institution_type=institution_type,
        management_type=management_type,
        max_annual_cost=max_annual_cost,
        limit=limit,
    )

    if result["status"] == "error":
        # Se le devuelve el error al modelo para que lo diga en vez de
        # rellenar el hueco con cifras inventadas.
        return {"error": (result["errors"] or ["unknown error"])[0]}

    return dict(result["data"])


__all__ = ["search_programs"]
