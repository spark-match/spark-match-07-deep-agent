"""Tests for the agent runtime middlewares."""

import logging

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolCall, ToolMessage

from src.agent.middleware import (
    ASSESSMENT_TOOL_NAME,
    AssessmentOnceMiddleware,
    MaxTurnsMiddleware,
)


def _fake_runtime() -> object:
    """Minimal runtime stub for middleware hooks that take a runtime arg."""
    return object()


class TestMaxTurnsMiddleware:
    """MaxTurnsMiddleware.after_model caps the agent at settings.max_turns."""

    def test_does_nothing_under_cap(self, monkeypatch):
        monkeypatch.setenv("SPARK_MAX_TURNS", "50")
        from src.config import get_settings

        get_settings.cache_clear()

        mw = MaxTurnsMiddleware()
        state = {
            "messages": [
                HumanMessage(content="hi"),
                AIMessage(content="hello"),
            ]
        }
        result = mw.after_model(state, _fake_runtime())
        assert result is None  # No state update

    def test_triggers_at_cap(self, monkeypatch):
        monkeypatch.setenv("SPARK_MAX_TURNS", "2")
        from src.config import get_settings

        get_settings.cache_clear()

        mw = MaxTurnsMiddleware()
        state = {
            "messages": [
                HumanMessage(content="hi"),
                AIMessage(content="reply 1"),
                AIMessage(content="reply 2"),
            ]
        }
        result = mw.after_model(state, _fake_runtime())
        assert result is not None
        # Should append a final AIMessage and route to "end" via jump_to —
        # the real LangChain 1.x contract. A "goto" key here would be
        # silently dropped by LangGraph and the graph would keep running.
        assert "messages" in result
        new_msg = result["messages"][0]
        assert isinstance(new_msg, AIMessage)
        assert "límite" in new_msg.content or "limite" in new_msg.content.lower()
        assert "2" in new_msg.content  # cap value echoed
        assert result.get("jump_to") == "end"

    def test_counts_only_ai_messages(self, monkeypatch):
        """Human and tool messages should not count toward the cap."""
        monkeypatch.setenv("SPARK_MAX_TURNS", "2")
        from src.config import get_settings

        get_settings.cache_clear()

        mw = MaxTurnsMiddleware()
        state = {
            "messages": [
                HumanMessage(content="user 1"),
                AIMessage(content="ai 1"),
                HumanMessage(content="user 2"),
                ToolMessage(content="tool 1", tool_call_id="c1"),
                HumanMessage(content="user 3"),
                # Only 1 AI message so far — under cap
            ]
        }
        result = mw.after_model(state, _fake_runtime())
        assert result is None  # Under cap, even with 5 total messages

    def test_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("SPARK_MAX_TURNS", "1")
        from src.config import get_settings

        get_settings.cache_clear()

        mw = MaxTurnsMiddleware()
        state = {
            "messages": [
                AIMessage(content="only one"),
            ]
        }
        with caplog.at_level(logging.WARNING):
            mw.after_model(state, _fake_runtime())
        assert any("Max turns reached" in r.message for r in caplog.records)


