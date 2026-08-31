"""Unit tests for conversation rehydration (src/threads/history.py).

Uses a real compiled LangGraph with a real checkpointer rather than a
stub. That matters here: the first version of this module read the
checkpointer directly and looked correct, but ``aget_tuple`` returns only
the channels the *last* step touched, so on a finished turn it came back
with ``skills_metadata`` and ``memory_contents`` and no messages at all. A
fake ``aget_state`` would have happily agreed with the broken code.
"""

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph

from src.threads.activity import CLAVE, INFORME, PASOS
from src.threads.history import _ASUNTO_POR_HERRAMIENTA, load_thread_messages


@pytest.fixture
def graph():
    """A minimal graph whose only job is to hold a messages channel."""

    def passthrough(state: MessagesState) -> dict:
        return {}

    builder = StateGraph(MessagesState)
    builder.add_node("passthrough", passthrough)
    builder.add_edge(START, "passthrough")
    return builder.compile(checkpointer=InMemorySaver())


async def _seed(graph, thread_id: str, messages: list) -> None:
    await graph.ainvoke({"messages": messages}, {"configurable": {"thread_id": thread_id}})


class TestLoadThreadMessages:
    async def test_returns_the_conversation_in_order(self, graph):
        await _seed(
            graph, "t_1", [HumanMessage(content="hola"), AIMessage(content="¿en qué ayudo?")]
        )

        messages = await load_thread_messages(graph, "t_1")

        assert [(m["role"], m["content"]) for m in messages] == [
            ("user", "hola"),
            ("assistant", "¿en qué ayudo?"),
        ]

    async def test_never_returns_system_messages(self, graph):
        """ProfileHydrationMiddleware injects the student's extracted
        vocational profile as a SystemMessage on every turn, and those are
        persisted in the same channel. Returning state verbatim would ship
        that block to the browser as part of the chat history."""
        await _seed(
            graph,
            "t_1",
            [
                SystemMessage(content="## Perfil vocacional ya conocido\nRIASEC: I=90"),
                HumanMessage(content="hola"),
                AIMessage(content="hola"),
            ],
        )

        messages = await load_thread_messages(graph, "t_1")

        assert all(m["role"] != "system" for m in messages)
        assert not any("Perfil vocacional" in m["content"] for m in messages)

    async def test_drops_tool_messages(self, graph):
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="busca carreras"),
                ToolMessage(content="{...resultados crudos...}", tool_call_id="tc1"),
                AIMessage(content="encontré esto"),
            ],
        )

        messages = await load_thread_messages(graph, "t_1")

        assert [m["role"] for m in messages] == ["user", "assistant"]

    async def test_el_payload_de_una_herramienta_nunca_sale(self, graph):
        """Es el motivo original del filtro y sigue en pie.

        Los `ToolMessage` llevan resultados de busqueda enteros. Que ahora
        se rehidrate la ACTIVIDAD no cambia eso: sale el resumen, no lo que
        devolvio la herramienta.
        """
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="becas"),
                AIMessage(
                    content="",
                    tool_calls=[{"id": "tc1", "name": "web_search", "args": {"query": "Beca 18"}}],
                ),
                ToolMessage(content="RESULTADO CRUDO SECRETO", tool_call_id="tc1"),
                AIMessage(content="mira esto"),
            ],
        )

        messages = await load_thread_messages(graph, "t_1")

        assert "RESULTADO CRUDO SECRETO" not in str(messages)

    async def test_drops_tool_call_only_assistant_turns(self, graph):
        """Real to the graph, invisible to the conversation."""
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="hola"),
                AIMessage(content=""),
                AIMessage(content="respuesta real"),
            ],
        )

        messages = await load_thread_messages(graph, "t_1")

        assert [m["content"] for m in messages] == ["hola", "respuesta real"]

    async def test_flattens_block_style_content(self, graph):
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="hola"),
                AIMessage(
                    content=[
                        {"type": "thinking", "thinking": "razonamiento interno"},
                        {"type": "text", "text": "hola de vuelta"},
                    ]
                ),
            ],
        )

        messages = await load_thread_messages(graph, "t_1")

        assert messages[-1]["content"] == "hola de vuelta"
        assert not any("razonamiento interno" in m["content"] for m in messages)

    async def test_threads_are_independent(self, graph):
        await _seed(graph, "t_a", [HumanMessage(content="conversación A")])
        await _seed(graph, "t_b", [HumanMessage(content="conversación B")])

        messages = await load_thread_messages(graph, "t_a")

        assert [m["content"] for m in messages] == ["conversación A"]

    async def test_unknown_thread_is_empty_not_an_error(self, graph):
        """'No messages yet' and 'never existed' are the same thing to a
        caller reopening a conversation — and distinguishing them would
        leak whether a derived id exists."""
        assert await load_thread_messages(graph, "t_nope") == []

    async def test_none_graph_returns_empty(self):
        assert await load_thread_messages(None, "t_1") == []


