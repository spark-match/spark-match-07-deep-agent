"""Recommendation tool - thin @tool wrapper around recommend_programs_handler."""

from typing import Any, cast

from langchain_core.tools import tool

from src.tools.recommendation.handler import DEFAULT_TOP_N, recommend_programs_handler


@tool
def recommend_programs(
    riasec_code: str,
    region: str | None = None,
    management_type: str | None = None,
    institution_type: str | None = None,
    max_annual_cost: float | None = None,
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, Any]:
    """Recomienda programas reales para un perfil RIASEC, ya filtrados y ordenados.

    Es la herramienta del Top-N final: cruza la afinidad vocacional con los
    datos economicos del portal Ponte en Carrera y devuelve una carrera por
    recomendacion, con su institucion y sus cifras.

    Cuando usar cada una:
    - `recommend_programs` (esta): el estudiante ya tiene codigo RIASEC y
      quiere saber QUE ESTUDIAR Y DONDE. Es la que alimenta el informe.
    - `search_programs`: busqueda libre por nombre o filtros, sin perfil.
    - `calculate_affinity`: solo afinidad, sin institucion ni cifras.

    Los filtros EXCLUYEN, no restan puntos: si pides Arequipa, no aparece nada
    de fuera de Arequipa. Si la combinacion no deja nada, el error te dice que
    filtro soltar y cuantos programas aparecerian.

    DILE AL ESTUDIANTE CUANTO RECORTA CADA FILTRO. La respuesta trae
    `candidates_without_each_filter`: cuantos programas habria soltando cada
    uno. Un filtro no da una respuesta mala, borra opciones en silencio, y si
    entendiste mal su presupuesto o su region el estudiante no tiene forma de
    notarlo. Con esto puedes decirle "con tu presupuesto quedan 43 de los 411
    de Arequipa" y darle la ocasion de corregirte. Hazlo siempre que un filtro
    deje fuera a la mayoria.

    REGLA OBLIGATORIA al presentar los resultados: cada recomendacion trae una
    lista `estimated` con los campos que NO son datos medidos de ese programa,
    sino la mediana de su familia de carrera. Presentalos como estimados o no
    los menciones. Y `match_score` es una puntuacion propia del sistema, no un
    dato del MINEDU: se puede explicar con `score_breakdown`, que desglosa
    cuanto viene de la afinidad y cuanto de la economia.

    El codigo RIASEC de cada carrera lo asigno un modelo de lenguaje, no el
    MINEDU. Sirve para orientar, no como clasificacion oficial.

    Args:
        riasec_code: Codigo Holland de 3 letras del estudiante, por ejemplo "IRC".
        region: Departamento donde quiere estudiar, por ejemplo "Arequipa".
            Omitir para no filtrar por region.
        management_type: "publica" o "privada". "ambas" para no filtrar.
        institution_type: "universidad" o "instituto". "ambos" para no filtrar.
        max_annual_cost: Presupuesto anual maximo en soles.
        top_n: Cuantas recomendaciones devolver. Maximo 10.

    Returns:
        Dict con `recommendations`, `total_candidates`, `careers_matched`,
        `filters_applied`, `scoring_version` y `source`. Si no hay resultados,
        un dict con `error`.
    """
    result = recommend_programs_handler(
        riasec_code=riasec_code,
        region=region,
        management_type=management_type,
        institution_type=institution_type,
        max_annual_cost=max_annual_cost,
        top_n=top_n,
    )

    if result["status"] == "error":
        return {"error": (result["errors"] or ["unknown error"])[0]}

    return cast(dict[str, Any], result["data"])
