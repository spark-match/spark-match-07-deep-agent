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

Donde vive una llamada, que no es un solo sitio
------------------------------------------------

La primera version de este modulo miraba solo ``message.tool_calls`` y **no
arreglo nada en produccion**: el turno seguia fallando y el middleware ni
siquiera registraba haber reparado algo. El motivo esta en
``langchain_aws/chat_models/bedrock.py`` (lineas 611-637): el payload de
Bedrock se construye desde los DOS sitios donde puede vivir una llamada, la
lista ``tool_calls`` y los bloques ``{"type": "tool_use"}`` dentro de
``content``. Cuando un bloque de ``content`` trae un id que ``tool_calls``
no menciona, se manda tal cual::

    else:
        tool_blocks.append({k: v for k, v in item.items() if ...})

Y un turno cortado a mitad deja exactamente eso: el bloque en ``content``
consolidado y ``tool_calls`` sin consolidar. Asi que aqui se mira la union
de ambos, que es lo que la API va a ver de verdad. Misma idea del lado de
las respuestas: un ``tool_result`` puede llegar como ``ToolMessage`` o como
bloque dentro de ``content``.
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


def _content_blocks(message: AnyMessage) -> list[dict[str, Any]]:
    """Los bloques de `content`, o nada si el contenido es texto plano."""
    content = message.content
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _tool_use_ids(message: AIMessage) -> list[str]:
    """Ids que este mensaje va a mandar como `tool_use`, mire donde mire la API.

    Union de `tool_calls` y de los bloques `tool_use` de `content`. Mirar
    solo `tool_calls` fue el motivo exacto de que la primera version de esto
    no arreglara nada: el bloque huerfano vivia en `content`.
    """
    ids = [str(call["id"]) for call in message.tool_calls if call.get("id")]
    seen = set(ids)
    for block in _content_blocks(message):
        block_id = block.get("id")
        if block.get("type") != "tool_use" or not block_id:
            continue
        if str(block_id) not in seen:
            seen.add(str(block_id))
            ids.append(str(block_id))
    return ids


def _tool_result_ids(message: AnyMessage) -> set[str]:
    """Ids que este mensaje responde. Vacio si no responde a ninguna llamada."""
    ids: set[str] = set()
    if isinstance(message, ToolMessage) and message.tool_call_id:
        ids.add(str(message.tool_call_id))
    for block in _content_blocks(message):
        used = block.get("tool_use_id")
        if block.get("type") == "tool_result" and used:
            ids.add(str(used))
    return ids


def _gaps(messages: list[AnyMessage]) -> list[tuple[int, list[str]]]:
    """Por cada bloque de llamadas, donde insertar y que ids faltan.

    Recorre en orden y toma como respuestas de un ``AIMessage`` las que van
    INMEDIATAMENTE detras. Esa es la posicion que exige la API y la que
    produce LangGraph, asi que buscar el id por todo el historial daria por
    buena una respuesta colocada donde el modelo no la acepta.
    """
    found: list[tuple[int, list[str]]] = []
    index = 0
    total = len(messages)

    while index < total:
        message = messages[index]
        index += 1

        if not isinstance(message, AIMessage):
            continue
        pending = _tool_use_ids(message)
        if not pending:
            continue

        answered: set[str] = set()
        while index < total:
            replied = _tool_result_ids(messages[index])
            if not replied:
                break
            answered |= replied
            index += 1

        missing = [call_id for call_id in pending if call_id not in answered]
        if missing:
            found.append((index, missing))

    return found


def unpaired_tool_use_ids(messages: list[AnyMessage]) -> list[str]:
    """Ids que la API va a rechazar: `tool_use` sin su `tool_result` detras.

    Es la misma comprobacion que hace Anthropic, y se usa para verificar que
    la reparacion sirvio de algo antes de mandar la peticion.
    """
    return [call_id for _, ids in _gaps(messages) for call_id in ids]


def repair_tool_calls(messages: list[AnyMessage]) -> list[AnyMessage]:
    """Devuelve el historial con un resultado para cada llamada que no lo tenga.

    La lista de entrada no se modifica.
    """
    repaired = list(messages)
    # De atras hacia delante: insertar por delante desplazaria los indices
    # que quedan por usar.
    for insert_at, missing in reversed(_gaps(messages)):
        repaired[insert_at:insert_at] = [
            ToolMessage(content=ORPHAN_TOOL_RESULT, tool_call_id=call_id, status="error")
            for call_id in missing
        ]
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
        missing = unpaired_tool_use_ids(messages)
        if not missing:
            return request

        repaired = repair_tool_calls(messages)

        # A nivel WARNING y con los ids: que esto salte significa que una
        # conversacion venia rota. Con los ids delante, un fallo posterior se
        # puede cruzar contra el que reporta la API en vez de adivinar --
        # que es justo lo que costo la primera version de esto, que no
        # registraba nada y parecia estar funcionando.
        logger.warning(
            "tool_call_repair: %d llamada(s) sin resultado completadas: %s",
            len(missing),
            ", ".join(missing),
        )

        # Autocomprobacion con la misma regla que aplica la API. Si algo
        # queda sin emparejar, la peticion va a fallar igual y conviene que
        # el log lo diga aqui y no solo como ValidationException.
        still_broken = unpaired_tool_use_ids(repaired)
        if still_broken:
            logger.error(
                "tool_call_repair: siguen sin emparejar %s -- la peticion va a fallar",
                ", ".join(still_broken),
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


__all__ = [
    "ORPHAN_TOOL_RESULT",
    "ToolCallRepairMiddleware",
    "repair_tool_calls",
    "unpaired_tool_use_ids",
]