class TestAssessmentOnceMiddleware:
    """AssessmentOnceMiddleware rejects repeat calls to the assessment tool."""

    def test_allows_first_call(self):
        mw = AssessmentOnceMiddleware()

        # Fake request with a non-matching tool
        class FakeRequest:
            tool_call = {"name": "search_careers", "id": "c1"}
            state = {"messages": []}

        captured = {}

        def fake_handler(req):
            captured["called"] = True
            return "ok"

        mw.wrap_tool_call(FakeRequest(), fake_handler)
        assert captured["called"] is True

    def test_bypasses_non_assessment_tools(self):
        mw = AssessmentOnceMiddleware()

        class FakeRequest:
            tool_call = {"name": "web_search", "id": "c1"}
            state = {"messages": [AIMessage(content="ok", tool_calls=[])]}

        called = [False]

        def fake_handler(req):
            called[0] = True
            return "ok"

        result = mw.wrap_tool_call(FakeRequest(), fake_handler)
        assert called[0] is True
        assert result == "ok"

    def test_rejects_second_assessment_call(self):
        mw = AssessmentOnceMiddleware()

        prior_call = ToolCall(name=ASSESSMENT_TOOL_NAME, args={}, id="c1")
        prior_ai = AIMessage(content="calling assessment", tool_calls=[prior_call])
        # La respuesta va incluida a proposito: lo que bloquea un reintento es
        # una evaluacion HECHA, no un intento suelto. Sin ella este historial
        # es el de un turno que se murio a medias, y ahi hay que dejar pasar.
        prior_ok = ToolMessage(content='{"riasec_code": "RIC"}', tool_call_id="c1")

        class FakeRequest:
            tool_call = {"name": ASSESSMENT_TOOL_NAME, "id": "c2"}
            state = {"messages": [prior_ai, prior_ok]}

        called = [False]

        def fake_handler(req):
            called[0] = True
            return "should not happen"

        result = mw.wrap_tool_call(FakeRequest(), fake_handler)
        assert called[0] is False
        # Should return a ToolMessage with an error
        assert isinstance(result, ToolMessage)
        assert "already called" in result.content.lower()
        assert result.tool_call_id == "c2"

    def test_allows_when_no_prior_calls(self):
        mw = AssessmentOnceMiddleware()

        class FakeRequest:
            tool_call = {"name": ASSESSMENT_TOOL_NAME, "id": "c1"}
            state = {"messages": [HumanMessage(content="hi")]}

        called = [False]

        def fake_handler(req):
            called[0] = True
            return "ok"

        result = mw.wrap_tool_call(FakeRequest(), fake_handler)
        assert called[0] is True
        assert result == "ok"


class TestElGuardNoSeCuentaASiMismo:
    """Los dos fallos que dejaron a un estudiante sin poder emitir informe.

    Medidos en dev el 2026-08-11: el modelo emite la llamada a las 04:18:29 y
    a las 04:18:32 el guard la rechaza por repetida. Era la unica de la
    conversacion.
    """

    def _pedir(self, id_actual, messages):
        mw = AssessmentOnceMiddleware()

        class FakeRequest:
            tool_call = {"name": ASSESSMENT_TOOL_NAME, "id": id_actual}
            state = {"messages": messages}

        paso = [False]

        def handler(req):
            paso[0] = True
            return "ok"

        resultado = mw.wrap_tool_call(FakeRequest(), handler)
        return paso[0], resultado

    def _llamada(self, cid):
        return AIMessage(
            content="", tool_calls=[ToolCall(name=ASSESSMENT_TOOL_NAME, args={}, id=cid)]
        )

    def test_la_llamada_en_curso_ya_esta_en_el_historial_y_no_cuenta(self):
        """Cuando corre el wrapper, el ``AIMessage`` con esta misma llamada ya
        esta en el estado. Contarlo hacia que la primera se viera a si misma."""
        paso, _ = self._pedir("c1", [HumanMessage(content="hola"), self._llamada("c1")])

        assert paso is True

    def test_un_intento_sin_respuesta_no_bloquea_el_reintento(self):
        """Asi acabo el turno que murio con `GraphRecursionError`: la llamada
        quedo en el historial y la respuesta no llego nunca."""
        historial = [self._llamada("c0"), self._llamada("c1")]

        paso, _ = self._pedir("c1", historial)

        assert paso is True

    def test_un_intento_que_fallo_tampoco(self):
        historial = [
            self._llamada("c0"),
            ToolMessage(content="Error: lo que sea", tool_call_id="c0", status="error"),
            self._llamada("c1"),
        ]

        paso, _ = self._pedir("c1", historial)

        assert paso is True

    def test_ni_uno_que_rechazo_este_mismo_guard(self):
        """El texto del rechazo dice «si el resultado se perdio, pide al
        usuario que repita el assessment». Si el propio rechazo contara, esa
        frase mandaria a un sitio del que no se puede volver."""
        historial = [
            self._llamada("c0"),
            ToolMessage(
                content=f"Error: {ASSESSMENT_TOOL_NAME} was already called",
                tool_call_id="c0",
            ),
            self._llamada("c1"),
        ]

        paso, _ = self._pedir("c1", historial)

        assert paso is True

    def test_una_evaluacion_hecha_si_bloquea(self):
        """Lo que el guard existe para evitar sigue evitado."""
        historial = [
            self._llamada("c0"),
            ToolMessage(content='{"riasec_code": "RIC"}', tool_call_id="c0"),
            self._llamada("c1"),
        ]

        paso, resultado = self._pedir("c1", historial)

        assert paso is False
        assert isinstance(resultado, ToolMessage)

    def test_el_rechazo_se_marca_como_error(self):
        """Para que un rechazo de este guard no se confunda mas adelante con
        una evaluacion buena -- que es como se enredo esto la primera vez."""
        historial = [
            self._llamada("c0"),
            ToolMessage(content='{"riasec_code": "RIC"}', tool_call_id="c0"),
            self._llamada("c1"),
        ]

        _, resultado = self._pedir("c1", historial)

        assert resultado.status == "error"


