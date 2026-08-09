"""Catalog tool - thin @tool wrapper around the search_careers_handler."""

from typing import Any, cast

from langchain_core.tools import tool

from src.tools.catalog.handler import DEFAULT_LIMIT, search_careers_handler


@tool
def search_careers(
    query: str,
    field: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Lista carreras del catalogo del Peru, sin la dimension institucional.

    Son las 554 carreras del portal Ponte en Carrera del MINEDU, cada una con
    su familia y su perfil RIASEC. Usa esta herramienta cuando la conversacion
    va de ORIENTACION: que carreras existen relacionadas con algo, como se
    llaman, a que familia pertenecen, que perfil vocacional tienen.

    Para cifras --donde se estudia, cuanto cuesta, cuanto se gana, que tasa de
    admision tiene-- usa `search_programs`: es la misma fuente vista por
    programa, y la unica que trae numeros. Esta no sabe nada de universidades,
    sueldos ni costos.

    El codigo RIASEC de cada carrera lo asigno un modelo de lenguaje, no el
    MINEDU. Sirve para orientar, no como clasificacion oficial.

    Args:
        query: Texto a buscar en el nombre de la carrera o en su familia.
            Acepta acentos o no ("ingenieria" encuentra "Ingenieria"). Vacio
            para no filtrar por texto.
        field: Familia de carrera, por ejemplo "Teatro" o "Educacion".
            Coincidencia por subcadena.
        limit: Cuantas devolver. El maximo es 25.

    Returns:
        Lista de carreras con `career`, `career_family`, `riasec_profile` y
        `program_count` (en cuantos programas se puede estudiar). Si el
        catalogo no se pudo cargar, una lista con un dict `error`.
    """
    result = search_careers_handler(query=query, field=field, limit=limit)

    if result["status"] == "error":
        # Surface error to the LLM so it can recover gracefully.
        return [{"error": e} for e in result["errors"] or ["unknown error"]]

    return cast(list[dict[str, Any]], result["data"]["careers"])
