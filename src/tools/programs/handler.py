"""Busqueda sobre el catalogo real de Ponte en Carrera (logica pura).

Esto es **recuperacion**, no scoring. Filtra y ordena; no calcula afinidad
ni inventa un ranking. La afinidad RIASEC tiene su propia herramienta
(``calculate_affinity``) y meterla aqui duplicaria la definicion de "encaja"
en dos sitios que se irian separando.

Esquema de retorno, el mismo que el resto de handlers del repositorio:

    {
        "status": "success" | "error",
        "data": {"programs": [...], "total_matches": int, "source": str} | None,
        "errors": [<mensaje>] | None,
    }
"""

from __future__ import annotations

from typing import Any

from src.tools.programs.loader import SOURCE_LABEL, Program, load_programs, normalize

# Tope duro de resultados. No es cosmetico: cada programa son ~15 campos que
# entran enteros en el contexto del modelo, y devolver 200 filas de una
# busqueda vaga desplaza a la conversacion del estudiante.
MAX_LIMIT = 25
DEFAULT_LIMIT = 8


def _measurements(program: Program) -> tuple[tuple[str, bool], ...]:
    """Cada cifra del resultado con su bandera de medicion al lado."""
    return (
        ("duration_years", program["duration_measured"]),
        ("monthly_income", program["income_measured"]),
        ("annual_cost", program["cost_measured"]),
        ("admission_rate", program["admission_measured"]),
    )


def _matches_text(program: Program, query: str | None) -> bool:
    return query is None or query in program["search_text"]


def _matches_exact(value: str, wanted: str | None) -> bool:
    return wanted is None or normalize(value) == wanted


def _riasec_overlap(program: Program, wanted: str | None) -> int:
    """Cuantas letras comparte el perfil de la carrera con el del estudiante.

    Deliberadamente burdo: sirve para *filtrar* un catalogo de 554 carreras
    a las que tienen algo que ver, no para puntuar. Quien puntua es
    ``calculate_affinity``.
    """
    if wanted is None:
        return 0
    return len(set(program["riasec_profile"]) & set(wanted))


def _estimated_fields(program: Program) -> list[str]:
    """Los campos cuyo valor NO se midio en este programa.

    Es la parte del resultado que impide mentir sin querer: el pipeline
    rellena lo que falta con la mediana de la familia de carrera, y sin esta
    lista un ingreso imputado es indistinguible de uno real.
    """
    return [field for field, measured in _measurements(program) if not measured]


def _measured_count(program: Program) -> int:
    return sum(1 for _, measured in _measurements(program) if measured)


def _to_result(program: Program) -> dict[str, Any]:
    return {
        "career": program["career"],
        "career_family": program["career_family"],
        "riasec_profile": program["riasec_profile"],
        "institution": program["institution"],
        "institution_type": program["institution_type"],
        "management_type": program["management_type"],
        "location": program["location"],
        "duration_years": program["duration_years"],
        "monthly_income": program["monthly_income"],
        "annual_cost": program["annual_cost"],
        "admission_rate": program["admission_rate"],
        "estimated": _estimated_fields(program),
    }


def search_programs_handler(
    career: str | None = None,
    riasec_profile: str | None = None,
    location: str | None = None,
    institution_type: str | None = None,
    management_type: str | None = None,
    max_annual_cost: float | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Busca programas reales aplicando los filtros que se pasen.

    Todos los filtros son opcionales y se combinan con Y. Sin ninguno,
    devuelve los primeros del catalogo por el orden de abajo, que sirve para
    hacerse una idea pero no es una recomendacion.

    Orden de los resultados: primero por cuantas de las cuatro cifras se
    midieron de verdad (descendente), luego por letras RIASEC compartidas si
    se pidio un perfil, y finalmente por carrera e institucion para que dos
    llamadas iguales devuelvan lo mismo. Poner delante lo medido es
    deliberado: lo primero que ve el estudiante debe ser lo que mejor
    sabemos, no una mediana bien presentada.
    """
    catalog = load_programs()
    if not catalog:
        return {
            "status": "error",
            "data": None,
            "errors": ["el catalogo de programas esta vacio - revisa data/programs/"],
        }

    query = normalize(career.strip()) if career and career.strip() else None
    wanted_location = normalize(location.strip()) if location and location.strip() else None
    wanted_institution = (
        normalize(institution_type.strip())
        if institution_type and institution_type.strip()
        else None
    )
    wanted_management = (
        normalize(management_type.strip()) if management_type and management_type.strip() else None
    )
    wanted_riasec = (
        riasec_profile.strip().upper() if riasec_profile and riasec_profile.strip() else None
    )

    matches = [
        program
        for program in catalog
        if _matches_text(program, query)
        and _matches_exact(program["location"], wanted_location)
        and _matches_exact(program["institution_type"], wanted_institution)
        and _matches_exact(program["management_type"], wanted_management)
        and (max_annual_cost is None or program["annual_cost"] <= max_annual_cost)
        # Con perfil pedido, se exige al menos una letra en comun: sin eso el
        # filtro no filtraria nada y el parametro seria decorativo.
        and (wanted_riasec is None or _riasec_overlap(program, wanted_riasec) > 0)
    ]

    matches.sort(
        key=lambda program: (
            -_measured_count(program),
            -_riasec_overlap(program, wanted_riasec),
            program["career"],
            program["institution"],
        )
    )

    capped = max(1, min(int(limit), MAX_LIMIT))
    return {
        "status": "success",
        "data": {
            "programs": [_to_result(program) for program in matches[:capped]],
            "total_matches": len(matches),
            "source": SOURCE_LABEL,
        },
        "errors": None,
    }


__all__ = ["DEFAULT_LIMIT", "MAX_LIMIT", "search_programs_handler"]
