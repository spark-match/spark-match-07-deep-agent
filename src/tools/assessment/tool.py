"""Assessment tool - thin @tool wrapper around the handler.

The wrapper delegates to the handler and unwraps the structured envelope
so the LLM receives the data dict directly (without the {status, data,
errors} wrapper, which would confuse it).

Ademas GUARDA el resultado en el perfil del estudiante. Ver
``src/memory/riasec_persist.py`` para por que eso no es un efecto secundario
oportunista sino la pieza que faltaba: sin ella el cuestionario se completaba,
el estudiante veia su codigo, y la puerta de D8 seguia diciendo que no tenia
perfil RIASEC.
"""

from typing import Any, cast

from langchain_core.tools import tool

from src.agent.user_context import get_user_id
from src.memory.riasec_persist import guardar_riasec_medido
from src.tools.assessment.handler import evaluate_riasec_profile_handler


def _contexto_del_turno() -> tuple[str, Any]:
    """``(user_id, store)`` del turno en curso, o valores por defecto.

    No son argumentos de la herramienta a proposito: lo que se declara en la
    firma se lo ensena el esquema al modelo, y un ``user_id`` que el modelo
    pueda escribir es un perfil que puede acabar en el buzon de otro. Misma
    regla que en ``src/tools/report/tool.py``.

    Fuera de un grafo (tests, invocacion directa) ``get_runtime`` lanza; se
    devuelve ``None`` de store y el guardado se salta sin romper nada.
    """
    try:
        from langgraph.runtime import get_runtime

        runtime = get_runtime()
    except Exception:
        return get_user_id(None), None
    return get_user_id(runtime), getattr(runtime, "store", None)


@tool
async def evaluate_riasec_profile(
    realistic: int,
    investigative: int,
    artistic: int,
    social: int,
    enterprising: int,
    conventional: int,
) -> dict[str, Any]:
    """Evaluate a student's RIASEC vocational profile from assessment scores.

    Each dimension is scored 1-10 based on the student's responses.
    Returns the computed profile with dominant types and interpretation.

    Saves the six scores and the resulting code to the student's profile, so
    the rest of the system (career matching, the orientation report) can use
    them without asking the student to take the questionnaire again.

    Args:
        realistic: Score for Realistic (hands-on, physical, mechanical)
        investigative: Score for Investigative (analytical, intellectual, scientific)
        artistic: Score for Artistic (creative, expressive, unstructured)
        social: Score for Social (helping, teaching, counseling)
        enterprising: Score for Enterprising (leading, persuading, managing)
        conventional: Score for Conventional (organizing, data, detail-oriented)
    """
    result = evaluate_riasec_profile_handler(
        realistic=realistic,
        investigative=investigative,
        artistic=artistic,
        social=social,
        enterprising=enterprising,
        conventional=conventional,
    )

    # Surface errors to the LLM as a dict so it can recover gracefully.
    if result["status"] == "error":
        return {"error": True, "errors": result["errors"]}

    data = cast(dict[str, Any], result["data"])

    # Despues de validar y solo si validó: un perfil escrito a partir de
    # puntuaciones que el handler rechazo seria peor que no escribir nada.
    user_id, store = _contexto_del_turno()
    await guardar_riasec_medido(
        store,
        user_id,
        scores=cast(dict[str, int], data["scores"]),
        riasec_code=cast(str, data["riasec_code"]),
    )

    return data
