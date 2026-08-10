"""Tests for the background turn driver (src/api/runs.py).

The point of the module is what happens when the *consumer* leaves, so
most of these drain a few events and then close the generator — which is
exactly what ``with_heartbeat`` does on a client disconnect.
"""

import asyncio

from src.api.runs import TurnosEnVuelo, eventos_del_turno


class GrafoFalso:
    """Emite ``pasos`` eventos con una pausa entre cada uno.

    La pausa importa: sin ella el turno entero cabe en un solo tick y no
    habria forma de irse «a mitad», que es el caso que se prueba.
    """

    def __init__(self, pasos: int = 4, pausa: float = 0.01, revienta_en: int | None = None):
        self.pasos = pasos
        self.pausa = pausa
        self.revienta_en = revienta_en
        self.emitidos: list[int] = []
        self.termino = False
        self.soltado = False
        self.fallos: list[str] = []

    def eventos(self):
        async def gen():
            for i in range(self.pasos):
                await asyncio.sleep(self.pausa)
                if self.revienta_en == i:
                    raise RuntimeError("el grafo reviento")
                self.emitidos.append(i)
                yield f"evento-{i}"
            self.termino = True

        return gen()

    def al_fallar(self, error: Exception) -> str:
        self.fallos.append(str(error))
        return "RUN_ERROR"

    async def al_terminar(self) -> None:
        self.soltado = True


async def _consume_y_se_va(handle, despues_de: int) -> list:
    """Lee unos cuantos eventos y cierra, como un cliente que se marcha."""
    vistos = []
    generador = eventos_del_turno(handle)
    async for evento in generador:
        vistos.append(evento)
        if len(vistos) == despues_de:
            break
    await generador.aclose()
    return vistos


class TestUnTurnoNormal:
    async def test_llegan_todos_los_eventos(self):
        grafo = GrafoFalso(pasos=3)
        handle = TurnosEnVuelo().lanzar(grafo.eventos, grafo.al_fallar, grafo.al_terminar)

        vistos = [evento async for evento in eventos_del_turno(handle)]

        assert vistos == ["evento-0", "evento-1", "evento-2"]

    async def test_la_conversacion_se_suelta_antes_de_cerrar_el_stream(self):
        # Al reves hay un hueco en el que el cliente ya vio el turno
        # terminado y la conversacion sigue arrendada: un mensaje enviado
        # ahi se llevaria un 409 que no le corresponde.
        grafo = GrafoFalso(pasos=2)
        handle = TurnosEnVuelo().lanzar(grafo.eventos, grafo.al_fallar, grafo.al_terminar)

        async for _ in eventos_del_turno(handle):
            pass

        assert grafo.soltado

    async def test_el_registro_queda_limpio(self):
        turnos = TurnosEnVuelo()
        grafo = GrafoFalso(pasos=2)
        handle = turnos.lanzar(grafo.eventos, grafo.al_fallar, grafo.al_terminar)

        async for _ in eventos_del_turno(handle):
            pass
        # El registro se limpia en el done_callback, que corre despues de
        # que la tarea vuelva -- o sea despues de este bucle. Un tick.
        await asyncio.sleep(0)

        assert len(turnos) == 0


class TestElClienteSeVaAMitad:
    """El motivo de todo el modulo."""

    async def test_el_turno_sigue_hasta_el_final(self):
        grafo = GrafoFalso(pasos=6, pausa=0.01)
        handle = TurnosEnVuelo().lanzar(grafo.eventos, grafo.al_fallar, grafo.al_terminar)

        await _consume_y_se_va(handle, despues_de=2)
        await asyncio.sleep(0.2)

        assert grafo.termino
        assert len(grafo.emitidos) == 6

    async def test_el_consumidor_solo_vio_lo_suyo(self):
        grafo = GrafoFalso(pasos=6, pausa=0.01)
        handle = TurnosEnVuelo().lanzar(grafo.eventos, grafo.al_fallar, grafo.al_terminar)

        vistos = await _consume_y_se_va(handle, despues_de=2)

        assert vistos == ["evento-0", "evento-1"]

    async def test_suelta_la_conversacion_al_acabar_y_no_al_irse_el_cliente(self):
        grafo = GrafoFalso(pasos=6, pausa=0.01)
        handle = TurnosEnVuelo().lanzar(grafo.eventos, grafo.al_fallar, grafo.al_terminar)

        await _consume_y_se_va(handle, despues_de=2)
        assert not grafo.soltado

        await asyncio.sleep(0.2)
        assert grafo.soltado


class TestCuandoElGrafoRevienta:
    async def test_el_aviso_cierra_el_stream(self):
        grafo = GrafoFalso(pasos=4, revienta_en=2)
        handle = TurnosEnVuelo().lanzar(grafo.eventos, grafo.al_fallar, grafo.al_terminar)

        vistos = [evento async for evento in eventos_del_turno(handle)]

        assert vistos == ["evento-0", "evento-1", "RUN_ERROR"]

    async def test_suelta_la_conversacion_igual(self):
        grafo = GrafoFalso(pasos=4, revienta_en=1)
        handle = TurnosEnVuelo().lanzar(grafo.eventos, grafo.al_fallar, grafo.al_terminar)

        async for _ in eventos_del_turno(handle):
            pass

        assert grafo.soltado

    async def test_reventar_sin_nadie_mirando_no_tumba_nada(self):
        grafo = GrafoFalso(pasos=6, pausa=0.01, revienta_en=3)
        handle = TurnosEnVuelo().lanzar(grafo.eventos, grafo.al_fallar, grafo.al_terminar)

        await _consume_y_se_va(handle, despues_de=1)
        await asyncio.sleep(0.2)

        assert grafo.fallos == ["el grafo reviento"]
        assert grafo.soltado


class TestApagado:
    async def test_espera_a_un_turno_en_vuelo(self):
        turnos = TurnosEnVuelo()
        grafo = GrafoFalso(pasos=3, pausa=0.01)
        turnos.lanzar(grafo.eventos, grafo.al_fallar, grafo.al_terminar)

        quedaron = await turnos.esperar(5.0)

        assert quedaron == 0
        assert grafo.termino

    async def test_cancela_al_que_no_llega_pero_le_deja_soltar(self):
        # Sin esto la conversacion se quedaria arrendada hasta caducar, y
        # el estudiante que recarga tras un despliegue no podria escribir.
        turnos = TurnosEnVuelo()
        grafo = GrafoFalso(pasos=100, pausa=0.05)
        turnos.lanzar(grafo.eventos, grafo.al_fallar, grafo.al_terminar)

        quedaron = await turnos.esperar(0.12)

        assert quedaron == 1
        assert not grafo.termino
        assert grafo.soltado

    async def test_un_turno_cancelado_no_se_reporta_como_fallo(self):
        # Apagar el proceso no es un fallo del turno, y emitir un RUN_ERROR
        # por cada turno vivo en cada despliegue llenaria el log de ruido.
        turnos = TurnosEnVuelo()
        grafo = GrafoFalso(pasos=100, pausa=0.05)
        turnos.lanzar(grafo.eventos, grafo.al_fallar, grafo.al_terminar)

        await turnos.esperar(0.12)

        assert grafo.fallos == []

    async def test_sin_nada_en_vuelo_no_espera(self):
        assert await TurnosEnVuelo().esperar(5.0) == 0
