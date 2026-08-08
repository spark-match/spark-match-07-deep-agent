"""Tests de los eventos de delegacion en subagentes.

Dos niveles, y los dos hacen falta:

- Los unitarios fijan el contrato del payload (que campos viajan, cuales no)
  y que el evento de cierre se emita tambien cuando el subagente revienta.
- El de integracion es el que de verdad prueba la premisa del diseno: que un
  ``on_custom_event`` disparado desde ``awrap_tool_call`` sale por
  ``astream_events``, que es el unico camino que recorre la API de
  produccion. Sin ese, todo lo demas seria una teoria bien testeada.
"""

import json
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from langchain_core.language_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.tools import BaseTool

from src.agent.factory import create_spark_agent
from src.agent.subagent_events import (
    SUBAGENT_END_EVENT,
    SUBAGENT_START_EVENT,
    UNKNOWN_SUBAGENT,
    SubagentEventsMiddleware,
    _emit,
    _subagent_type,
)


class _FakeToolCallRequest:
    def __init__(self, tool_call: dict[str, Any]) -> None:
        self.tool_call = tool_call


def _task_request(subagent_type: str = "assessment", call_id: str = "call_1"):
    return _FakeToolCallRequest(
        {
            "name": "task",
            "id": call_id,
            "args": {
                "description": "evalua el perfil del estudiante",
                "subagent_type": subagent_type,
            },
        }
    )


class TestSubagentTypeSelector:
    def test_returns_none_for_any_other_tool(self):
        request = _FakeToolCallRequest({"name": "search_careers", "args": {"query": "x"}})
        assert _subagent_type(request) is None

    def test_reads_the_subagent_type_argument(self):
        assert _subagent_type(_task_request("matching")) == "matching"

    def test_missing_subagent_type_falls_back(self):
        request = _FakeToolCallRequest({"name": "task", "id": "c", "args": {"description": "x"}})
        assert _subagent_type(request) == UNKNOWN_SUBAGENT

    def test_unparsed_args_do_not_raise(self):
        """El modelo puede emitir JSON invalido y dejar ``args`` como cadena.
        Un hook que solo observa no tiene derecho a tumbar el turno por eso."""
        request = _FakeToolCallRequest({"name": "task", "id": "c", "args": '{"roto'})
        assert _subagent_type(request) == UNKNOWN_SUBAGENT


class TestSubagentEventsMiddleware:
    def test_other_tools_emit_nothing(self, monkeypatch):
        emitted: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            "src.agent.subagent_events.dispatch_custom_event",
            lambda name, payload: emitted.append((name, payload)),
        )

        result = SubagentEventsMiddleware().wrap_tool_call(
            _FakeToolCallRequest({"name": "web_search", "args": {}}),
            lambda _request: "ok",
        )

        assert result == "ok"
        assert emitted == []

    def test_emits_start_and_end_around_the_delegation(self, monkeypatch):
        emitted: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            "src.agent.subagent_events.dispatch_custom_event",
            lambda name, payload: emitted.append((name, payload)),
        )

        def handler(_request):
            # El start tiene que haber salido ya cuando el subagente empieza:
            # de eso depende que la UI pinte algo mientras se espera.
            assert [name for name, _ in emitted] == [SUBAGENT_START_EVENT]
            return "resultado del subagente"

        SubagentEventsMiddleware().wrap_tool_call(_task_request("planning"), handler)

        assert [name for name, _ in emitted] == [SUBAGENT_START_EVENT, SUBAGENT_END_EVENT]
        start, end = emitted[0][1], emitted[1][1]
        assert start == {"toolCallId": "call_1", "subagent": "planning"}
        assert end["toolCallId"] == "call_1"
        assert end["subagent"] == "planning"
        assert end["ok"] is True
        assert end["durationMs"] >= 0

    def test_the_prompt_of_the_subagent_never_leaves_the_server(self, monkeypatch):
        """``description`` es la instruccion que el coordinador redacta para el
        subagente: un prompt interno. Mismo criterio por el que src/api/app.py
        filtra los eventos RAW."""
        emitted: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            "src.agent.subagent_events.dispatch_custom_event",
            lambda name, payload: emitted.append((name, payload)),
        )

        SubagentEventsMiddleware().wrap_tool_call(_task_request(), lambda _request: "ok")

        for _name, payload in emitted:
            assert "description" not in payload
            assert "evalua el perfil del estudiante" not in str(payload)

    def test_a_failing_subagent_still_closes_the_pair(self, monkeypatch):
        """Sin el evento de cierre, un fallo deja el indicador girando para
        siempre en la pantalla del estudiante."""
        emitted: list[tuple[str, dict[str, Any]]] = []
        monkeypatch.setattr(
            "src.agent.subagent_events.dispatch_custom_event",
            lambda name, payload: emitted.append((name, payload)),
        )

        def exploding_handler(_request):
            raise RuntimeError("el subagente reviento")

        try:
            SubagentEventsMiddleware().wrap_tool_call(_task_request(), exploding_handler)
        except RuntimeError:
            pass
        else:  # pragma: no cover - defensivo
            raise AssertionError("el error del subagente debe seguir propagandose")

        assert [name for name, _ in emitted] == [SUBAGENT_START_EVENT, SUBAGENT_END_EVENT]
        assert emitted[1][1]["ok"] is False

    async def test_async_hook_emits_the_same_pair(self, monkeypatch):
        emitted: list[tuple[str, dict[str, Any]]] = []

        async def fake_adispatch(name, payload):
            emitted.append((name, payload))

        monkeypatch.setattr("src.agent.subagent_events.adispatch_custom_event", fake_adispatch)

        async def handler(_request):
            return "ok"

        result = await SubagentEventsMiddleware().awrap_tool_call(
            _task_request("matching"), handler
        )

        assert result == "ok"
        assert [name for name, _ in emitted] == [SUBAGENT_START_EVENT, SUBAGENT_END_EVENT]
        assert emitted[0][1]["subagent"] == "matching"

    def test_missing_callback_context_is_not_fatal(self):
        """Fuera de ``astream_events`` no hay callback manager y
        ``dispatch_custom_event`` lanza. Esto es telemetria para la UI: que
        falte no puede costarle el turno al estudiante."""
        _emit(SUBAGENT_START_EVENT, {"toolCallId": "x", "subagent": "assessment"})


