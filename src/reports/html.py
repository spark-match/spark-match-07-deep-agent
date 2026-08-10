"""El informe como documento HTML, listo para WeasyPrint (ADR-019, fase 4').

Dos decisiones que conviene no deshacer:

**El HTML crudo del Markdown va deshabilitado.** CommonMark permite `<div>` y
compania dentro del texto, y por defecto markdown-it lo deja pasar. Aqui no:
dos de los campos del informe —el retrato del perfil y cada explicacion— los
escribe un modelo cuyo contexto contiene lo que ha tecleado el estudiante. No
es que se espere un ataque; es que basta una etiqueta mal cerrada, escrita sin
mala intencion, para descuadrar el documento entero. Con `html=False` cualquier
etiqueta sale como texto visible, que es un fallo que se ve y se corrige.

**Las tablas se habilitan a mano.** El preset `commonmark` no las trae, y la
ficha de cada carrera es una tabla.
"""

from __future__ import annotations

from pathlib import Path

from markdown_it import MarkdownIt

from src.models.report import OrientationReport
from src.reports.markdown import report_to_markdown

HOJA_DE_ESTILOS = Path(__file__).resolve().parent / "report.css"

_PLANTILLA = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>{titulo}</title>
</head>
<body>
{cuerpo}
</body>
</html>
"""

_TITULO = "Informe de orientación vocacional — Spark Match"


def _conversor() -> MarkdownIt:
    return MarkdownIt("commonmark", {"html": False}).enable("table")


def report_to_html(informe: OrientationReport) -> str:
    """Documento HTML completo. La hoja de estilos se pasa aparte a WeasyPrint."""
    cuerpo = _conversor().render(report_to_markdown(informe))
    return _PLANTILLA.format(titulo=_TITULO, cuerpo=cuerpo)


def read_stylesheet() -> str:
    """La hoja de estilos del informe, como texto."""
    return HOJA_DE_ESTILOS.read_text(encoding="utf-8")


__all__ = ["HOJA_DE_ESTILOS", "read_stylesheet", "report_to_html"]
