"""Renderizado del informe de orientacion (ADR-019, fase 4').

`OrientationReport` -> Markdown -> HTML -> PDF, en tres modulos separados
porque los dos primeros pasos son Python puro y se prueban enteros, y el
tercero necesita bibliotecas del sistema que no estan en todas partes.

Vive en el agente y no en el backend porque WeasyPrint necesita pango, cairo
y gdk-pixbuf, y el backend corre en Lambda (D11).
"""

from src.reports.html import report_to_html
from src.reports.markdown import report_to_markdown
from src.reports.pdf import (
    PdfRenderingUnavailableError,
    pdf_rendering_available,
    report_to_pdf,
)

__all__ = [
    "PdfRenderingUnavailableError",
    "pdf_rendering_available",
    "report_to_html",
    "report_to_markdown",
    "report_to_pdf",
]
