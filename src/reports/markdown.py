"""El informe como Markdown (ADR-019, fase 4').

Por que Markdown: el informe se entrega en PDF y se guarda en JSON, y ninguno
de los dos se lee en una terminal cuando algo sale raro. Esta version si. El
PDF ya no se construye desde aqui --tiene su propia estructura en `html.py`,
con portada y fichas que una tabla de Markdown no sabe representar-- asi que
este fichero es el volcado legible del informe, y no tiene mas trabajo que ese.

**Aqui no se escribe prosa.** Todo el texto libre de este fichero es
estructura: encabezados, etiquetas de campo y las notas al pie sobre la
procedencia. El retrato del perfil y las explicaciones vienen ya escritas
dentro del `OrientationReport` (ver `src/tools/report/handler.py`), y esa
frontera se mantiene tambien aqui.

Como se escribe cada cifra vive en `cifras.py`, compartido con el PDF: si el
volcado y el documento formatean distinto, el que este depurando no sabe cual
de los dos miente.
"""

from __future__ import annotations

from src.models.report import OrientationReport, ReportCareer
from src.reports.cifras import (
    FILAS,
    MARCA_ESTIMADO,
    afinidades,
    gestion,
    nombre_legible,
    valor_marcado,
)


def _ficha(indice: int, carrera: ReportCareer, afinidad: str) -> list[str]:
    lineas = [
        f"### {indice}. {carrera.career} — {afinidad} de afinidad",
        "",
        f"{carrera.institution} · {carrera.location} · "
        f"{carrera.institution_type} {gestion(carrera)}",
        "",
        "| Dato | Valor |",
        "| --- | --- |",
    ]
    lineas += [f"| {etiqueta} | {valor_marcado(carrera, campo)} |" for campo, etiqueta in FILAS]
    lineas += ["", carrera.insight, ""]
    return lineas


def _procedencia(informe: OrientationReport) -> list[str]:
    """La letra pequena, que aqui no es letra pequena.

    Un informe que da cifras sin decir de donde salen invita a tratarlas como
    oficiales. Estas tres notas son las mismas que el agente esta obligado a
    dar en el chat; en un documento que se guarda y se ensena a terceros hacen
    mas falta, no menos, porque nadie va a estar delante para matizarlas.
    """
    notas = [
        "## Cómo leer este informe",
        "",
        f"- **La afinidad es un cálculo de Spark Match**, no una cifra oficial del "
        f"MINEDU. Criterio de puntuación `{informe.scoring_version}`.",
        f"- Las cifras de duración, ingreso, costo y admisión salen de "
        f"{informe.dataset_source}, datos del {informe.dataset_snapshot_date.isoformat()}.",
        f"- Lo marcado como «{MARCA_ESTIMADO}» **no es un dato de ese programa**: es la "
        "mediana de su familia de carrera, que se usa para rellenar lo que el portal no "
        "publicó.",
        "- El código RIASEC de cada carrera lo asignó un modelo de lenguaje. Orienta; no "
        "es una clasificación oficial.",
    ]

    if informe.filters_applied:
        recortes = ", ".join(
            f"sin {nombre_legible(nombre)} habría {cuantos:,}"
            for nombre, cuantos in sorted(informe.candidates_without_each_filter.items())
        )
        aplicados = ", ".join(nombre_legible(f) for f in informe.filters_applied)
        notas += [
            f"- Se filtró por {aplicados}, y eso dejó {informe.total_candidates:,} "
            f"programas de todo el catálogo ({recortes}). Cambiar un filtro cambia "
            "esta lista.",
        ]
    else:
        notas += [
            f"- No se aplicó ningún filtro: se comparó contra los "
            f"{informe.total_candidates:,} programas del catálogo.",
        ]

    return notas


def report_to_markdown(informe: OrientationReport) -> str:
    """El informe completo en Markdown."""
    lineas = [
        "# Informe de orientación vocacional",
        "",
        f"**Perfil RIASEC:** {informe.riasec_code}",
        "",
        "## Tu perfil",
        "",
        informe.profile_summary,
        "",
        "## Carreras recomendadas",
        "",
    ]

    # Las afinidades se calculan de una vez y no carrera a carrera: si dos
    # redondean igual hay que enseñar un decimal, y eso no se sabe mirando una
    # sola. Ver `cifras.afinidades`.
    etiquetas = afinidades(informe.careers)
    for indice, (carrera, afinidad) in enumerate(
        zip(informe.careers, etiquetas, strict=True), start=1
    ):
        lineas += _ficha(indice, carrera, afinidad)

    lineas += _procedencia(informe)

    return "\n".join(lineas).strip() + "\n"


__all__ = ["report_to_markdown"]
