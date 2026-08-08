"""Repara llamadas a herramienta que se quedaron sin resultado.

El problema que resuelve, medido en dev el 2026-08-08: una conversacion
entera dejo de funcionar de forma permanente. Cada turno posterior fallaba
con la MISMA excepcion, siempre en el mismo indice y con el mismo id::

    ValidationException (InvokeModelWithResponseStream): messages.22:
    `tool_use` ids were found without `tool_result` blocks immediately
    after: toolu_bdrk_013v2T9o6kDS2QarNA7DVroF

La API de Anthropic exige que cada bloque ``tool_use`` vaya seguido de su
``tool_result``. Si el checkpoint guarda un ``AIMessage`` con ``tool_calls``
y el ``ToolMessage`` correspondiente nunca llega, el historial queda
invalido -- y como cada turno reenvia el historial completo, el modelo lo
rechaza antes de generar una sola palabra. La conversacion no se recupera
sola: esta muerta hasta que alguien borre el hilo.

Como se llega a ese estado: el turno se corta entre el nodo ``model`` y el
nodo ``tools``. El ``AIMessage`` ya esta persistido y el ``ToolMessage`` no
llega nunca. Basta con recargar la pagina, cambiar de conversacion (el
frontend aborta el stream con ``AbortController``) o que se caiga el SSE en
el momento justo. O sea: no es un caso raro, es un caso de martes.

Que hace este middleware: antes de CADA llamada al modelo, recorre los
mensajes y, por cada ``tool_call`` sin respuesta, inserta un ``ToolMessage``
de error en su sitio. El modelo ve "esa herramienta no llego a ejecutarse",
que es exactamente lo que paso, y sigue la conversacion.

Dos decisiones que no son obvias:

- **Se inserta, no se borra.** Descartar el ``AIMessage`` huerfano tambien
  arreglaria el historial, pero ese mensaje puede llevar texto que el
  estudiante ya leyo en pantalla; borrarlo dejaria la conversacion diciendo
  algo distinto de lo que se vio.
- **No se reescribe el checkpoint.** La reparacion se aplica sobre lo que
  se le manda al modelo, no sobre lo persistido. Es idempotente, se aplica
  igual en cada turno, y no toca el estado guardado -- que es justo lo que
  no conviene manipular para arreglar un sintoma. El efecto util es que
  esto **revive tambien los hilos que ya estaban envenenados**, sin
  migracion ni borrado.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AnyMessage, ToolMessage

logger = logging.getLogger(__name__)

# Lo que ve el modelo en lugar del resultado que nunca llego. En espanol y
# explicito: es lo que va a leer para decidir que hacer, y "error" a secas
# le invita a reintentar la misma llamada en bucle.
ORPHAN_TOOL_RESULT = (
    "La herramienta no llego a ejecutarse porque la conversacion se "
    "interrumpio antes de que devolviera un resultado. No hay datos de esta "
    "llamada. Si los necesitas, vuelve a pedirlos; si no, sigue sin ellos."
)


def _tool_call_ids(message: AIMessage) -> list[str]:
    """Ids de las llamadas de este mensaje, saltando las que no lo traen."""
    return [str(call["id"]) for call in message.tool_calls if call.get("id")]


def repair_tool_calls(messages: list[AnyMessage]) -> list[AnyMessage]:
    """Devuelve el historial con un resultado para cada llamada que no lo tenga.

    Recorre en orden y trata como respuestas de un ``AIMessage`` los
    ``ToolMessage`` que van INMEDIATAMENTE detras. Esa es la posicion que
    exige la API y la que produce LangGraph, asi que buscar el id por todo
    el historial daria por buena una respuesta que en realidad esta
    colocada donde el modelo no la acepta.

    La lista de entrada no se modifica.
    """
    repaired: list[AnyMessage] = []
    index = 0
    total = len(messages)

    while index < total:
        message = messages[index]
        repaired.append(message)
        index += 1

        if not isinstance(message, AIMessage) or not message.tool_calls:
            continue

        answered: set[str] = set()
        while index < total:
            # A una variable antes del isinstance: comprobar el tipo sobre
            # `messages[index]` no estrecha nada para quien lee los tipos.
            following = messages[index]
            if not isinstance(following, ToolMessage):
                break
            answered.add(str(following.tool_call_id))
            repaired.append(following)
            index += 1

        for call_id in _tool_call_ids(message):
            if call_id in answered:
                continue
            repaired.append(
                ToolMessage(content=ORPHAN_TOOL_RESULT, tool_call_id=call_id, status="error")
            )

    return repaired


class ToolCallRepairMiddleware(AgentMiddleware[Any, Any, Any]):
    """Completa las llamadas a herramienta huerfanas antes de llamar al modelo.

    Va al final de la lista de middleware, o sea lo mas pegado al modelo:
    cualquier otro que reordene o inyecte mensajes lo hace antes, y esto es
    lo ultimo que los mira. Si se pusiera al principio, un middleware
    posterior podria volver a dejar el historial invalido.
    """

    def _repair(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        messages = list(request.messages)
        repaired = repair_tool_calls(messages)
        added = len(repaired) - len(messages)
        if not added:
            return request

        # A nivel WARNING y con el numero: que esto salte es que una
        # conversacion venia rota, y saber cuantas veces pasa es la unica
        # forma de enterarse de si el corte de turnos es frecuente.
        logger.warning(
            "tool_call_repair: %d llamada(s) sin resultado completadas en el historial",
            added,
        )
        return request.override(messages=repaired)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """Repara el historial y sigue con la llamada."""
        return handler(self._repair(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Contraparte async. La produccion (ag_ui_langgraph) usa solo esta."""
        return await handler(self._repair(request))


__all__ = ["ORPHAN_TOOL_RESULT", "ToolCallRepairMiddleware", "repair_tool_calls"]
