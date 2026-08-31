"""El turno termina aunque el estudiante cierre la pestaña.

Hasta ahora el turno lo conducia la propia respuesta HTTP: el endpoint
iteraba el grafo dentro del generador que alimenta el SSE. Cuando el
cliente se iba, ``with_heartbeat`` cerraba ese generador —a proposito, para
no dejar el run colgado— y con el moria el run.

Como LangGraph escribe un checkpoint por superstep, lo que sobrevivia era
lo que ya hubiera terminado. Si te ibas mientras el modelo escribia, el
mensaje del asistente no llegaba a persistirse: volvias a entrar y
encontrabas tu pregunta sin respuesta. No es un caso raro — es cerrar la
pestaña, cambiar de app en el movil, o perder la cobertura un momento.

Aqui el run pasa a una tarea de fondo y los eventos viajan por una cola.
La respuesta HTTP se vuelve un **consumidor** del turno en vez de su
dueño: si se va, el turno sigue hasta el final y el checkpointer termina
de escribirlo. Al recargar, la respuesta esta.

## Lo que esto NO arregla

Sobrevive a que el cliente se vaya; no a que se vaya el proceso. Si ECS
reemplaza la task a mitad de turno —un despliegue, un escalado— el run
muere igual. :func:`esperar_a_los_turnos` le da a esos runs una
oportunidad de terminar durante el apagado, acotada por el ``stopTimeout``
del servicio, que es lo que ECS espera antes del SIGKILL. La ventana pasa
de «cada vez que alguien cierra una pestaña» a «cuando desplegamos».
Cerrarla del todo pedira sacar el run del proceso web.

## Por que una cola y no un `tee`

Porque los dos lados tienen finales distintos. El turno acaba cuando el
grafo acaba; el consumidor acaba cuando el cliente se va, que puede ser
mucho antes. Con una cola eso se expresa solo: el productor deja de
encolar cuando ya no hay nadie —``_Turno.desconectar``— y sigue leyendo
el grafo hasta el final.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# Marca el final de la cola. Un objeto propio y no `None`: `None` es un
# valor que un evento podria tomar, y confundirlos cortaria el stream a
# mitad de turno.
_FIN = object()

# Lo que se le da a un turno cancelado para que corra su `finally` y suelte
# el arrendamiento. Es soltar una clave del store, no un turno: con esto
# sobra, y alargarlo solo retrasaria el apagado.
_MARGEN_DE_CANCELACION_SEGUNDOS = 2.0


class _Turno:
    """Un run en vuelo y la cola por la que lo mira quien lo pidio."""

    __slots__ = ("cola",)

    def __init__(self) -> None:
        # Sin tope: el consumidor normal drena mas rapido de lo que un
        # modelo produce, y un tope aqui frenaria al grafo por culpa de un
        # cliente lento en vez de dejarlo terminar, que es el objetivo.
        # Cuando no hay consumidor no se encola nada en absoluto.
        self.cola: asyncio.Queue[Any] | None = asyncio.Queue()

    def emitir(self, evento: Any) -> None:
        cola = self.cola
        if cola is not None:
            # `put_nowait` y no `await put`: entre el `is not None` de
            # arriba y esta linea no puede colarse una desconexion, porque
            # no hay ningun await de por medio.
            cola.put_nowait(evento)

    def desconectar(self) -> None:
        """El cliente se fue. El turno sigue; sus eventos ya no interesan."""
        self.cola = None


async def _conducir(
    turno: _Turno,
    eventos: Callable[[], AsyncIterator[Any]],
    al_fallar: Callable[[Exception], Any | None],
    al_terminar: Callable[[], Awaitable[None]],
) -> None:
    """Lleva el turno hasta el final, lo mire alguien o no."""
    try:
        async for evento in eventos():
            turno.emitir(evento)
    except asyncio.CancelledError:
        # Apagado del proceso. No es un fallo del turno y no hay nada que
        # contarle a nadie, pero el `finally` de abajo si tiene que correr.
        raise
    except Exception as error:
        aviso = al_fallar(error)
        if aviso is not None:
            turno.emitir(aviso)
    finally:
        # Soltar la conversacion ANTES de cerrar el stream, no despues.
        # Al reves hay un hueco de unos ticks en el que el cliente ya vio
        # el turno terminado y la conversacion sigue arrendada: un mensaje
        # enviado en ese hueco se lleva un 409 que no le corresponde. Lo
        # encontro el guion de comprobacion, no el razonamiento.
        #
        # Escudado porque en el apagado estamos dentro de una cancelacion,
        # y un await desnudo volveria a cancelarse antes de soltar --
        # dejando la conversacion bloqueada hasta que caducara.
        with contextlib.suppress(Exception):
            await asyncio.shield(al_terminar())
        turno.emitir(_FIN)


class TurnosEnVuelo:
    """Los runs que siguen corriendo sin nadie mirando.

    Guardar la referencia no es burocracia: una tarea sin referencias
    fuertes puede recogerla el recolector de basura a mitad, y el turno
    desapareceria sin traza. Ademas es lo que permite esperarlos en el
    apagado.
    """

    def __init__(self) -> None:
        self._tareas: set[asyncio.Task[None]] = set()

    def __len__(self) -> int:
        return len(self._tareas)

    def lanzar(
        self,
        eventos: Callable[[], AsyncIterator[Any]],
        al_fallar: Callable[[Exception], Any | None],
        al_terminar: Callable[[], Awaitable[None]],
    ) -> _Turno:
        turno = _Turno()
        tarea = asyncio.create_task(_conducir(turno, eventos, al_fallar, al_terminar))
        self._tareas.add(tarea)
        tarea.add_done_callback(self._tareas.discard)
        return turno

    async def esperar(self, plazo_segundos: float) -> int:
        """Da a los turnos en vuelo una oportunidad de terminar.

        Devuelve cuantos seguian vivos al agotarse el plazo. Cancelarlos no
        haria falta —el proceso se va— pero se hace para que sus `finally`
        suelten el arrendamiento: si no, esa conversacion se queda
        bloqueada hasta que caduque, y el estudiante que recarga tras un
        despliegue no puede escribir.
        """
        if not self._tareas:
            return 0

        pendientes = set(self._tareas)
        logger.info("Esperando a %d turno(s) en vuelo antes de apagar", len(pendientes))
        _, quedan = await asyncio.wait(pendientes, timeout=max(plazo_segundos, 0.0))

        for tarea in quedan:
            tarea.cancel()
        if quedan:
            logger.warning("%d turno(s) no terminaron a tiempo y se cancelan", len(quedan))
            await asyncio.wait(quedan, timeout=_MARGEN_DE_CANCELACION_SEGUNDOS)

        return len(quedan)


async def eventos_del_turno(turno: _Turno) -> AsyncIterator[Any]:
    """Los eventos del turno, hasta que acabe o hasta que dejen de leerse.

    El `finally` es lo que convierte «el cliente cerro la pestaña» en «deja
    de encolar» en vez de en «mata el turno»: cuando ``with_heartbeat``
    cierra este generador, el productor se entera y sigue solo.
    """
    cola = turno.cola
    if cola is None:
        return

    try:
        while True:
            evento = await cola.get()
            if evento is _FIN:
                return
            yield evento
    finally:
        turno.desconectar()


__all__ = ["TurnosEnVuelo", "eventos_del_turno"]
