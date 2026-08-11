"""Lo que pasa dentro de un subagente y el padre tiene que conservar.

Un subagente de deepagents corre entero dentro de la herramienta ``task``, con
su propio grafo, y de todo lo que hace **solo vuelve su texto final**:
``_return_command_with_state_update`` (``deepagents/middleware/subagents.py``)
arma un ``Command`` cuyo ``messages`` es un unico ``ToolMessage`` con esa
cadena, y descarta los mensajes internos. Lo que el subagente llamo, y lo que
le devolvieron, no llega nunca al checkpoint del grafo padre.

Casi siempre da igual -- es justo el aislamiento que se busca al delegar --
pero hay cosas que se producen ahi dentro y el estudiante necesita al recargar
la pagina. La primera es **el id del informe**: ``publish_orientation_report``
vive en el subagente de report, asi que al refrescar el enlace a su informe
desaparecia aunque el informe existiera. El evento en vivo
``spark.report.ready`` lo cuenta mientras pasa; el historial no tenia de donde
sacarlo.

## El buzon

El padre abre un buzon antes de delegar, la herramienta de dentro escribe en
el, y el padre lo vacia sobre el ``ToolMessage`` que si se persiste.

**Es un ContextVar con un diccionario MUTABLE dentro, y esa distincion es todo
el mecanismo.** Una ``asyncio.Task`` nace con una *copia* del contexto, asi que
un ``.set()`` hecho desde dentro del subagente no se ve desde fuera -- seria un
buzon que nadie recoge. Lo que si cruza es la referencia: la copia apunta al
MISMO diccionario, y mutarlo se ve en los dos lados. Comprobado, no supuesto,
incluyendo el salto a un hilo de ``asyncio.to_thread``.

**Viaja en ``additional_kwargs`` y bajo una clave nuestra.** Ese campo lo
serializa el checkpointer con el resto del mensaje, que es la unica propiedad
que hace falta aqui. La clave propia (:data:`CLAVE`) evita pelearse con lo que
los proveedores meten ahi.

**Nada de esto puede tumbar un turno.** Sin buzon abierto -- invocacion directa
del grafo, tests -- ``anotar`` no hace nada, y un resultado con una forma que no
se reconoce se devuelve intacto. Perder el enlace al informe es un incordio;
perder el informe seria otra cosa.
"""

from __future__ import annotations

import contextlib
import dataclasses
import logging
from collections.abc import Awaitable, Callable, Iterator
from contextvars import ContextVar
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from src.agent.subagent_events import SUBAGENT_TOOL_NAME

logger = logging.getLogger(__name__)

#: Bajo que clave viaja lo recogido dentro de ``additional_kwargs``. Con
#: prefijo propio porque ese diccionario es de todos: los proveedores escriben
#: ahi lo suyo y un nombre generico acabaria pisando o pisado.
CLAVE = "spark"

#: El id del informe emitido durante la delegacion.
INFORME = "reportId"

_BUZON: ContextVar[dict[str, Any] | None] = ContextVar("spark_subagent_carryover", default=None)


@contextlib.contextmanager
def buzon_abierto() -> Iterator[dict[str, Any]]:
    """Abre un buzon para la delegacion que empieza y lo cierra al salir.

    El ``reset`` va en ``finally`` y no suelto: sin el, un turno que reviente a
    mitad dejaria el buzon de esa delegacion colgado del contexto, y la
    siguiente escribiria encima de lo que quedo.
    """
    caja: dict[str, Any] = {}
    testigo = _BUZON.set(caja)
    try:
        yield caja
    finally:
        _BUZON.reset(testigo)


def anotar(clave: str, valor: Any) -> None:
    """Deja algo para el padre. Sin buzon abierto no hace nada.

    Que no haya buzon es normal y no es un error: pasa en cada invocacion
    directa del grafo y en la mayoria de los tests, y tambien si algun dia una
    de estas herramientas se llama fuera de un subagente.
    """
    caja = _BUZON.get()
    if caja is not None:
        caja[clave] = valor


def adjuntar(
    resultado: ToolMessage | Command[Any], caja: dict[str, Any]
) -> ToolMessage | Command[Any]:
    """Cuelga lo recogido del ``ToolMessage`` que devuelve la delegacion.

    Devuelve el resultado intacto cuando no hay nada que colgar o cuando no
    tiene la forma que deepagents produce hoy. Lo segundo es deliberado: esto
    depende de una estructura interna de otra libreria, y si cambia el precio
    tiene que ser quedarnos sin enlace al informe, no romper la delegacion.
    """
    if not caja:
        return resultado

    if isinstance(resultado, ToolMessage):
        return _con_lo_recogido(resultado, caja)

    if not isinstance(resultado, Command):
        logger.debug("La delegacion devolvio algo inesperado; no se adjunta nada")
        return resultado

    actualizacion = resultado.update
    if not isinstance(actualizacion, dict):
        return resultado
    mensajes = actualizacion.get("messages")
    if not isinstance(mensajes, list):
        return resultado

    # `dataclasses.replace` y no tocar el original: `Command` es un dataclass
    # CONGELADO, y ademas asi no se pierden `goto`, `resume` ni `graph`, que
    # reconstruirlo a mano dejaria por el camino.
    return dataclasses.replace(
        resultado,
        update={
            **actualizacion,
            "messages": [
                _con_lo_recogido(m, caja) if isinstance(m, ToolMessage) else m for m in mensajes
            ],
        },
    )


def _con_lo_recogido(mensaje: ToolMessage, caja: dict[str, Any]) -> ToolMessage:
    """Una copia del mensaje con el buzon dentro de ``additional_kwargs``."""
    extras = dict(mensaje.additional_kwargs)
    extras[CLAVE] = {**(extras.get(CLAVE) or {}), **caja}
    return mensaje.model_copy(update={"additional_kwargs": extras})


def lo_recogido(mensaje: Any) -> dict[str, Any]:
    """Lo que se saco de una delegacion, o ``{}``.

    Lo usa ``src/threads/history.py`` para republicarlo. Tolera cualquier forma
    porque lee mensajes escritos por builds anteriores a que esto existiera.
    """
    extras = getattr(mensaje, "additional_kwargs", None)
    if not isinstance(extras, dict):
        return {}
    nuestro = extras.get(CLAVE)
    return nuestro if isinstance(nuestro, dict) else {}


class SubagentCarryoverMiddleware(AgentMiddleware):
    """Abre el buzon alrededor de cada delegacion y lo vuelca en el resultado.

    Va aparte de ``SubagentEventsMiddleware`` a proposito, aunque los dos
    envuelvan ``task``: aquel solo observa -- lo dice su docstring y se apoya en
    ello para tragarse sus propios fallos -- y esto modifica lo que se
    persiste. Juntarlos convertiria un modulo de telemetria en uno del que
    depende que el estudiante vuelva a encontrar su informe.

    Solo el hook async, por lo mismo que ``ReportGateMiddleware``: la API de
    produccion recorre el grafo con ``astream_events`` y es el unico camino por
    el que un subagente llega a emitir un informe.
    """

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        if request.tool_call.get("name") != SUBAGENT_TOOL_NAME:
            return await handler(request)

        with buzon_abierto() as caja:
            resultado = await handler(request)
            return adjuntar(resultado, caja)


__all__ = [
    "CLAVE",
    "INFORME",
    "SubagentCarryoverMiddleware",
    "adjuntar",
    "anotar",
    "buzon_abierto",
    "lo_recogido",
]
