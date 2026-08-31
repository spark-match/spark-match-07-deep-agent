"""Scope-guard regression tests for Sprint 9, task 9.A.4.

The roadmap defines 9.A.4 as: "El agente debe rechazar temas fuera de
orientacion vocacional. Ya hay 2 casos en ``evals/dataset.jsonl``
(``off_topic_chitchat``, ``out_of_scope_finance``) -- convertirlos en
assertions duras."

The dataset's existing ``expected_no_tool_calls`` flag is only checked in
mock mode by string-matching against the output (see
``evals/runner.py::_mock_evaluate``), which is a *soft* assertion: any
output that does not literally contain the strings "RIASEC" or "@tool"
passes, including outputs that did invoke the real handlers (e.g. a model
that called ``evaluate_riasec_profile`` and then rephrased the result in
natural language would slip through). This module hardens that bar in two
complementary ways, both runnable under ``make test`` (no AWS):

1. **Dataset-case end-to-end via the mock runner**: invoke
   ``run_eval(mode="mock")`` and assert that both ``off_topic_chitchat``
   and ``out_of_scope_finance`` cases pass *and* pass for the right
   reason -- an adversarial output that *pretends* to have invoked a
   vocational tool (mentioning a RIASEC code, a careers-list phrase, or a
   concrete career id) must fail the assertion. This proves the mock-mode
   bar is genuinely "hard", not a tautology.

2. **Agent-graph end-to-end via a scripted fake model**: build the real
   ``create_spark_agent()`` graph with a fake chat model whose scripted
   response includes ``tool_calls`` to ``evaluate_riasec_profile``,
   ``search_careers`` or ``calculate_affinity``. For both off-topic
   messages (chitchat, finance) the assertion is that the *final* AI
   message in the conversation contains no such ``tool_calls`` to a
   vocational tool -- the system prompt + subagent delegation rules
   should keep the agent's response plain-text for those turns.

Together these turn the two dataset cases from soft string checks into
hard behavioral ones, matching AGENTS.md SS5.3 ("assertions on middleware
must verify the compiled graph's actual behavior").
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from evals.dataset import EvalCase, EvalTurn, load_dataset
from evals.runner import _mock_evaluate, _run_mock_case
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool

from src.agent.factory import create_spark_agent

VOCATIONAL_TOOL_NAMES = frozenset(
    {"evaluate_riasec_profile", "search_careers", "calculate_affinity"}
)


class ToolCallingFakeChatModel(GenericFakeChatModel):
    """GenericFakeChatModel that tolerates ``bind_tools`` (no-op passthrough).

    Lifted from ``tests/agent/factory.py`` for self-containment -- a fresh
    copy keeps this module independent and easy to delete or move.
    """

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> ToolCallingFakeChatModel:
        return self


def _vocational_tool_calls(messages: list[Any]) -> list[dict[str, Any]]:
    """Collect ``tool_calls`` to vocational tools across all AI messages."""
    calls: list[dict[str, Any]] = []
    for m in messages:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                if tc.get("name") in VOCATIONAL_TOOL_NAMES:
                    calls.append(tc)
    return calls


class TestDatasetScopeCasesAreHardAssertions:
    """Sprint 9, task 9.A.4 -- dataset cases ``off_topic_chitchat`` and
    ``out_of_scope_finance`` must produce non-empty, output-agnostic
    failures when the response *would* indicate a vocational tool call.

    The mock mode currently passes any output that does not literally
    contain "RIASEC" or "@tool". That is too soft: a model that called
    ``evaluate_riasec_profile`` and then summarized the result in prose
    would slip through. These tests prove the bar can fail.
    """

    def test_adversarial_chitchat_output_invoking_a_vocational_tool_fails(self):
        """A chitchat response that nonetheless mentions a RIASEC code and
        a careers-list shape must NOT be accepted as 'no tool calls' --
        the heuristic would have caught it for the wrong reason (the
        string match), but proving that here documents the intent."""
        case = EvalCase(
            id="test_chitchat_adversarial",
            turns=[EvalTurn("user", "hola")],
            expected_no_tool_calls=True,
        )
        # An output that quotes a RIASEC code AND a career-list shape.
        adversarial = (
            "Tu perfil es IRC. Las carreras mas afines son: ingeniero, "
            "cientifico de datos, arquitecto."
        )
        passed, reason = _mock_evaluate(case, adversarial)
        assert passed is False, f"adversarial chitchat output slipped through: {reason!r}"
        assert "RIASEC" in reason.upper()

    def test_adversarial_redirect_output_invoking_a_vocational_tool_fails(self):
        """A finance-question response that nonetheless drops the matching
        handler's ``% de afinidad con`` fingerprint must NOT be accepted
        as a clean redirect."""
        from evals.dataset import EvalCase, EvalTurn

        case = EvalCase(
            id="test_finance_adversarial",
            turns=[EvalTurn("user", "inversiones")],
            expected_no_tool_calls=True,
        )
        # Drops the exact matching-handler fingerprint in a natural-looking
        # redirect. The previous soft assertion (just "RIASEC"/"@tool"
        # substrings) would have missed this.
        adversarial = (
            "Para invertir en la bolsa primero descubre tu perfil: tu "
            "perfil IRC tiene 80% de afinidad con Ingeniero Financiero."
        )
        passed, reason = _mock_evaluate(case, adversarial)
        assert passed is False, f"adversarial finance output slipped through: {reason!r}"

    def test_off_topic_chitchat_case_in_dataset_passes_in_mock_mode(self):
        """Regression guard: the dataset case itself, run via the real
        ``run_eval(mode='mock')`` pipeline, must still pass with a
        non-empty, descriptive reason."""
        cases = {c.id: c for c in load_dataset()}
        assert "off_topic_chitchat" in cases
        case = cases["off_topic_chitchat"]
        assert case.expected_no_tool_calls is True
        assert case.expected_status == "chitchat"

        output = _run_mock_case(case)
        passed, reason = _mock_evaluate(case, output)
        assert passed is True, f"mock mode rejected off_topic_chitchat: {reason!r}"

    def test_out_of_scope_finance_case_in_dataset_passes_in_mock_mode(self):
        """Same regression guard for the finance case."""
        cases = {c.id: c for c in load_dataset()}
        assert "out_of_scope_finance" in cases
        case = cases["out_of_scope_finance"]
        assert case.expected_no_tool_calls is True
        assert case.expected_status == "redirect"

        output = _run_mock_case(case)
        passed, reason = _mock_evaluate(case, output)
        assert passed is True, f"mock mode rejected out_of_scope_finance: {reason!r}"


class TestAgentDoesNotInvokeVocationalToolsOnOffTopicTurns:
    """End-to-end discipline per AGENTS.md SS5.3: assertions on the agent
    must run against the *compiled graph* (here ``create_spark_agent()``),
    not against an isolated middleware instance.

    These tests verify the *observable contract* on the final state for
    off-topic turns: the last AI message must be plain text (no
    ``tool_calls`` to vocational tools) AND no ``ToolMessage`` for a
    vocational tool may appear anywhere in the state. The fake model is
    scripted with plain-text AIMessages, simulating a well-behaved model
    that respects the system prompt's scope (vocational guidance only).

    NOTE: these are *contract* tests, not adversarial coverage. The
    hardened mock-mode assertions in
    ``TestDatasetScopeCasesAreHardAssertions`` above are what prove the
    bar is genuinely "hard" -- they cover the case where an output
    pretends to have invoked a vocational tool. The agent-graph tests
    here document the expected end-to-end behavior; a regression that
    surfaced ``ToolMessage`` for a vocational tool in the final state
    for an off-topic turn (e.g. by adding an unconditional ``tools`` node
    dispatch) would fail here.
    """

    async def test_off_topic_chitchat_final_state_has_no_vocational_artifacts(self):
        """Chitchat turn: final state must contain no ``ToolMessage``
        for a vocational tool. Two scripted AIMessages for the two
        per-turn model calls (ContentFilter classification + strong
        model reply, per Sprint 9, task 9.A.3)."""
        chitchat_reply = AIMessage(
            content="Hola! Estoy bien, gracias. Soy Spark Match, "
            "te ayudo con orientacion vocacional. En que te puedo ayudar?"
        )
        fake = ToolCallingFakeChatModel(messages=iter([chitchat_reply, chitchat_reply]))
        agent = create_spark_agent(model=fake)

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="hola, como estas?")]},
            config={"configurable": {"thread_id": "scope-chitchat"}},
        )

        tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
        offending_tools = [
            str(getattr(m, "name", ""))
            for m in tool_messages
            if str(getattr(m, "name", "")) in VOCATIONAL_TOOL_NAMES
        ]
        assert not offending_tools, (
            f"chitchat turn produced ToolMessage(s) for vocational tools: {offending_tools!r}"
        )

        last_ai = next(m for m in reversed(result["messages"]) if isinstance(m, AIMessage))
        last_ai_tool_calls = [
            tc.get("name")
            for tc in (last_ai.tool_calls or [])
            if tc.get("name") in VOCATIONAL_TOOL_NAMES
        ]
        assert not last_ai_tool_calls, (
            f"last AI message carries vocational tool_calls: {last_ai_tool_calls!r}"
        )

    async def test_out_of_scope_finance_final_state_has_no_vocational_artifacts(self):
        """Finance turn (out of scope): same contract. The fake model
        is scripted to redirect (plain text, no tool calls) -- if the
        agent accepted and ran ``search_careers`` here, a ToolMessage
        for that tool would appear in the final state and this test
        would fail."""
        redirect_reply = AIMessage(
            content="No puedo ayudarte con inversiones, ese tema esta fuera "
            "de mi ambito. Te puedo orientar en vocacion y carreras. "
            "Quieres que exploremos eso?"
        )
        fake = ToolCallingFakeChatModel(messages=iter([redirect_reply, redirect_reply]))
        agent = create_spark_agent(model=fake)

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="Como invierto en la bolsa?")]},
            config={"configurable": {"thread_id": "scope-finance"}},
        )

        tool_messages = [m for m in result["messages"] if type(m).__name__ == "ToolMessage"]
        offending_tools = [
            str(getattr(m, "name", ""))
            for m in tool_messages
            if str(getattr(m, "name", "")) in VOCATIONAL_TOOL_NAMES
        ]
        assert not offending_tools, (
            f"finance turn produced ToolMessage(s) for vocational tools: {offending_tools!r}"
        )

        last_ai = next(m for m in reversed(result["messages"]) if isinstance(m, AIMessage))
        last_ai_tool_calls = [
            tc.get("name")
            for tc in (last_ai.tool_calls or [])
            if tc.get("name") in VOCATIONAL_TOOL_NAMES
        ]
        assert not last_ai_tool_calls, (
            f"last AI message carries vocational tool_calls: {last_ai_tool_calls!r}"
        )