def _llamada(id_: str, nombre: str, **args) -> dict:
    return {"id": id_, "name": nombre, "args": args}


class TestActividadRehidratada:
    """Al recargar, los chips tienen que volver.

    Sin esto la respuesta queda sin procedencia: el estudiante ve unas
    cifras y ninguna pista de si salieron del catalogo del MINEDU, de una
    busqueda en internet o de un especialista -- que es justo lo que decide
    cuanto fiarse de ellas.
    """

    async def test_la_actividad_se_cuelga_del_mensaje_que_cierra_el_turno(self, graph):
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="qué carreras hay"),
                AIMessage(content="", tool_calls=[_llamada("tc1", "search_careers", query="ing")]),
                ToolMessage(content="[...]", tool_call_id="tc1"),
                AIMessage(content="encontré estas"),
            ],
        )

        messages = await load_thread_messages(graph, "t_1")

        assert "activity" not in messages[0]
        assert [a["tool"] for a in messages[1]["activity"]] == ["search_careers"]

    async def test_se_ve_que_se_busco_en_cada_consulta(self, graph):
        """Es lo que hace util desplegar «6 consultas al catálogo»."""
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="opciones"),
                AIMessage(
                    content="",
                    tool_calls=[
                        _llamada("tc1", "search_careers", query="ingeniería"),
                        _llamada("tc2", "search_careers", query="gestión"),
                    ],
                ),
                ToolMessage(content="[...]", tool_call_id="tc1"),
                ToolMessage(content="[...]", tool_call_id="tc2"),
                AIMessage(content="esto encontré"),
            ],
        )

        actividad = (await load_thread_messages(graph, "t_1"))[1]["activity"]

        assert [a["subject"] for a in actividad] == ["ingeniería", "gestión"]

    async def test_dos_especialistas_salen_los_dos(self, graph):
        # El caso que motivo desplegar el resumen: si se llamo a dos, se
        # tienen que poder ver los dos y cuales fueron.
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="ayúdame"),
                AIMessage(
                    content="",
                    tool_calls=[
                        _llamada("tc1", "task", subagent_type="matching", description="prompt"),
                        _llamada("tc2", "task", subagent_type="planning", description="prompt"),
                    ],
                ),
                ToolMessage(content="[...]", tool_call_id="tc1"),
                ToolMessage(content="[...]", tool_call_id="tc2"),
                AIMessage(content="listo"),
            ],
        )

        actividad = (await load_thread_messages(graph, "t_1"))[1]["activity"]

        assert [a["subagent"] for a in actividad] == ["matching", "planning"]

    async def test_el_prompt_interno_del_especialista_no_sale(self, graph):
        """`description` es la instruccion que el coordinador le redacta al
        subagente: un prompt interno. Mismo criterio que subagent_events."""
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="ayúdame"),
                AIMessage(
                    content="",
                    tool_calls=[
                        _llamada(
                            "tc1",
                            "task",
                            subagent_type="matching",
                            description="INSTRUCCION INTERNA QUE NO DEBE SALIR",
                        )
                    ],
                ),
                ToolMessage(content="[...]", tool_call_id="tc1"),
                AIMessage(content="listo"),
            ],
        )

        messages = await load_thread_messages(graph, "t_1")

        assert "INSTRUCCION INTERNA" not in str(messages)

    async def test_una_herramienta_fuera_de_la_lista_blanca_no_trae_asunto(self, graph):
        # La lista es blanca a proposito: una herramienta nueva no filtra
        # sus argumentos por el mero hecho de existir.
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="hola"),
                AIMessage(
                    content="",
                    tool_calls=[_llamada("tc1", "manage_prefs", content="dato personal")],
                ),
                ToolMessage(content="ok", tool_call_id="tc1"),
                AIMessage(content="anotado"),
            ],
        )

        actividad = (await load_thread_messages(graph, "t_1"))[1]["activity"]

        assert actividad[0]["tool"] == "manage_prefs"
        assert "subject" not in actividad[0]
        assert "dato personal" not in str(actividad)

    async def test_un_asunto_larguisimo_se_recorta(self, graph):
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="hola"),
                AIMessage(content="", tool_calls=[_llamada("tc1", "web_search", query="x" * 300)]),
                ToolMessage(content="[...]", tool_call_id="tc1"),
                AIMessage(content="ya"),
            ],
        )

        actividad = (await load_thread_messages(graph, "t_1"))[1]["activity"]

        assert len(actividad[0]["subject"]) <= 80

    async def test_un_fallo_de_herramienta_se_marca(self, graph):
        # Un turno donde la busqueda fallo y otro donde no se distinguen
        # solo por esto.
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="busca"),
                AIMessage(content="", tool_calls=[_llamada("tc1", "web_search", query="beca")]),
                ToolMessage(content="boom", tool_call_id="tc1", status="error"),
                AIMessage(content="no pude"),
            ],
        )

        actividad = (await load_thread_messages(graph, "t_1"))[1]["activity"]

        assert actividad[0]["ok"] is False

    async def test_una_herramienta_que_fue_bien_se_marca(self, graph):
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="busca"),
                AIMessage(content="", tool_calls=[_llamada("tc1", "web_search", query="beca")]),
                ToolMessage(content="[...]", tool_call_id="tc1"),
                AIMessage(content="mira"),
            ],
        )

        actividad = (await load_thread_messages(graph, "t_1"))[1]["activity"]

        assert actividad[0]["ok"] is True

    async def test_los_chips_van_sobre_la_respuesta_y_no_sobre_el_preambulo(self, graph):
        """Anthropic emite a veces texto y llamadas en el mismo mensaje.

        Ese texto es preambulo ("déjame revisar el catálogo") y el turno
        sigue. Colgarle ahi los chips los pondria sobre la frase
        equivocada.
        """
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="qué hay"),
                AIMessage(
                    content="Déjame revisar el catálogo.",
                    tool_calls=[_llamada("tc1", "search_careers", query="ing")],
                ),
                ToolMessage(content="[...]", tool_call_id="tc1"),
                AIMessage(content="Encontré estas tres."),
            ],
        )

        messages = await load_thread_messages(graph, "t_1")

        preambulo = next(m for m in messages if m["content"].startswith("Déjame"))
        respuesta = next(m for m in messages if m["content"].startswith("Encontré"))
        assert "activity" not in preambulo
        assert len(respuesta["activity"]) == 1


