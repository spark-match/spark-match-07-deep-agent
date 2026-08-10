"""Report subagent — redacta el informe de orientacion (ADR-019, fase 4).

Es el cuarto especialista, y el unico que no conversa: produce un **documento**
que el estudiante guarda y relee. Se separa del subagente de matching, que ya
sabe rankear carreras, porque los dos objetivos se estorban en un mismo prompt
— uno responde en el momento y admite matices sobre la marcha, el otro escribe
algo que se va a leer sin nadie delante para aclararlo.

Solo dos herramientas, a proposito: ver el ranking y emitir el informe. Lo que
no tiene es cualquier via para escribir cifras — ver
``src/tools/report/handler.py``.
"""

from src.prompts import REPORT_SYSTEM_PROMPT
from src.tools.recommendation import recommend_programs
from src.tools.report import build_orientation_report

REPORT_SUBAGENT = {
    "name": "report",
    "description": (
        "Redacta el informe de orientación vocacional completo del estudiante: un "
        "documento con su perfil explicado en prosa y las carreras recomendadas con "
        "sus cifras reales (duración, ingreso, costo, tasa de admisión) y el porqué "
        "de cada una. Delegar cuando el estudiante pida su informe, su reporte o un "
        "resumen de todo lo trabajado, y ya tenga perfil RIASEC."
    ),
    "system_prompt": REPORT_SYSTEM_PROMPT,
    # `recommend_programs` para ver que carreras hay y con que cifras;
    # `build_orientation_report` para emitir. Nada mas: `search_careers`
    # tentaria a volcar la descripcion del catalogo dentro del `insight`, que
    # es justo lo que el prompt pide no hacer.
    "tools": [recommend_programs, build_orientation_report],
}
