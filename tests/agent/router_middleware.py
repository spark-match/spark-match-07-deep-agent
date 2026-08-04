"""Unit tests for the router middleware's pure helpers (Sprint 8, task 8.4).

IntentRouterMiddleware's actual routing behavior (which model a given turn
is dispatched to) is tested end-to-end against the real compiled graph in
tests/agent/factory.py, not here: constructing a bare ModelRequest by hand
would require filling in every field (model, messages, system_message,
tool_choice, tools, response_format, state, runtime) including a real
LangGraph Runtime, which is both fragile across langgraph versions and
exactly the "isolated hook, not the compiled graph's behavior" pattern
AGENTS.md SS5.3 warns against for middleware tests.
"""

from langchain_core.language_models import GenericFakeChatModel

from src.agent.router_middleware import _model_label


class TestModelLabel:
    def test_uses_model_id_when_present(self):
        class _FakeBedrockLike(GenericFakeChatModel):
            model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

        model = _FakeBedrockLike(messages=iter([]))
        assert _model_label(model) == "us.anthropic.claude-haiku-4-5-20251001-v1:0"

    def test_falls_back_to_llm_type_when_no_model_id(self):
        """GenericFakeChatModel (used throughout the test suite) has no
        model_id -- must not raise, and must fall back to something
        identifying, not an empty string."""
        model = GenericFakeChatModel(messages=iter([]))
        label = _model_label(model)
        assert label
        assert label == model._llm_type