class TestElInformeEmitido:
    """El enlace al informe tiene que seguir ahi al recargar.

    El id no esta en ningun mensaje del hilo: `publish_orientation_report`
    corre dentro del subagente de report y de ahi no vuelve mas que el texto
    final. Se lo cuelga `SubagentCarryoverMiddleware` al `ToolMessage` de la
    delegacion, y esto comprueba que de ahi sale al historial.
    """

    @staticmethod
    def _delegacion_con_informe(report_id: str) -> ToolMessage:
        return ToolMessage(
            content="informe emitido",
            tool_call_id="tc1",
            additional_kwargs={CLAVE: {INFORME: report_id}},
        )

    async def test_sale_en_el_mensaje_que_cierra_el_turno(self, graph):
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="genérame mi reporte"),
                AIMessage(
                    content="",
                    tool_calls=[_llamada("tc1", "task", subagent_type="report", description="x")],
                ),
                self._delegacion_con_informe("r-9"),
                AIMessage(content="ya lo tienes"),
            ],
        )

        pregunta, respuesta = await load_thread_messages(graph, "t_1")

        assert "report_id" not in pregunta
        assert respuesta["report_id"] == "r-9"

    async def test_no_se_pega_al_preambulo(self, graph):
        # Ese «dame un momento» va ANTES de la delegacion, asi que el enlace
        # quedaria por encima de la frase que anuncia que el informe esta listo.
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="genérame mi reporte"),
                AIMessage(
                    content="dame un momento",
                    tool_calls=[_llamada("tc1", "task", subagent_type="report", description="x")],
                ),
                self._delegacion_con_informe("r-9"),
                AIMessage(content="ya lo tienes"),
            ],
        )

        preambulo, respuesta = (await load_thread_messages(graph, "t_1"))[1:]

        assert "report_id" not in preambulo
        assert respuesta["report_id"] == "r-9"

    async def test_un_turno_sin_informe_no_lleva_la_clave(self, graph):
        # El frontend pregunta por `msg.reportId`; una cadena vacia pintaria un
        # boton hacia ninguna parte.
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="qué carreras hay"),
                AIMessage(content="", tool_calls=[_llamada("tc1", "search_careers", query="ing")]),
                ToolMessage(content="[...]", tool_call_id="tc1"),
                AIMessage(content="estas"),
            ],
        )

        assert all("report_id" not in m for m in await load_thread_messages(graph, "t_1"))

    async def test_el_informe_de_un_turno_no_se_cuela_en_el_siguiente(self, graph):
        # El buzon se vacia al empezar cada turno, igual que los chips. Sin eso
        # el boton reaparecia en respuestas que no habian emitido nada.
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="genérame mi reporte"),
                AIMessage(
                    content="",
                    tool_calls=[_llamada("tc1", "task", subagent_type="report", description="x")],
                ),
                self._delegacion_con_informe("r-9"),
                AIMessage(content="ya lo tienes"),
                HumanMessage(content="gracias"),
                AIMessage(content="a ti"),
            ],
        )

        messages = await load_thread_messages(graph, "t_1")

        assert messages[1]["report_id"] == "r-9"
        assert "report_id" not in messages[3]

    async def test_un_mensaje_de_antes_de_esto_no_revienta(self, graph):
        # El checkpointer guarda hilos escritos por builds anteriores, sin
        # `additional_kwargs` nuestro. Rehidratarlos tiene que seguir yendo.
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="genérame mi reporte"),
                AIMessage(
                    content="",
                    tool_calls=[_llamada("tc1", "task", subagent_type="report", description="x")],
                ),
                ToolMessage(content="informe emitido", tool_call_id="tc1"),
                AIMessage(content="ya lo tienes"),
            ],
        )

        messages = await load_thread_messages(graph, "t_1")

        assert "report_id" not in messages[1]
        assert messages[1]["activity"][0]["subagent"] == "report"


