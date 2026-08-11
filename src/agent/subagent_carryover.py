"""Lo que pasa dentro de un subagente y el padre tiene que conservar.

Un subagente de deepagents corre entero dentro de la herramienta ``task``, con
su propio grafo, y de todo lo que hace **solo vuelve su texto final**:
``_return_command_with_state_update`` (``deepagents/middleware/subagents.py``)
arma un ``Command`` cuyo ``messages`` es un unico ``ToolMessage`` con esa
cadena, y descarta los mensajes internos. Lo que el subagente llamo, y lo que
le devolvieron, no llega nunca al checkpoint del grafo padre.

Casi siempre da igual -- es justo el aislamiento que se busca al delegar --
pero hay dos cosas que se producen ahi dentro y el estudiante necesita al
recargar la pagina. Las dos se veian en vivo, porque ``astream_events`` recorre
el grafo entero, y las dos desaparecian al refrescar, porque el historial se
reconstruye solo del padre:

- **El id del informe.** ``publish_orientation_report`` vive en el subagente de
  report, asi que el enlace a su informe desaparecia aunque el informe
  existiera. El evento en vivo ``spark.report.ready`` lo cuenta mientras pasa;
  el historial no tenia de donde sacarlo.
- **Los pasos que dio.** El chip decia "1 paso" donde en vivo se habian visto
  ocho: los siete de dentro son llamadas del grafo del subagente.

## El buzon

El padre abre un buzon antes de delegar, lo de dentro escribe en el, y el padre
lo vacia sobre el ``ToolMessage`` que si se persiste. Dos middlewares y en
sitios distintos: :class:`SubagentCarryoverMiddleware` en el coordinador -- abre
y vuelca -- y :class:`SubagentStepsMiddleware` **dentro de cada subagente**,
porque la lista de middleware del coordinador no baja a ellos.

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

**Lo que se anota va filtrado, no crudo.** Los pasos pasan por la lista blanca
de ``src/threads/activity.py`` **al escribir**, y no al leer como hace el
historial: esto se persiste en el checkpoint, asi que guardar la llamada entera
y filtrarla despues dejaria los argumentos sin autorizar ya guardados.

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

# Las claves y el resumidor viven en `src/threads/activity.py`, que no importa
# nada del proyecto: es lo unico que deja que el agente escriba y el historial
# lea sin que los dos modulos se importen entre si. El ciclo lo cerraba
# `src/threads/__init__.py`, y el sintoma era el arranque muriendo con un
# `partially initialized module`.
from src.threads.activity import CLAVE, INFORME, PASOS, lo_recogido, resumen_publicable

logger = logging.getLogger(__name__)

#: Cuantos pasos se guardan como mucho de una sola delegacion.
#:
#: Un subagente normal hace tres o cuatro llamadas y esto no le afecta. Lo que
#: acota es el caso patologico: un bucle que se dispara hasta chocar con el
#: limite de recursion escribiria cientos de entradas en el checkpoint, y ese
#: checkpoint se relee entero en cada turno posterior de la conversacion. Lo
#: que se descarta se registra -- un recorte silencioso se leeria luego como
#: "el subagente solo hizo treinta cosas".
_MAX_PASOS = 30

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


def anotar_paso(paso: dict[str, Any]) -> None:
    """Añade una llamada a la lista de pasos de esta delegacion.

    Como :func:`anotar`, no hace nada sin buzon abierto: un subagente invocado
    por su cuenta en un test no tiene padre a quien contarle nada.
    """
    caja = _BUZON.get()
    if caja is None:
        return

    pasos = caja.setdefault(PASOS, [])
    if len(pasos) >= _MAX_PASOS:
        # Una vez por paso descartado y no una al final: si esto aparece en el
        # log, lo que hay detras es un subagente en bucle, y eso interesa mas
        # que el recorte en si.
        logger.warning(
            "El subagente paso de %d llamadas; la de %r no se guarda en el historial",
            _MAX_PASOS,
            paso.get("tool"),
        )
        return
    pasos.append(paso)


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


class SubagentStepsMiddleware(AgentMiddleware):
    """Anota en el buzon cada llamada que el subagente hace por dentro.

    **Se cablea en el subagente, no en el coordinador**, y por eso existe
    aparte de :class:`SubagentCarryoverMiddleware`: la lista
    ``middleware=[...]`` de ``create_deep_agent`` no baja a los subagentes --
    deepagents monta cada uno con la de SU propia spec
    (``deepagents/middleware/subagents.py``) -- asi que la unica forma de ver
    lo que pasa ahi dentro es estar ahi dentro. Va en cada entrada de
    ``src/agent/subagents/``.

    Lo que se anota es el resumen filtrado por la lista blanca, no la llamada:
    ver ``src/threads/activity.py``. Esto se **escribe** en el checkpoint, asi
    que filtrar despues llegaria tarde.

    Los dos hooks, sincrono y async. A diferencia de los del coordinador, este
    corre dentro de un subagente que puede invocarse de las dos formas, y un
    middleware que solo implementa uno hace que LangChain lance
    ``NotImplementedError`` en el otro modo -- para TODAS las llamadas, no solo
    para esta.
    """

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        resumen = resumen_publicable(request.tool_call)
        resultado = handler(request)
        _anotar_con_resultado(resumen, resultado)
        return resultado

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        resumen = resumen_publicable(request.tool_call)
        resultado = await handler(request)
        _anotar_con_resultado(resumen, resultado)
        return resultado


def _anotar_con_resultado(resumen: dict[str, Any] | None, resultado: Any) -> None:
    """Anota el paso una vez que se sabe como acabo.

    Si la herramienta LANZA en vez de devolver un ``ToolMessage`` de error,
    esto no llega a correr y el paso no se anota. No hace falta protegerlo: una
    excepcion ahi tumba el turno, y un turno que no termina no escribe
    checkpoint -- no hay historial que pudiera quedar incompleto.

    Un resultado que no es un ``ToolMessage`` deja el ``ok`` en ``None``, que es
    "nunca se supo" y no "fallo". El frontend ya distingue las dos cosas.
    """
    if resumen is None:
        return
    if isinstance(resultado, ToolMessage):
        resumen["ok"] = resultado.status != "error"
    anotar_paso(resumen)


__all__ = [
    "CLAVE",
    "INFORME",
    "PASOS",
    "SubagentCarryoverMiddleware",
    "SubagentStepsMiddleware",
    "adjuntar",
    "anotar",
    "anotar_paso",
    "buzon_abierto",
    "lo_recogido",
]
