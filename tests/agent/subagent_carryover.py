"""Tests del buzon que saca cosas de dentro de un subagente.

El caso que lo justifica esta al final: un ``ContextVar`` con un diccionario
mutable dentro **si** cruza la frontera de una ``asyncio.Task``, y un ``.set()``
desde dentro no. De eso depende que el id del informe llegue al checkpoint, asi
que se prueba en vez de darse por supuesto.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from src.agent.subagent_carryover import (
    CLAVE,
    INFORME,
    SubagentCarryoverMiddleware,
    adjuntar,
    anotar,
    buzon_abierto,
    lo_recogido,
)


class _FakeToolCallRequest:
    def __init__(self, tool_call: dict[str, Any]) -> None:
        self.tool_call = tool_call


def _delegacion(call_id: str = "tc1"):
    return _FakeToolCallRequest(
        {"name": "task", "id": call_id, "args": {"subagent_type": "report"}}
    )


def _resultado(call_id: str = "tc1") -> Command[Any]:
    """Lo que devuelve deepagents: un Command con UN ToolMessage dentro."""
    return Command(update={"messages": [ToolMessage(content="listo", tool_call_id=call_id)]})


class TestBuzon:
    def test_lo_anotado_llega_a_quien_lo_abrio(self):
        with buzon_abierto() as caja:
            anotar(INFORME, "r-9")

        assert caja == {INFORME: "r-9"}

    def test_sin_buzon_abierto_no_pasa_nada(self):
        # Es lo normal en una invocacion directa del grafo y en los tests. No
        # tener donde escribir no puede ser un error.
        anotar(INFORME, "r-9")

    def test_el_buzon_se_cierra_aunque_reviente(self):
        # Sin el reset en `finally`, la siguiente delegacion escribiria encima
        # de lo que dejo un turno que se murio a medias.
        with contextlib.suppress(RuntimeError), buzon_abierto():
            raise RuntimeError("el turno reviento")

        with buzon_abierto() as segunda:
            pass

        assert segunda == {}

    def test_dos_buzones_anidados_no_se_mezclan(self):
        with buzon_abierto() as fuera:
            anotar("a", 1)
            with buzon_abierto() as dentro:
                anotar("b", 2)
            anotar("c", 3)

        assert dentro == {"b": 2}
        assert fuera == {"a": 1, "c": 3}


class TestAdjuntar:
    def test_cuelga_lo_recogido_del_tool_message(self):
        salida = adjuntar(_resultado(), {INFORME: "r-9"})

        assert isinstance(salida, Command)
        assert lo_recogido(salida.update["messages"][0]) == {INFORME: "r-9"}

    def test_sin_nada_recogido_devuelve_el_resultado_intacto(self):
        original = _resultado()

        assert adjuntar(original, {}) is original

    def test_no_pierde_los_demas_campos_del_command(self):
        # `Command` es un dataclass congelado con `goto`, `resume` y `graph`
        # ademas de `update`. Reconstruirlo a mano los dejaria por el camino.
        original = Command(
            update={"messages": [ToolMessage(content="listo", tool_call_id="tc1")]},
            goto="model",
        )

        salida = adjuntar(original, {INFORME: "r-9"})

        assert isinstance(salida, Command)
        assert salida.goto == "model"

    def test_no_pisa_lo_que_ya_hubiera_en_additional_kwargs(self):
        # Ese diccionario es de todos: los proveedores escriben ahi lo suyo.
        mensaje = ToolMessage(
            content="listo", tool_call_id="tc1", additional_kwargs={"del_proveedor": "x"}
        )

        salida = adjuntar(Command(update={"messages": [mensaje]}), {INFORME: "r-9"})

        assert isinstance(salida, Command)
        extras = salida.update["messages"][0].additional_kwargs
        assert extras["del_proveedor"] == "x"
        assert extras[CLAVE] == {INFORME: "r-9"}

    def test_deja_en_paz_los_mensajes_que_no_son_de_herramienta(self):
        original = Command(update={"messages": [AIMessage(content="hola")]})

        salida = adjuntar(original, {INFORME: "r-9"})

        assert isinstance(salida, Command)
        assert salida.update["messages"][0].additional_kwargs == {}

    def test_un_tool_message_suelto_tambien_vale(self):
        salida = adjuntar(ToolMessage(content="listo", tool_call_id="tc1"), {INFORME: "r-9"})

        assert lo_recogido(salida) == {INFORME: "r-9"}

    def test_una_forma_inesperada_no_rompe_la_delegacion(self):
        # Esto depende de una estructura interna de deepagents. Si cambia, el
        # precio tiene que ser quedarnos sin enlace, no tumbar el turno.
        for raro in (Command(update={"messages": "no es una lista"}), Command(update=None)):
            assert adjuntar(raro, {INFORME: "r-9"}) is raro


class TestLoRecogido:
    def test_de_un_mensaje_sin_nada_sale_vacio(self):
        assert lo_recogido(ToolMessage(content="x", tool_call_id="tc1")) == {}

    def test_de_algo_que_no_es_un_mensaje_tambien(self):
        assert lo_recogido("una cadena") == {}
        assert lo_recogido(None) == {}

    def test_una_clave_nuestra_con_forma_rara_no_revienta(self):
        mensaje = ToolMessage(content="x", tool_call_id="tc1", additional_kwargs={CLAVE: "roto"})

        assert lo_recogido(mensaje) == {}


class TestSubagentCarryoverMiddleware:
    async def test_otra_herramienta_pasa_sin_buzon(self):
        # Fuera de una delegacion no hay nada que sacar, y abrir un buzon
        # dejaria que cualquier herramienta se colara en el checkpoint.
        vistos: list[dict[str, Any] | None] = []

        async def handler(_request):
            with buzon_abierto() as caja:
                anotar("x", 1)
                vistos.append(dict(caja))
            return ToolMessage(content="ok", tool_call_id="tc1")

        peticion = _FakeToolCallRequest({"name": "search_careers", "id": "tc1", "args": {}})
        salida = await SubagentCarryoverMiddleware().awrap_tool_call(peticion, handler)

        assert lo_recogido(salida) == {}

    async def test_lo_que_escribe_el_subagente_sale_pegado_al_resultado(self):
        async def handler(_request):
            anotar(INFORME, "r-9")
            return _resultado()

        salida = await SubagentCarryoverMiddleware().awrap_tool_call(_delegacion(), handler)

        assert isinstance(salida, Command)
        assert lo_recogido(salida.update["messages"][0]) == {INFORME: "r-9"}

    async def test_una_delegacion_que_no_emite_nada_no_ensucia_el_mensaje(self):
        async def handler(_request):
            return _resultado()

        salida = await SubagentCarryoverMiddleware().awrap_tool_call(_delegacion(), handler)

        assert isinstance(salida, Command)
        assert salida.update["messages"][0].additional_kwargs == {}

    async def test_el_buzon_no_sobrevive_a_la_delegacion(self):
        async def handler(_request):
            anotar(INFORME, "r-9")
            return _resultado()

        await SubagentCarryoverMiddleware().awrap_tool_call(_delegacion(), handler)

        # Fuera ya no hay buzon: si lo hubiera, la siguiente delegacion
        # heredaria el informe de la anterior.
        anotar(INFORME, "r-10")
        with buzon_abierto() as siguiente:
            pass
        assert siguiente == {}

    async def test_si_la_delegacion_revienta_el_buzon_se_cierra_igual(self):
        async def handler(_request):
            anotar(INFORME, "r-9")
            raise RuntimeError("el subagente se cayo")

        with contextlib.suppress(RuntimeError):
            await SubagentCarryoverMiddleware().awrap_tool_call(_delegacion(), handler)

        with buzon_abierto() as siguiente:
            pass
        assert siguiente == {}

    async def test_lo_escrito_desde_una_task_hija_tambien_llega(self):
        """La premisa del mecanismo, y no es obvia.

        deepagents corre el subagente con `ainvoke()`, que puede acabar en una
        `Task` nueva -- y una `Task` nace con una COPIA del contexto. Un `.set()`
        desde dentro no se veria fuera; mutar el diccionario que ya estaba, si,
        porque la copia apunta al mismo objeto. Si esto deja de cumplirse, el
        id del informe no llega al checkpoint y el boton vuelve a perderse al
        recargar.
        """

        async def muy_adentro():
            anotar(INFORME, "r-9")

        async def handler(_request):
            await asyncio.create_task(muy_adentro())
            return _resultado()

        salida = await SubagentCarryoverMiddleware().awrap_tool_call(_delegacion(), handler)

        assert isinstance(salida, Command)
        assert lo_recogido(salida.update["messages"][0]) == {INFORME: "r-9"}