class TestLosPasosDelSubagente:
    """En vivo el chip decia 8 pasos; al recargar, 1.

    Los siete de dentro ocurren en el grafo del subagente y no llegan al
    checkpoint del padre. Ahora viajan pegados al `ToolMessage` de la
    delegacion, y esto comprueba que salen como hermanos suyos -- que es como
    el estudiante los vio la primera vez.
    """

    @staticmethod
    def _delegacion_con_pasos(*pasos: dict) -> ToolMessage:
        return ToolMessage(
            content="listo",
            tool_call_id="tc1",
            additional_kwargs={CLAVE: {PASOS: list(pasos)}},
        )

    @staticmethod
    def _paso(tool: str, id_: str, **extra) -> dict:
        return {"id": id_, "tool": tool, "ok": True, **extra}

    async def test_los_pasos_salen_detras_de_su_delegacion(self, graph):
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="qué carreras me convienen"),
                AIMessage(
                    content="",
                    tool_calls=[_llamada("tc1", "task", subagent_type="matching", description="x")],
                ),
                self._delegacion_con_pasos(
                    self._paso("recommend_programs", "in1", subject="IRC"),
                    self._paso("search_careers", "in2", subject="ingeniería"),
                ),
                AIMessage(content="estas"),
            ],
        )

        actividad = (await load_thread_messages(graph, "t_1"))[1]["activity"]

        assert [a.get("subagent") or a["tool"] for a in actividad] == [
            "matching",
            "recommend_programs",
            "search_careers",
        ]

    async def test_la_clave_interna_no_sale_hacia_el_navegador(self, graph):
        # `steps` es como viajan por dentro; hacia fuera son chips hermanos.
        # Dejarla puesta le daria al frontend dos formas de leer lo mismo.
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="ayúdame"),
                AIMessage(
                    content="",
                    tool_calls=[_llamada("tc1", "task", subagent_type="matching", description="x")],
                ),
                self._delegacion_con_pasos(self._paso("search_careers", "in1")),
                AIMessage(content="listo"),
            ],
        )

        actividad = (await load_thread_messages(graph, "t_1"))[1]["activity"]

        assert all("steps" not in a for a in actividad)

    async def test_cada_delegacion_se_lleva_los_suyos(self, graph):
        # Con dos especialistas en el mismo turno, los pasos de uno detras de
        # una lista plana quedarian colgando del otro.
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="ayúdame"),
                AIMessage(
                    content="",
                    tool_calls=[
                        _llamada("tc1", "task", subagent_type="matching", description="x"),
                        _llamada("tc2", "task", subagent_type="planning", description="x"),
                    ],
                ),
                ToolMessage(
                    content="listo",
                    tool_call_id="tc1",
                    additional_kwargs={CLAVE: {PASOS: [self._paso("search_careers", "in1")]}},
                ),
                ToolMessage(
                    content="listo",
                    tool_call_id="tc2",
                    additional_kwargs={CLAVE: {PASOS: [self._paso("web_search", "in2")]}},
                ),
                AIMessage(content="listo"),
            ],
        )

        actividad = (await load_thread_messages(graph, "t_1"))[1]["activity"]

        assert [a.get("subagent") or a["tool"] for a in actividad] == [
            "matching",
            "search_careers",
            "planning",
            "web_search",
        ]

    async def test_una_delegacion_sin_pasos_sale_como_antes(self, graph):
        # Los hilos que ya estaban guardados no llevan nada nuestro.
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="ayúdame"),
                AIMessage(
                    content="",
                    tool_calls=[_llamada("tc1", "task", subagent_type="matching", description="x")],
                ),
                ToolMessage(content="listo", tool_call_id="tc1"),
                AIMessage(content="listo"),
            ],
        )

        actividad = (await load_thread_messages(graph, "t_1"))[1]["activity"]

        assert len(actividad) == 1
        assert actividad[0]["subagent"] == "matching"


