"""Eventos AG-UI para la delegacion en subagentes.

deepagents no expone la delegacion como nada visible desde fuera: los tres
subagentes viven detras de UNA sola herramienta llamada ``task``
(``StructuredTool.from_function(name="task")`` en
``deepagents.middleware.subagents``), y su handler corre el subagente
entero con ``ainvoke()`` dentro de esa misma llamada. Al navegador solo
llega ``toolCallName="task"``, asi que el frontend no puede decir mas que
un generico "consultando a un especialista", ni distinguir cual, ni saber
cuando termino.

El protocolo AG-UI tampoco tiene un evento propio de subagente. Lo que si
tiene es un pasillo generico: ``ag_ui_langgraph`` traduce cualquier
``on_custom_event`` cuyo nombre no sea uno de los cuatro reservados de
``CustomEventNames`` a un ``CustomEvent(type=CUSTOM, name=..., value=...)``
que viaja por el mismo stream SSE (``ag_ui_langgraph/agent.py:1344``). Este
middleware usa ese pasillo, que es el unico que no exige tocar la libreria.

Lo que se manda y lo que no:

- Se manda ``subagent``, la clave estable del especialista (``assessment``,
  ``matching``, ``planning``), para que el frontend la traduzca a una
  etiqueta suya. La copia en castellano es cosa de la UI, no del agente:
  aqui viaja un identificador, no un texto que alguien vaya a leer.
- NO se manda ``description``, el otro argumento de ``task``. Es la
  instruccion que el coordinador redacta para el subagente, o sea un prompt
  interno. Mismo criterio por el que ``src/api/app.py`` filtra los eventos
  RAW antes de que salgan del servidor.

El evento de cierre se emite tambien cuando el subagente revienta
(``ok: false``). Sin eso, un fallo dejaria el indicador girando para
siempre en la pantalla del estudiante, que es justo el sintoma que este
contrato existe para quitar.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.callbacks.manager import (
    adispatch_custom_event,
    dispatch_custom_event,
)
from langchain_core.messages import ToolMessage
from langgraph.types import Command

logger = logging.getLogger(__name__)

# Nombre con el que deepagents registra la herramienta de delegacion.
SUBAGENT_TOOL_NAME = "task"

# Nombres de los eventos custom. Con prefijo propio a proposito: el espacio
# de nombres de ``on_custom_event`` es compartido con la libreria, y los
# cuatro nombres que ag_ui_langgraph trata de forma especial
# (manually_emit_message, manually_emit_tool_call, manually_emit_state,
# exit) no llevan prefijo. Un nombre nuestro sin prefijo se arriesga a
# colisionar con uno reservado que agreguen mas adelante.
SUBAGENT_START_EVENT = "spark.subagent.start"
SUBAGENT_END_EVENT = "spark.subagent.end"

#: Se emite cuando un informe queda registrado y el estudiante ya puede
#: abrirlo. Existe porque el chat no tenia forma de saberlo: el contenido del
#: informe no vuelve al contexto a proposito, asi que lo unico que llegaba a
#: la pantalla era el modelo diciendo "tu informe esta listo" sin nada que
#: pulsar. El estudiante tenia que adivinar que habia una seccion "Reporte"
#: en el menu.
REPORT_READY_EVENT = "spark.report.ready"

# Cuando el modelo llama a ``task`` sin decir a quien. deepagents responde a
# eso con un mensaje de error y no invoca a nadie, pero el evento se emite
# igual para que el par start/end siga estando completo.
UNKNOWN_SUBAGENT = "desconocido"


def _subagent_type(request: ToolCallRequest) -> str | None:
    """Clave del subagente al que va esta llamada, o ``None`` si no es ``task``.

    ``args`` puede llegar como cadena sin parsear cuando el modelo emite
    JSON invalido; en ese caso se trata como si no hubiera argumentos, en
    vez de reventar dentro de un hook que solo esta para observar.
    """
    tool_call = request.tool_call
    if tool_call.get("name") != SUBAGENT_TOOL_NAME:
        return None

    args = tool_call.get("args")
    if not isinstance(args, dict):
        return UNKNOWN_SUBAGENT

    subagent_type = args.get("subagent_type")
    return str(subagent_type) if subagent_type else UNKNOWN_SUBAGENT


def _payload(tool_call_id: str, subagent: str, **extra: Any) -> dict[str, Any]:
    """Cuerpo del evento. ``toolCallId`` en camelCase porque el consumidor
    es el frontend, y ahi ese es el nombre del campo en el resto del
    protocolo AG-UI."""
    return {"toolCallId": tool_call_id, "subagent": subagent, **extra}


def _elapsed_ms(started: float) -> int:
    """Milisegundos transcurridos, con reloj monotono.

    Monotono y no de pared: al estudiante se le va a ensenar esto como
    "tardo 8 s", y un ajuste de hora del sistema a mitad de turno no tiene
    por que producir una duracion negativa.
    """
    return round((time.monotonic() - started) * 1000)


def _emit(name: str, payload: dict[str, Any]) -> None:
    """Emite el evento sincrono, tragandose el fallo si no hay callbacks.

    ``dispatch_custom_event`` exige un callback manager en contexto y lanza
    ``RuntimeError`` cuando no lo hay -- p.ej. si alguien invoca el grafo
    fuera de ``astream_events``. Esto es telemetria para la UI: que falte no
    puede tumbar el turno del estudiante.
    """
    try:
        dispatch_custom_event(name, payload)
    except RuntimeError:
        logger.debug("no se pudo emitir %s (sin callbacks en contexto)", name, exc_info=True)


async def _aemit(name: str, payload: dict[str, Any]) -> None:
    """Contraparte async de :func:`_emit`."""
    try:
        await adispatch_custom_event(name, payload)
    except RuntimeError:
        logger.debug("no se pudo emitir %s (sin callbacks en contexto)", name, exc_info=True)


class SubagentEventsMiddleware(AgentMiddleware):
    """Anuncia el inicio y el fin de cada delegacion en un subagente.

    Implementa los dos hooks, sincrono y async, por la misma razon ya
    documentada en ``src/agent/middleware.py``: LangChain lanza
    ``NotImplementedError`` para toda llamada a herramienta en el modo que
    no este implementado, y la API de produccion (``ag-ui-langgraph``)
    recorre el grafo exclusivamente con ``astream_events``.
    """

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        subagent = _subagent_type(request)
        if subagent is None:
            return handler(request)

        tool_call_id = str(request.tool_call.get("id") or "")
        _emit(SUBAGENT_START_EVENT, _payload(tool_call_id, subagent))
        started = time.monotonic()
        ok = False
        try:
            result = handler(request)
            ok = True
            return result
        finally:
            _emit(
                SUBAGENT_END_EVENT,
                _payload(tool_call_id, subagent, ok=ok, durationMs=_elapsed_ms(started)),
            )

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        subagent = _subagent_type(request)
        if subagent is None:
            return await handler(request)

        tool_call_id = str(request.tool_call.get("id") or "")
        await _aemit(SUBAGENT_START_EVENT, _payload(tool_call_id, subagent))
        started = time.monotonic()
        ok = False
        try:
            result = await handler(request)
            ok = True
            return result
        finally:
            await _aemit(
                SUBAGENT_END_EVENT,
                _payload(tool_call_id, subagent, ok=ok, durationMs=_elapsed_ms(started)),
            )


async def avisar_informe_listo(report_id: str, careers: list[str]) -> None:
    """Le dice a la pantalla que ya hay un informe que abrir.

    Se traga el fallo igual que el resto de eventos de aqui: esto es un aviso
    para la interfaz, y que no llegue no puede tumbar un turno en el que el
    informe ya se emitio correctamente. El peor caso es el de antes, que el
    estudiante tenga que ir a buscarlo al menu.
    """
    await _aemit(REPORT_READY_EVENT, {"reportId": report_id, "careers": careers})


__all__ = [
    "REPORT_READY_EVENT",
    "SUBAGENT_END_EVENT",
    "SUBAGENT_START_EVENT",
    "SUBAGENT_TOOL_NAME",
    "UNKNOWN_SUBAGENT",
    "SubagentEventsMiddleware",
    "avisar_informe_listo",
]
