"""Report package - emision del informe de orientacion (ADR-019, fases 4 y 5).

El modelo escribe la prosa, el motor pone las cifras, y la frontera entre las
dos es un parametro que no existe. Ver `handler.py` para el porque.

`handler.py` ensambla y `publish.py` emite: arma, sube a S3 y registra la fila
en el backend. Solo el segundo es una herramienta, y esa asimetria es
deliberada -- ver la cabecera de `publish.py`.
"""

from src.tools.report.handler import MIN_CAREERS, build_orientation_report_handler
from src.tools.report.publish import publish_orientation_report_handler
from src.tools.report.tool import publish_orientation_report

__all__ = [
    "MIN_CAREERS",
    "build_orientation_report_handler",
    "publish_orientation_report",
    "publish_orientation_report_handler",
]
