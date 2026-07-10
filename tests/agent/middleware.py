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
        # Should append a final AIMessage and route to END
        assert "messages" in result
        new_msg = result["messages"][0]
        assert isinstance(new_msg, AIMessage)
        assert "límite" in new_msg.content or "limite" in new_msg.content.lower()
        assert "2" in new_msg.content  # cap value echoed
        assert result.get("goto") == "__end__"  # END constant

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

        class FakeRequest:
            tool_call = {"name": ASSESSMENT_TOOL_NAME, "id": "c2"}
            state = {"messages": [prior_ai]}

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
