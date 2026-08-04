"""Unit tests for the content-safety classifier guardrail (Sprint 9, 9.A.3).

See tests/agent/factory.py for the compiled-graph end-to-end test
(AGENTS.md SS5.3) -- this file covers _parse_classification() and the
hook's return value in isolation, using a scripted GenericFakeChatModel
as the classifier (no real Bedrock/Haiku call, matching this project's
no-AWS-required-to-build guarantee, AGENTS.md hard rule #7).
"""

from langchain_core.language_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from src.agent.content_filter import (
    CANONICAL_UNSAFE_CONTENT_REFUSAL,
    ContentFilterMiddleware,
    _parse_classification,
)


def _fake_runtime() -> object:
    return object()


def _fake_classifier(*responses: str) -> GenericFakeChatModel:
    """A GenericFakeChatModel that replays the given raw response strings,
    one per call, in order — mirrors the strict-JSON classifier output
    shape ContentFilterMiddleware expects."""
    return GenericFakeChatModel(messages=iter(AIMessage(content=r) for r in responses))


class _ExplodingClassifier(GenericFakeChatModel):
    """Raises on every call — used to test the fail-open policy."""

    def _generate(self, *args, **kwargs):
        raise RuntimeError("simulated classifier outage")

    async def _agenerate(self, *args, **kwargs):
        raise RuntimeError("simulated classifier outage")


class TestParseClassification:
    def test_parses_safe_true(self):
        safe, reason = _parse_classification('{"safe": true, "reason": "benign question"}')
        assert safe is True
        assert reason == "benign question"

    def test_parses_safe_false(self):
        safe, reason = _parse_classification('{"safe": false, "reason": "violence request"}')
        assert safe is False
        assert reason == "violence request"

    def test_strips_markdown_fences(self):
        safe, _reason = _parse_classification('```json\n{"safe": true, "reason": "ok"}\n```')
        assert safe is True

    def test_malformed_json_fails_open(self):
        safe, reason = _parse_classification("not json at all")
        assert safe is True
        assert "fail-open" in reason

    def test_non_object_json_fails_open(self):
        safe, reason = _parse_classification("[1, 2, 3]")
        assert safe is True
        assert "fail-open" in reason

    def test_missing_safe_key_defaults_to_true(self):
        safe, reason = _parse_classification('{"reason": "no safe key"}')
        assert safe is True
        assert reason == "no safe key"


class TestContentFilterMiddlewareSync:
    def test_safe_message_returns_none(self):
        mw = ContentFilterMiddleware(
            classifier_model=_fake_classifier('{"safe": true, "reason": "vocational question"}')
        )
        state = {"messages": [HumanMessage(content="¿Qué carreras me convienen?")]}

        assert mw.before_model(state, _fake_runtime()) is None

    def test_unsafe_message_blocks_and_jumps_to_end(self):
        mw = ContentFilterMiddleware(
            classifier_model=_fake_classifier('{"safe": false, "reason": "violence"}')
        )
        state = {"messages": [HumanMessage(content="algo peligroso")]}

        result = mw.before_model(state, _fake_runtime())

        assert result is not None
        assert result.get("jump_to") == "end"
        new_msg = result["messages"][0]
        assert isinstance(new_msg, AIMessage)
        assert new_msg.content == CANONICAL_UNSAFE_CONTENT_REFUSAL

    def test_empty_message_returns_none_without_calling_the_classifier(self):
        classifier = _fake_classifier('{"safe": false, "reason": "should never be read"}')
        mw = ContentFilterMiddleware(classifier_model=classifier)

        result = mw.before_model({"messages": []}, _fake_runtime())

        assert result is None

    def test_classifier_exception_fails_open(self):
        """Sprint 9, 9.A.3: confirmed fail-open policy — a classifier
        outage must never block a legitimate turn."""
        mw = ContentFilterMiddleware(classifier_model=_ExplodingClassifier(messages=iter([])))
        state = {"messages": [HumanMessage(content="cualquier mensaje")]}

        result = mw.before_model(state, _fake_runtime())

        assert result is None

    def test_logs_warning_on_block(self, caplog):
        import logging

        mw = ContentFilterMiddleware(
            classifier_model=_fake_classifier('{"safe": false, "reason": "self-harm"}')
        )
        state = {"messages": [HumanMessage(content="algo preocupante")]}

        with caplog.at_level(logging.WARNING):
            mw.before_model(state, _fake_runtime())

        assert any("guardrail_blocked" in r.message for r in caplog.records)

    def test_logs_warning_on_classifier_error(self, caplog):
        import logging

        mw = ContentFilterMiddleware(classifier_model=_ExplodingClassifier(messages=iter([])))
        state = {"messages": [HumanMessage(content="hola")]}

        with caplog.at_level(logging.WARNING):
            mw.before_model(state, _fake_runtime())

        assert any("content_filter_error" in r.message for r in caplog.records)


class TestContentFilterMiddlewareAsync:
    """Mirrors TestContentFilterMiddlewareSync for abefore_model — required,
    not optional, since the production API (ag-ui-langgraph) drives the
    graph exclusively via astream_events (same reasoning already
    documented for AssessmentOnceMiddleware/IntentRouterMiddleware)."""

    async def test_safe_message_returns_none(self):
        mw = ContentFilterMiddleware(
            classifier_model=_fake_classifier('{"safe": true, "reason": "ok"}')
        )
        state = {"messages": [HumanMessage(content="¿Cómo llego a ser ingeniero?")]}

        assert await mw.abefore_model(state, _fake_runtime()) is None

    async def test_unsafe_message_blocks_and_jumps_to_end(self):
        mw = ContentFilterMiddleware(
            classifier_model=_fake_classifier('{"safe": false, "reason": "illegal activity"}')
        )
        state = {"messages": [HumanMessage(content="algo ilegal")]}

        result = await mw.abefore_model(state, _fake_runtime())

        assert result is not None
        assert result.get("jump_to") == "end"
        assert result["messages"][0].content == CANONICAL_UNSAFE_CONTENT_REFUSAL

    async def test_classifier_exception_fails_open(self):
        mw = ContentFilterMiddleware(classifier_model=_ExplodingClassifier(messages=iter([])))
        state = {"messages": [HumanMessage(content="cualquier mensaje")]}

        result = await mw.abefore_model(state, _fake_runtime())

        assert result is None
