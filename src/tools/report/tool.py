"""Report tool - thin @tool wrapper around publish_orientation_report_handler."""

from typing import Any

from langchain_core.tools import tool

from src.agent.user_context import get_user_id
from src.auth.current_token import get_request_token
from src.tools.report.publish import publish_orientation_report_handler


def _contexto_del_turno() -> tuple[str, Any]:
    """``(user_id, store)`` del turno en curso, o valores por defecto.

    ``get_runtime`` no es un argumento de la herramienta a proposito: cuanto
    se declare en la firma se lo ensena el esquema al modelo, y un
    ``user_id`` que el modelo pueda escribir es un `user_id` que puede
    equivocar -- o elegir. El dueno del informe sale del JWT, igual que en el
    backend.

    Fuera de un grafo (tests, invocacion directa) ``get_runtime`` lanza. Se
    devuelve el usuario por defecto y ``None`` de store, que es lo que hace
    que la puerta de D8 rechace por perfil vacio en vez de reventar.
    """
    try:
        from langgraph.runtime import get_runtime

        runtime = get_runtime()
    except Exception:
        return get_user_id(None), None
    return get_user_id(runtime), getattr(runtime, "store", None)


@tool
async def publish_orientation_report(
    riasec_code: str,
    profile_summary: str,
    insights: list[dict[str, str]],
    region: str | None = None,
    management_type: str | None = None,
    institution_type: str | None = None,
    max_annual_cost: float | None = None,
) -> dict[str, Any]:
    """Emite el informe de orientacion: lo arma, lo guarda y lo registra. Tu escribes la prosa.

    Es el ultimo paso y hace el trabajo entero de una vez: arma el documento,
    renderiza el PDF, lo guarda y lo deja disponible para el estudiante. No
    hay un segundo paso que llamar despues.

    Antes tienes que haber llamado a `recommend_programs` con los MISMOS
    filtros para ver que carreras salen.

    NO le pases cifras, y no las tiene como parametro a proposito: la
    duracion, el ingreso, el costo, la tasa de admision y la puntuacion las
    vuelve a calcular el motor aqui dentro. Tu aportas las dos cosas que el
    motor no puede: el retrato del perfil y el por que de cada carrera.

    Si te falta una carrera por explicar, o explicas una que el motor no
    recomendo, la herramienta falla y te dice cual. Corrigelo y vuelve a
    llamarla; no se habra creado nada a medias.

    Puede rechazarte por el estado del perfil del estudiante -- sin codigo
    RIASEC, o con muy pocos datos -- y en ese caso te dice que hacer. Eso no
    es un fallo tecnico: es que todavia no le toca informe, y la salida es
    seguir conversando con el.

    NO te devuelve el informe. Te devuelve su identificador y la lista de
    carreras, que es lo que necesitas para anunciarselo. El documento lo abre
    el en su pantalla.

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
        `{"status": "success", "data": {"report_id", "careers", ...}}` cuando
        el informe queda emitido. Si algo no cuadra, `status: "error"` con
        `errors` explicando que corregir o que decirle al estudiante.
    """
    user_id, store = _contexto_del_turno()

    return await publish_orientation_report_handler(
        user_id=user_id,
        token=get_request_token(),
        store=store,
        riasec_code=riasec_code,
        profile_summary=profile_summary,
        insights=insights,
        region=region,
        management_type=management_type,
        institution_type=institution_type,
        max_annual_cost=max_annual_cost,
    )