class TestTurnosCortados:
    """Cerrar la pestaña a mitad deja llamadas sin respuesta detras."""

    async def test_un_turno_sin_cerrar_no_deja_chips_sueltos(self, graph):
        # Un chip solo, sin mensaje debajo, parece un fallo de la app mas
        # que un turno interrumpido.
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="busca"),
                AIMessage(content="", tool_calls=[_llamada("tc1", "search_careers", query="ing")]),
                ToolMessage(content="[...]", tool_call_id="tc1"),
                HumanMessage(content="mejor otra cosa"),
                AIMessage(content="claro"),
            ],
        )

        messages = await load_thread_messages(graph, "t_1")

        assert all("activity" not in m for m in messages)

    async def test_un_turno_cortado_al_final_tampoco(self, graph):
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="busca"),
                AIMessage(content="", tool_calls=[_llamada("tc1", "search_careers", query="ing")]),
                ToolMessage(content="[...]", tool_call_id="tc1"),
            ],
        )

        messages = await load_thread_messages(graph, "t_1")

        assert all("activity" not in m for m in messages)


class TestLaListaBlancaApuntaAArgumentosReales:
    """El mapa de asuntos es una copia a mano de firmas que estan en otro sitio.

    Y ya se habia desincronizado: la entrada de `search_programs` pedia un
    `query` que esa herramienta no tiene ni ha tenido nunca --busca por
    `career`--, asi que sus chips salian sin asunto. El fallo es silencioso
    por construccion, porque un argumento ausente y un argumento vacio se
    leen igual desde `args.get(...)`, y por eso hace falta una prueba y no
    releer el mapa con cuidado.
    """

    @staticmethod
    def _herramientas() -> dict[str, Any]:
        # Importadas aqui y no arriba para que el resto del fichero --que va
        # de rehidratar-- no dependa del catalogo ni de la busqueda web.
        from src.tools.catalog.tool import search_careers
        from src.tools.programs.tool import search_programs
        from src.tools.recommendation.tool import recommend_programs
        from src.tools.web_search.tool import web_search

        # Por `tool.name` y no por una clave escrita a mano: lo que viaja en
        # la tool call es ese nombre, asi que es el mismo con el que el mapa
        # tiene que casar.
        catalogo = (search_careers, search_programs, recommend_programs, web_search)
        return {t.name: t for t in catalogo}

    def test_cada_entrada_nombra_una_herramienta_que_existe(self):
        herramientas = self._herramientas()

        assert set(_ASUNTO_POR_HERRAMIENTA) <= set(herramientas)

    def test_cada_entrada_nombra_un_argumento_que_existe(self):
        herramientas = self._herramientas()

        for nombre, campo in _ASUNTO_POR_HERRAMIENTA.items():
            argumentos = herramientas[nombre].args
            assert campo in argumentos, f"{nombre} no tiene un argumento {campo!r}"

    async def test_de_search_programs_se_ve_la_carrera_que_busco(self, graph):
        await _seed(
            graph,
            "t_1",
            [
                HumanMessage(content="dónde estudio eso"),
                AIMessage(
                    content="",
                    tool_calls=[
                        _llamada("tc1", "search_programs", career="Ingeniería Industrial", limit=5)
                    ],
                ),
                ToolMessage(content="[...]", tool_call_id="tc1"),
                AIMessage(content="en estas universidades"),
            ],
        )

        actividad = (await load_thread_messages(graph, "t_1"))[1]["activity"]

        assert actividad[0]["subject"] == "Ingeniería Industrial"
