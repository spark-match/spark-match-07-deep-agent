"""El informe como Markdown (ADR-019, fase 4').

Por que Markdown en medio y no HTML directo: el informe se entrega en PDF pero
tambien se guarda en JSON, y algun dia se vera en la web. Markdown es el unico
formato de los tres que se lee entero en una terminal cuando algo sale raro, y
el diff de una plantilla Markdown en una revision se entiende sin abrir un
navegador.

**Aqui no se escribe prosa.** Todo el texto libre de este fichero es
estructura: encabezados, etiquetas de campo y las notas al pie sobre la
procedencia. El retrato del perfil y las explicaciones vienen ya escritas
dentro del `OrientationReport` (ver `src/tools/report/handler.py`), y esa
frontera se mantiene tambien aqui.

Formato de cifras: separador de miles con coma y decimales con punto, que es
la convencion peruana y la que ya usa la web. Un informe que escribe las
cantidades distinto que la pantalla de la que salio parece de otro sitio.
"""

from __future__ import annotations

from src.models.report import OrientationReport, ReportCareer

# Como se llama cada cifra en el informe, y en que orden se listan. El orden
# es deliberado: primero lo que dura (el compromiso), luego lo que se gana
# (el motivo), luego lo que cuesta y lo dificil que es entrar (las dos
# barreras). Es el orden en que un estudiante hace las preguntas.
_FILAS: tuple[tuple[str, str], ...] = (
    ("duration_years", "Duración"),
    ("monthly_income", "Ingreso mensual al egresar"),
    ("annual_cost", "Costo anual"),
    ("admission_rate", "Admisión"),
)

_MARCA_ESTIMADO = "estimado"


def _soles(cantidad: float) -> str:
    """S/ 4,261 — sin decimales, que en estas magnitudes son ruido."""
    return f"S/ {cantidad:,.0f}"


def _anios(cantidad: float) -> str:
    """5 años, o 3.5 años cuando la cifra no es redonda."""
    if abs(cantidad - round(cantidad)) < 0.05:
        entero = round(cantidad)
        return f"{entero} año" if entero == 1 else f"{entero} años"
    return f"{cantidad:.1f} años"


def _porcentaje(fraccion: float) -> str:
    """La tasa de admision viaja 0-1 y se lee en porcentaje."""
    return f"{fraccion * 100:.0f}%"


def _valor(carrera: ReportCareer, campo: str) -> str:
    bruto = getattr(carrera, campo)
    if campo == "duration_years":
        texto = _anios(bruto)
    elif campo == "admission_rate":
        texto = _porcentaje(bruto)
    else:
        texto = _soles(bruto)

    # Un dato imputado marcado como tal es la diferencia entre informar y
    # aparentar. La lista `estimated` llega hasta aqui justo para esto.
    if campo in carrera.estimated:
        return f"{texto} ({_MARCA_ESTIMADO})"
    return texto


def _ficha(indice: int, carrera: ReportCareer) -> list[str]:
    afinidad = f"{carrera.match_score:.0f}%"
    lineas = [
        f"### {indice}. {carrera.career} — {afinidad} de afinidad",
        "",
        f"{carrera.institution} · {carrera.location} · "
        f"{carrera.institution_type} {carrera.management_type.lower()}",
        "",
        "| Dato | Valor |",
        "| --- | --- |",
    ]
    lineas += [f"| {etiqueta} | {_valor(carrera, campo)} |" for campo, etiqueta in _FILAS]
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
        f"- Las cifras de duración, ingreso, costo y admisión salen de {informe.source}.",
        f"- Lo marcado como «{_MARCA_ESTIMADO}» **no es un dato de ese programa**: es la "
        "mediana de su familia de carrera, que se usa para rellenar lo que el portal no "
        "publicó.",
        "- El código RIASEC de cada carrera lo asignó un modelo de lenguaje. Orienta; no "
        "es una clasificación oficial.",
    ]

    if informe.filters_applied:
        recortes = ", ".join(
            f"sin «{nombre}» habría {cuantos:,}"
            for nombre, cuantos in sorted(informe.candidates_without_each_filter.items())
        )
        notas += [
            f"- Se filtró por {', '.join(informe.filters_applied)}, y eso dejó "
            f"{informe.total_candidates:,} programas de todo el catálogo ({recortes}). "
            "Cambiar un filtro cambia esta lista.",
        ]
    else:
        notas += [
            f"- No se aplicó ningún filtro: se comparó contra los "
            f"{informe.total_candidates:,} programas del catálogo.",
        ]

    return notas


def report_to_markdown(informe: OrientationReport) -> str:
    """El informe completo en Markdown, listo para convertir a HTML."""
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

    for indice, carrera in enumerate(informe.careers, start=1):
        lineas += _ficha(indice, carrera)

    lineas += _procedencia(informe)

    return "\n".join(lineas).strip() + "\n"


__all__ = ["report_to_markdown"]
