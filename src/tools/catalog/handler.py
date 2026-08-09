"""Catalog handler - busqueda a nivel de CARRERA (logica pura).

Dos vistas sobre el mismo dato, no dos datos:

- ``search_programs`` (``src/tools/programs``) devuelve filas carrera x
  institucion: donde se estudia, cuanto cuesta, que tasa de admision tiene.
- ``search_careers`` (esto) devuelve las 554 carreras unicas con su familia y
  su perfil RIASEC, sin dimension institucional. Responde a "que carreras
  existen relacionadas con X y que perfil vocacional tienen", que es la
  pregunta de la conversacion de orientacion, no la de la eleccion de
  universidad.

Hasta el 2026-08-09 esta herramienta leia ``data/careers/*.md``: veinte fichas
curadas a mano, con su propio ``riasec_profile``, su propio ``field`` y ningun
dato economico. O sea un segundo catalogo de carreras en paralelo al real, con
veinte entradas frente a 554 y sin forma de cruzarlos. Se retiro entero (ver
ADR-019 de spark-match-03-backend): una sola fuente, ``data/programs/programs.csv``.

Consecuencia visible en la firma: ``field`` filtraba por el campo curado
(~10 valores como "Tecnologia" o "Salud") y ahora filtra por ``career_family``,
que son las 81 familias reales del portal del MINEDU.

Esquema de retorno, el mismo que el resto de handlers del repositorio:

    {
        "status": "success" | "error",
        "data": {"careers": [...], "total_matches": int,
                 "fallback_used": bool, "source": str} | None,
        "errors": [<mensaje>] | None,
    }
"""

from __future__ import annotations

from typing import Any

from src.tools.programs.loader import (
    SOURCE_LABEL,
    CareerEntry,
    load_careers,
    normalize,
)

# Tope duro, por el mismo motivo que en `search_programs`: el catalogo pasó de
# 20 carreras a 554, y una consulta vaga como "ingenieria" casa con mas de
# cien. Devolverlas todas desplaza la conversacion del estudiante fuera del
# contexto del modelo. Antes no hacia falta porque con 20 el peor caso era 20.
MAX_LIMIT = 25
DEFAULT_LIMIT = 10

# Cuantas sugerir cuando la busqueda no encuentra nada.
FALLBACK_SUGGESTIONS = 5


def _to_result(entry: CareerEntry) -> dict[str, Any]:
    """Lo que ve el modelo. `search_text` no sale: es un indice interno."""
    return {
        "career": entry["career"],
        "career_family": entry["career_family"],
        "riasec_profile": entry["riasec_profile"],
        "program_count": entry["program_count"],
    }


def _matches(entry: CareerEntry, query: str | None, family: str | None) -> bool:
    if query is not None and query not in entry["search_text"]:
        return False
    return family is None or family in normalize(entry["career_family"])


def _por_relevancia(entry: CareerEntry) -> tuple[int, str]:
    """Mas ofertada primero; a igualdad, alfabetico para que sea estable."""
    return (-entry["program_count"], entry["career"])


def search_careers_handler(
    query: str,
    field: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Busca carreras por texto libre y, opcionalmente, por familia.

    Estrategia, en orden:
        1. Coincidencia de ``query`` sobre nombre + familia, y de ``field``
           sobre la familia.
        2. Si no hay nada y se pidio ``field``, todas las de esa familia.
        3. Si sigue sin haber nada, las mas ofertadas del catalogo como
           sugerencia, con ``fallback_used`` en true para que el modelo sepa
           que no son una respuesta a lo que pregunto.

    Args:
        query: Texto libre (nombre de carrera o de familia). Vacio = sin filtro.
        field: Familia de carrera. Coincidencia por subcadena, sin acentos.
        limit: Maximo de resultados; se recorta a ``MAX_LIMIT``.
    """
    catalog = load_careers()
    if not catalog:
        return {
            "status": "error",
            "data": None,
            "errors": ["el catalogo de carreras esta vacio - revisa data/programs/"],
        }

    texto = normalize(query.strip()) if query and query.strip() else None
    familia = normalize(field.strip()) if field and field.strip() else None

    matches = [entry for entry in catalog if _matches(entry, texto, familia)]
    fallback_used = False

    # Fallback 1: la familia entera cuando el texto no casa con nada.
    if not matches and familia is not None:
        matches = [entry for entry in catalog if familia in normalize(entry["career_family"])]
        fallback_used = bool(matches)

    # Fallback 2: las mas ofertadas, como punto de partida.
    if not matches:
        matches = sorted(catalog, key=_por_relevancia)[:FALLBACK_SUGGESTIONS]
        fallback_used = True

    matches.sort(key=_por_relevancia)
    capped = max(1, min(int(limit), MAX_LIMIT))

    return {
        "status": "success",
        "data": {
            "careers": [_to_result(entry) for entry in matches[:capped]],
            "total_matches": len(matches),
            "fallback_used": fallback_used,
            "source": SOURCE_LABEL,
        },
        "errors": None,
    }


__all__ = ["DEFAULT_LIMIT", "FALLBACK_SUGGESTIONS", "MAX_LIMIT", "search_careers_handler"]