class _StreamingToolCallFakeChatModel(GenericFakeChatModel):
    """Un fake que sabe *emitir en streaming* una llamada a herramienta.

    El de ``tests/agent/factory.py`` no vale aqui, y la diferencia es justo lo
    que este test prueba. ``GenericFakeChatModel._stream`` reconstruye el
    chunk unicamente a partir de ``content``, asi que las ``tool_calls`` del
    mensaje guionizado **se pierden por el camino**: con ``ainvoke`` la
    herramienta se llama y con ``astream_events`` no, y el grafo termina sin
    pasar por el nodo ``tools``. Como la API de produccion solo recorre el
    grafo con ``astream_events``, un fake que no sepa streamear tool calls no
    puede probar nada de lo que pasa ahi.

    ``bind_tools`` es un no-op por la razon de siempre: el modelo de upstream
    lanza ``NotImplementedError`` y deepagents siempre bindea antes de
    invocar.
    """

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> _StreamingToolCallFakeChatModel:
        return self

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        message = next(self.messages)
        if isinstance(message, str):
            message = AIMessage(content=message)

        chunk = ChatGenerationChunk(
            message=AIMessageChunk(
                content=message.content,
                tool_call_chunks=[
                    {
                        "name": call["name"],
                        "args": json.dumps(call["args"]),
                        "id": call["id"],
                        "index": index,
                        "type": "tool_call_chunk",
                    }
                    for index, call in enumerate(message.tool_calls or [])
                ],
            )
        )
        if run_manager is not None:
            run_manager.on_llm_new_token("", chunk=chunk)
        yield chunk


def _delegate_then_answer() -> Iterator[AIMessage]:
    """Primero delega en el subagente ``matching``, luego contesta siempre.

    Infinito a proposito: por el mismo iterador pasan la llamada del
    coordinador, la del subagente que se acaba de invocar y la del
    coordinador otra vez. Fijar un numero exacto ataria el test al orden
    interno de deepagents, que no es lo que se esta probando aqui.
    """
    yield AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {"description": "rankea carreras", "subagent_type": "matching"},
                "id": "call_subagente",
            }
        ],
    )
    while True:
        yield AIMessage(content="listo")


class TestSubagentEventsReachTheStream:
    """La premisa del diseno, probada contra el grafo real: un custom event
    disparado desde el hook de herramienta sale por ``astream_events``, que es
    el unico camino que recorre ``ag-ui-langgraph`` en produccion."""

    async def test_astream_events_carries_both_events(self):
        # El fast model solo atiende al clasificador del ContentFilter, que
        # corre antes de cada llamada al modelo del coordinador.
        fast = _StreamingToolCallFakeChatModel(
            messages=iter([AIMessage(content='{"safe": true, "reason": "ok"}')] * 8)
        )
        agent = create_spark_agent(
            model=_StreamingToolCallFakeChatModel(messages=_delegate_then_answer()),
            fast_model=fast,
        )

        # El texto no es decorativo: "me gusta" es un marcador narrativo de
        # `src.agent.intent`, y sin el la heuristica clasifica el turno como
        # "clarification" y `IntentRouterMiddleware` lo manda al modelo
        # rapido -- que no lleva guionizada ninguna delegacion, asi que el
        # test pasaria a verde sin haber probado nada.
        custom_events = [
            event
            async for event in agent.astream_events(
                {
                    "messages": [
                        HumanMessage(
                            content=(
                                "me gusta construir cosas con las manos y quiero saber "
                                "que carreras universitarias encajan con mi perfil"
                            )
                        )
                    ]
                },
                config={"configurable": {"thread_id": "subagent-events"}},
            )
            if event["event"] == "on_custom_event"
        ]

        by_name = {event["name"]: event["data"] for event in custom_events}
        assert SUBAGENT_START_EVENT in by_name
        assert SUBAGENT_END_EVENT in by_name
        assert by_name[SUBAGENT_START_EVENT]["subagent"] == "matching"
        assert by_name[SUBAGENT_START_EVENT]["toolCallId"] == "call_subagente"
        assert by_name[SUBAGENT_END_EVENT]["ok"] is True
