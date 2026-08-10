"""Report tool - thin @tool wrapper around build_orientation_report_handler."""

from typing import Any, cast

from langchain_core.tools import tool

from src.tools.report.handler import build_orientation_report_handler


@tool
def build_orientation_report(
    riasec_code: str,
    profile_summary: str,
    insights: list[dict[str, str]],
    region: str | None = None,
    management_type: str | None = None,
    institution_type: str | None = None,
    max_annual_cost: float | None = None,
) -> dict[str, Any]:
    """Arma el informe de orientacion final. Tu escribes la prosa; las cifras las pone el sistema.

    Es el ultimo paso de un informe. Antes tienes que haber llamado a
    `recommend_programs` con los MISMOS filtros para ver que carreras salen.

    NO le pases cifras, y no las tiene como parametro a proposito: la duracion,
    el ingreso, el costo, la tasa de admision y la puntuacion los vuelve a
    calcular el motor aqui dentro. Tu aportas las dos cosas que el motor no
    puede: el retrato del perfil y el por que de cada carrera.

    Si te falta una carrera por explicar, o explicas una que el motor no
    recomendo, la herramienta falla y te dice cual. No te devuelve un informe a
    medias.

    Args:
        riasec_code: Codigo Holland de 3 letras del estudiante, por ejemplo "IRC".
        profile_summary: Retrato en prosa de su perfil vocacional. Habla de
            esta persona, no del codigo: que se le da bien, que parece
            motivarle, que tensiones hay entre sus intereses. Dos o tres
            parrafos.
        insights: Una entrada `{"career": "...", "insight": "..."}` por carrera
            que quieras en el informe, entre 2 y 10. El nombre de la carrera
            tiene que ser el que devolvio `recommend_programs` (los acentos y
            las mayusculas dan igual). El `insight` explica por que ESA carrera
            encaja con ESTE estudiante — no repitas la descripcion de la
            carrera, que ya esta en las cifras.
        region: El mismo que usaste en `recommend_programs`.
        management_type: El mismo que usaste en `recommend_programs`.
        institution_type: El mismo que usaste en `recommend_programs`.
        max_annual_cost: El mismo que usaste en `recommend_programs`.

    Returns:
        El informe completo, listo para guardar. Si algo no cuadra, un dict con
        `error` explicando que corregir.
    """
    result = build_orientation_report_handler(
        riasec_code=riasec_code,
        profile_summary=profile_summary,
        insights=insights,
        region=region,
        management_type=management_type,
        institution_type=institution_type,
        max_annual_cost=max_annual_cost,
    )

    if result["status"] == "error":
        return {"error": (result["errors"] or ["unknown error"])[0]}

    return cast(dict[str, Any], result["data"])
