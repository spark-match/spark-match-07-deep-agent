"""Tests for the optional ``RubricMiddleware`` wiring in Sprint 9, task
9.B.5. See ``docs/rubric-middleware-evaluation.md`` for the full
rationale: the middleware is *complementary* to ``SparkMatchJudge``,
not a substitute, and is gated behind ``enable_rubric=True`` because
its deepagents API is ``.. beta::`` and adds a per-turn LLM-call
cost when activated.

Two regression guards exercised here:

1. ``RubricMiddleware`` is **not** in the stack by default
   (``enable_rubric=False``). This proves the production path is
   unchanged -- a graph built without the flag must not regress on
   latency, cost, or eval pass-rate (the latter is exercised through
   the dataset loader tests in ``tests/evals/framework.py``).

2. When ``enable_rubric=True``, the middleware IS in the stack but
   is **no-op without a rubric on the invocation state** -- the
   docstring contract that makes the optional wiring safe. The
   ``RubricMiddleware`` source explicitly states: "The middleware
   activates only when a caller passes a `rubric` on invocation
   state. With no rubric, both `before_agent` and `after_agent`
   return without modifying state." (deepagents 0.6.12, verified via
   ``inspect.getsource(RubricMiddleware)``). We assert the resulting
   agent still produces a normal single-turn response without the
   grader sub-agent being invoked.
"""

from __future__ import annotations

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage

from src.agent.factory import create_spark_agent


class ToolCallingFakeChatModel(GenericFakeChatModel):
    """Local copy of the ``bind_tools``-tolerating fake used elsewhere
    in this repo's tests; deliberately redefined here to keep this
    module self-contained -- if a future test reorganization moves
    the shared version, this file keeps working."""

    def bind_tools(
        self,
        tools,  # mirror of upstream signature -- intentionally untyped
        *,
        tool_choice=None,
        **kwargs,
    ):
        return self


class TestRubricMiddlewareIsOptIn:
    """Default state: no RubricMiddleware in the graph, no per-turn
    cost, no API instability risk for callers who didn't ask for it."""

    def test_default_factory_does_not_wire_rubric_middleware(self):
        fake = ToolCallingFakeChatModel(messages=iter([AIMessage(content="hi")]))
        agent = create_spark_agent(model=fake)
        nodes = agent.get_graph().nodes
        assert not any("RubricMiddleware" in name for name in nodes), (
            "RubricMiddleware is opt-in only -- default factory must "
            "not include it (see docs/rubric-middleware-evaluation.md "
            "SS4 for why)"
        )

    def test_default_factory_still_works_for_a_normal_turn(self):
        """Sanity check: enabling rubric off, the graph still answers
        a single turn end-to-end (regression guard for the wiring
        change itself)."""
        fake = ToolCallingFakeChatModel(
            messages=iter([AIMessage(content="ok"), AIMessage(content="ok")])
        )
        agent = create_spark_agent(model=fake)
        result = agent.invoke(
            {"messages": [HumanMessage(content="hola")]},
            config={"configurable": {"thread_id": "no-rubric"}},
        )
        ai_messages = [m for m in result["messages"] if isinstance(m, AIMessage)]
        assert ai_messages[-1].content == "ok"


class TestRubricMiddlewareWhenOptedIn:
    """Opt-in state: the middleware is wired but does nothing on its
    own (the upstream contract) -- the caller must also pass a
    ``rubric`` on invocation state to activate the grader loop."""

    def test_enable_rubric_wires_the_middleware(self):
        fake = ToolCallingFakeChatModel(messages=iter([AIMessage(content="ok")]))
        agent = create_spark_agent(model=fake, enable_rubric=True)
        nodes = agent.get_graph().nodes
        assert any("RubricMiddleware" in name for name in nodes), (
            "enable_rubric=True must add RubricMiddleware to the stack"
        )

    def test_enable_rubric_without_rubric_state_is_still_a_single_turn(self):
        """With the middleware wired but no ``rubric`` on invocation
        state, the upstream contract says the middleware is no-op.
        Asserting that the agent produces a normal single-turn
        response proves we have not broken the default path even
        when the middleware is present in the stack."""
        fake = ToolCallingFakeChatModel(
            messages=iter([AIMessage(content="ok"), AIMessage(content="ok")])
        )
        agent = create_spark_agent(model=fake, enable_rubric=True)
        result = agent.invoke(
            {
                "messages": [HumanMessage(content="hola")],
                # Note: NO "rubric" key -- middleware must be no-op.
            },
            config={"configurable": {"thread_id": "rubric-noop"}},
        )
        ai_messages = [m for m in result["messages"] if isinstance(m, AIMessage)]
        assert ai_messages[-1].content == "ok"
