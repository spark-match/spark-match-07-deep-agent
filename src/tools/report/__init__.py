"""Report package - ensamblado del informe de orientacion (ADR-019, fase 4).

El modelo escribe la prosa, el motor pone las cifras, y la frontera entre las
dos es un parametro que no existe. Ver `handler.py` para el porque.
"""

from src.tools.report.handler import MIN_CAREERS, build_orientation_report_handler
from src.tools.report.tool import build_orientation_report

__all__ = [
    "MIN_CAREERS",
    "build_orientation_report",
    "build_orientation_report_handler",
]
