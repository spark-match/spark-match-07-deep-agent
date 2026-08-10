"""Ensamblado del informe de orientacion (ADR-019, fase 4).

**Por que esta herramienta vuelve a ejecutar el motor en vez de recibir las
cifras por parametro.**

La forma obvia seria que el subagente llamase a ``recommend_programs``, leyera
el ranking y luego pasara aqui las carreras con sus numeros y su explicacion.
Es mas corto y es un error: en el momento en que una cifra entra al contexto
del modelo y vuelve a salir por una llamada a herramienta, ha pasado por un
generador de texto. La mayoria de las veces saldria intacta. Alguna vez no, y
el resultado seria un documento con el sello de Spark Match, cifras
presentadas como del MINEDU, y un estudiante decidiendo donde estudiar con
ellas. Ese fallo exacto ya vivio en este repositorio: el mock del frontend
atribuia al portal Ponte en Carrera ingresos que nunca publico.

Asi que el modelo pasa **solo prosa**: el resumen del perfil y una frase por
carrera. Los numeros los pone esta funcion volviendo a llamar al motor con los
mismos filtros. No es que se le pida al modelo que no invente cifras; es que
no hay parametro por donde meterlas.

**Los desajustes son errores, no huecos.** Si el modelo escribe sobre una
carrera que el motor no recomendo, o deja una sin explicar, la funcion falla y
dice cual. Emitir el informe a medias seria peor: un hueco en un PDF que
alguien va a leer sin nosotros delante no se distingue de una omision
deliberada.
"""

from __future__ import annotations

from typing import Any

from src.models.report import OrientationReport, ReportCareer
from src.tools.programs.loader import normalize
from src.tools.recommendation.handler import MAX_TOP_N, recommend_programs_handler

# Un informe con una sola carrera no es una orientacion, es una apuesta; y por
# encima de `MAX_TOP_N` el motor no da mas candidatos distintos de todos modos.
MIN_CAREERS = 2


def _error(mensaje: str) -> dict[str, Any]:
    return {"status": "error", "data": None, "errors": [mensaje]}


def _indexar_insights(insights: list[dict[str, str]]) -> tuple[dict[str, str], str | None]:
    """Mapa carrera-normalizada -> frase, o el motivo por el que no se puede.

    Se normaliza la clave porque el modelo escribe el nombre de la carrera a
    mano y "Ingenieria Industrial" tiene que casar con "Ingeniería Industrial".
    Pedirle una coincidencia exacta byte a byte seria hacer fallar el informe
    por un acento.
    """
    por_carrera: dict[str, str] = {}

    for i, entrada in enumerate(insights):
        if not isinstance(entrada, dict):
            return {}, f"insights[{i}] no es un objeto con 'career' e 'insight'"

        carrera = str(entrada.get("career", "")).strip()
        frase = str(entrada.get("insight", "")).strip()

        if not carrera:
            return {}, f"insights[{i}] no trae 'career'"
        if not frase:
            return (
                {},
                f"insights[{i}] ('{carrera}') no trae 'insight'; "
                f"una carrera sin explicar no entra en el informe",
            )

        clave = normalize(carrera)
        if clave in por_carrera:
            return {}, f"'{carrera}' aparece dos veces en insights"

        por_carrera[clave] = frase

    return por_carrera, None


def build_orientation_report_handler(
    riasec_code: str,
    profile_summary: str,
    insights: list[dict[str, str]],
    region: str | None = None,
    management_type: str | None = None,
    institution_type: str | None = None,
    max_annual_cost: float | None = None,
) -> dict[str, Any]:
    """Arma el informe cruzando la prosa del modelo con las cifras del motor.

    Args:
        riasec_code: Codigo Holland de 3 letras del estudiante.
        profile_summary: Retrato en prosa de su perfil. Lo escribe el modelo.
        insights: Una entrada ``{"career": ..., "insight": ...}`` por carrera
            que deba salir en el informe. El orden da igual: manda el del motor.
        region: Departamento, si el estudiante lo pidio.
        management_type: "publica" | "privada". None o "ambas" = sin filtro.
        institution_type: "universidad" | "instituto". None o "ambos" = sin filtro.
        max_annual_cost: Presupuesto anual maximo, en soles.

    Returns:
        El esquema habitual de los handlers, con ``data`` conteniendo un
        :class:`~src.models.report.OrientationReport` serializado.
    """
    resumen = (profile_summary or "").strip()
    if not resumen:
        return _error("profile_summary esta vacio; el informe necesita el retrato del perfil")

    if not isinstance(insights, list) or not insights:
        return _error("insights esta vacio; sin carreras explicadas no hay informe")

    if len(insights) > MAX_TOP_N:
        return _error(f"insights trae {len(insights)} carreras; el maximo es {MAX_TOP_N}")

    if len(insights) < MIN_CAREERS:
        return _error(
            f"insights trae {len(insights)} carrera; el informe necesita al menos "
            f"{MIN_CAREERS} para que el estudiante pueda comparar"
        )

    por_carrera, problema = _indexar_insights(insights)
    if problema is not None:
        return _error(problema)

    # El motor se pide entero (MAX_TOP_N) y no `len(insights)`: el subagente
    # pudo haber mirado un top-10 y elegido cinco, y recortar aqui a cinco
    # devolveria las cinco PRIMERAS, que no tienen por que ser las que eligio.
    ranking = recommend_programs_handler(
        riasec_code=riasec_code,
        region=region,
        management_type=management_type,
        institution_type=institution_type,
        max_annual_cost=max_annual_cost,
        top_n=MAX_TOP_N,
    )
    if ranking["status"] == "error":
        return ranking

    datos = ranking["data"]

    careers: list[ReportCareer] = []
    vistas: set[str] = set()
    for recomendacion in datos["recommendations"]:
        clave = normalize(recomendacion["career"])
        frase = por_carrera.get(clave)
        if frase is None:
            continue
        vistas.add(clave)
        careers.append(ReportCareer(**recomendacion, insight=frase))

    # Lo que el modelo explico y el motor no recomendo. Puede ser un nombre mal
    # copiado o una carrera que no existe en el catalogo; en los dos casos, la
    # cifra que la acompanaria no existiria.
    sobrantes = [
        entrada["career"] for entrada in insights if normalize(entrada["career"]) not in vistas
    ]
    if sobrantes:
        disponibles = ", ".join(r["career"] for r in datos["recommendations"])
        return _error(
            f"el motor no recomendo estas carreras, asi que no hay cifras que "
            f"ponerles: {', '.join(sobrantes)}. Las recomendadas son: {disponibles}"
        )

    return {
        "status": "success",
        "data": OrientationReport(
            profile_summary=resumen,
            riasec_code=datos["riasec_code"],
            careers=careers,
            total_candidates=datos["total_candidates"],
            careers_matched=datos["careers_matched"],
            filters_applied=datos["filters_applied"],
            candidates_without_each_filter=datos["candidates_without_each_filter"],
            scoring_version=datos["scoring_version"],
            source=datos["source"],
        ).model_dump(),
        "errors": None,
    }


__all__ = ["MIN_CAREERS", "build_orientation_report_handler"]