class TestAssessmentOnceMiddlewareAsync:
    """Regression tests for the async tool-call path.

    LangChain's middleware framework raises ``NotImplementedError`` for a
    tool call if only the sync (``wrap_tool_call``) or only the async
    (``awrap_tool_call``) hook is defined and the graph is invoked in the
    other mode. The production API (``ag-ui-langgraph``) drives the graph
    exclusively via ``astream_events``, so a missing ``awrap_tool_call``
    would break every tool call in production, not just the assessment
    tool. These tests exercise ``awrap_tool_call`` directly to make sure
    it exists and behaves identically to the sync version.
    """

    async def test_allows_first_call_async(self):
        mw = AssessmentOnceMiddleware()

        class FakeRequest:
            tool_call = {"name": "search_careers", "id": "c1"}
            state = {"messages": []}

        called = [False]

        async def fake_handler(req):
            called[0] = True
            return "ok"

        result = await mw.awrap_tool_call(FakeRequest(), fake_handler)
        assert called[0] is True
        assert result == "ok"

    async def test_rejects_second_assessment_call_async(self):
        mw = AssessmentOnceMiddleware()

        prior_call = ToolCall(name=ASSESSMENT_TOOL_NAME, args={}, id="c1")
        prior_ai = AIMessage(content="calling assessment", tool_calls=[prior_call])
        prior_ok = ToolMessage(content='{"riasec_code": "RIC"}', tool_call_id="c1")

        class FakeRequest:
            tool_call = {"name": ASSESSMENT_TOOL_NAME, "id": "c2"}
            state = {"messages": [prior_ai, prior_ok]}

        called = [False]

        async def fake_handler(req):
            called[0] = True
            return "should not happen"

        result = await mw.awrap_tool_call(FakeRequest(), fake_handler)
        assert called[0] is False
        assert isinstance(result, ToolMessage)
        assert "already called" in result.content.lower()
        assert result.tool_call_id == "c2"


class TestMiddlewareIntegration:
    """The factory wires both middlewares into the agent."""

    def test_factory_imports_middleware(self):
        from src.agent import factory
        from src.agent.middleware import (
            AssessmentOnceMiddleware,
            MaxTurnsMiddleware,
        )

        # Both classes are importable and instantiable
        assert callable(MaxTurnsMiddleware)
        assert callable(AssessmentOnceMiddleware)
        assert hasattr(factory, "create_spark_agent")


# Use a small fixture to set the env var cleanly per test
@pytest.fixture(autouse=True)
def _clean_settings_cache():
    """Each test gets a fresh Settings cache (env changes invalidate it)."""
    from src.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
