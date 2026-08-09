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

Esto costo dos despliegues fallidos, y merece la pena contar por que.

La primera version miraba solo ``message.tool_calls``. La segunda anadio los
bloques ``{"type": "tool_use"}`` de ``content``. Las dos siguieron dando el
historial por sano mientras la API lo rechazaba, y las dos pasaron sus tests
-- porque los tests validaban MI lectura de los mensajes, no la peticion que
sale hacia Bedrock.

La llamada no vive en un sitio, vive en cuatro. ``_format_anthropic_messages``
reescribe el contenido de cada ``AIMessage`` con ``output_version="v1"``
llamando a ``_convert_from_v1_to_anthropic``
(``langchain_aws/chat_models/bedrock.py``), y ahi tres tipos de bloque
distintos acaban siendo un ``tool_use``: ``tool_use``, ``tool_call`` y
``tool_call_chunk``. El ultimo es justo lo que deja un stream cortado a
mitad: el trozo llego, ``tool_calls`` nunca se consolido. Invisible desde
``tool_calls``, invisible desde ``tool_use``, y perfectamente visible para la
API.

De ahi las dos defensas que tiene ahora este modulo:

- ``_TOOL_USE_BLOCK_TYPES`` sale de leer ese conversor, no de suponer.
- Los tests comprueban el payload YA CONVERTIDO, llamando al conversor de
  langchain_aws en vez de reimplementar sus reglas. Un test que valida mi
  lectura de los mensajes es exactamente el que dejo pasar los dos fallos.

Y por si aparece una quinta representacion: si un bloque tiene ``id`` y pinta
de llamada pero este modulo no sabe leerlo, se registra por log. "No hay nada
que reparar" y "no supe verlo" no pueden volver a leerse igual.
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


# Tipos de bloque de `content` que acaban siendo un `tool_use` en la peticion.
# No es una lista puesta a ojo: es exactamente lo que traduce
# `_convert_from_v1_to_anthropic` en langchain_aws/chat_models/bedrock.py,
# que reescribe el contenido de cada AIMessage con output_version="v1" antes
# de mandarlo.
#
#   "tool_use"        ya viene en formato Anthropic y pasa tal cual
#   "tool_call"       -> {"type": "tool_use", "id": block["id"], ...}
#   "tool_call_chunk" -> {"type": "tool_use", "id": block["id"], ...}
#
# El tercero es el que importa aqui: un stream cortado a mitad deja trozos
# `tool_call_chunk` que nunca se consolidaron en `tool_calls`. Mirar solo
# "tool_use" (y antes, solo `tool_calls`) es lo que hizo que dos intentos de
# arreglar esto dieran el historial por sano mientras la API lo rechazaba.
_TOOL_USE_BLOCK_TYPES = frozenset({"tool_use", "tool_call", "tool_call_chunk"})

# Claves que delatan una llamada en un bloque que no reconocemos. Solo para
# avisar por log: mejor una pista de que hay una representacion nueva que otra
# ronda de despliegues a ciegas.
_CALL_LIKE_KEYS = frozenset({"name", "args", "input"})


def _content_blocks(message: AnyMessage) -> list[dict[str, Any]]:
    """Los bloques de `content`, o nada si el contenido es texto plano."""
    content = message.content
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def _block_tool_use_id(block: dict[str, Any]) -> str | None:
    """El id de llamada que este bloque va a mandar, si manda alguno."""
    block_type = block.get("type")
    if block_type in _TOOL_USE_BLOCK_TYPES:
        block_id = block.get("id")
        return str(block_id) if block_id else None
    # `non_standard` viaja verbatim a la peticion (`new_content.append(
    # block["value"])` en el mismo conversor), asi que su contenido puede ser
    # un tool_use ya formado.
    if block_type == "non_standard":
        value = block.get("value")
        if isinstance(value, dict) and value.get("type") == "tool_use" and value.get("id"):
            return str(value["id"])
    return None


def _tool_use_ids(message: AIMessage) -> list[str]:
    """Ids que este mensaje va a mandar como `tool_use`, mire donde mire la API.

    Union de `tool_calls` y de los bloques de `content` que el conversor de
    langchain_aws convierte en `tool_use`.
    """
    ids = [str(call["id"]) for call in message.tool_calls if call.get("id")]
    seen = set(ids)
    for block in _content_blocks(message):
        block_id = _block_tool_use_id(block)
        if block_id and block_id not in seen:
            seen.add(block_id)
            ids.append(block_id)
    return ids


def _unrecognised_call_blocks(message: AIMessage) -> list[str]:
    """Tipos de bloque con pinta de llamada que este modulo no sabe leer.

    Existe porque el modo de fallo de esto es silencioso: si una version nueva
    de langchain empieza a representar las llamadas de otra forma, la
    reparacion vuelve a dar el historial por sano y el unico sintoma es una
    ValidationException sin pista. Mejor que lo diga el log.
    """
    return [
        str(block.get("type"))
        for block in _content_blocks(message)
        if _block_tool_use_id(block) is None and block.get("id") and _CALL_LIKE_KEYS & block.keys()
    ]


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

        # Antes de decidir que no hay nada que hacer: avisar si aparece una
        # forma de representar una llamada que este modulo no conoce. Sin
        # esto, "no hay nada que reparar" y "no supe verlo" se leen igual.
        for message in messages:
            if not isinstance(message, AIMessage):
                continue
            unknown = _unrecognised_call_blocks(message)
            if unknown:
                logger.warning(
                    "tool_call_repair: bloques con pinta de llamada sin leer: %s",
                    ", ".join(sorted(set(unknown))),
                )

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
