"""Smoke tests for the compiled agent graph.

Builds the real ``create_spark_agent()`` graph (no mocks on the factory
itself) and exercises it end-to-end with a scripted fake chat model. This
is the only test in the suite that invokes the actual compiled LangGraph
state machine, so it is what catches middleware wiring regressions that
unit tests against isolated middleware instances cannot: LangGraph silently
drops unknown state-update keys (see ``src/agent/middleware.py`` — the
``goto`` vs ``jump_to`` bug), and this project's async production API
(``ag-ui-langgraph``) only exercises the graph via ``astream_events``.
"""

from collections.abc import Callable, Sequence
from typing import Any

import pytest
from langchain_core.language_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import InMemorySaver

from src.agent.factory import create_spark_agent


class ToolCallingFakeChatModel(GenericFakeChatModel):
    """A ``GenericFakeChatModel`` that tolerates ``bind_tools``.

    The upstream model raises ``NotImplementedError`` from ``bind_tools``,
    which breaks any tool-calling agent loop (deepagents always binds
    tools before invoking the model). Binding is a no-op here: the fake
    model replays its scripted messages regardless of what is bound.
    """

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Callable[..., Any] | BaseTool],
        *,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> ToolCallingFakeChatModel:
        return self


def _looping_tool_call_messages(count: int, tool_name: str = "search_careers") -> list[AIMessage]:
    """Build ``count`` canned AIMessages that each call the same tool.

    Used to simulate a model that never voluntarily stops, so the only
    thing that can end the conversation is ``MaxTurnsMiddleware``.
    """
    return [
        AIMessage(
            content="",
            tool_calls=[
                {"name": tool_name, "args": {"query": "software"}, "id": f"call_{i}"},
            ],
        )
        for i in range(count)
    ]


@pytest.fixture(autouse=True)
def _clean_settings_cache():
    """Each test gets a fresh Settings cache (env changes invalidate it)."""
    from src.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class TestAgentGraphStructure:
    """Structural assertions on the compiled graph — catches wiring drift."""

    def test_builds_without_aws_credentials(self):
        """create_spark_agent() must not need live AWS access to construct."""
        fake = ToolCallingFakeChatModel(messages=iter([AIMessage(content="hi")]))
        agent = create_spark_agent(model=fake)
        assert agent is not None

    def test_graph_has_model_and_tools_nodes(self):
        fake = ToolCallingFakeChatModel(messages=iter([AIMessage(content="hi")]))
        agent = create_spark_agent(model=fake)
        nodes = agent.get_graph().nodes
        assert "model" in nodes
        assert "tools" in nodes

    def test_max_turns_middleware_is_wired_into_the_graph(self):
        """Regression guard for B1: the hook must actually register as a node.

        A middleware whose ``after_model``/``before_model`` hook never runs
        (e.g. missing from ``middleware=[...]``, or the class import broken)
        would silently vanish from the graph with no error at construction
        time — only a real invocation would ever reveal it.
        """
        fake = ToolCallingFakeChatModel(messages=iter([AIMessage(content="hi")]))
        agent = create_spark_agent(model=fake)
        nodes = agent.get_graph().nodes
        assert any("MaxTurnsMiddleware" in name for name in nodes)

    def test_custom_tools_and_subagent_delegation_are_bound(self):
        """The 4 project tools and the subagent 'task' tool must be bound."""
        fake = ToolCallingFakeChatModel(messages=iter([AIMessage(content="hi")]))
        agent = create_spark_agent(model=fake)
        tools_node = agent.get_graph().nodes["tools"].data
        tool_names = set(tools_node.tools_by_name.keys())
        assert {
            "evaluate_riasec_profile",
            "search_careers",
            "calculate_affinity",
            "web_search",
            "task",  # deepagents' subagent-delegation tool
        }.issubset(tool_names)


class TestMaxTurnsActuallyStopsTheGraph:
    """Regression test for B1 — the previous ``goto`` bug never stopped this."""

    async def test_max_turns_actually_stops_the_graph(self, monkeypatch):
        monkeypatch.setenv("SPARK_MAX_TURNS", "2")
        from src.config import get_settings

        get_settings.cache_clear()

        # A model that always calls a tool: if MaxTurnsMiddleware did nothing
        # (the old "goto" bug), this would run until recursion_limit blew up
        # with a cryptic GraphRecursionError instead of stopping cleanly.
        fake = ToolCallingFakeChatModel(messages=iter(_looping_tool_call_messages(50)))
        agent = create_spark_agent(model=fake)

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="quiero explorar carreras")]},
            config={"configurable": {"thread_id": "t1"}, "recursion_limit": 50},
        )

        ai_messages = [m for m in result["messages"] if isinstance(m, AIMessage)]
        # cap (2) real turns + at most 1 cutoff message from the middleware.
        assert len(ai_messages) <= 3
        assert "límite" in ai_messages[-1].content.lower()

    async def test_under_cap_the_model_can_still_respond_plainly(self, monkeypatch):
        """Sanity check: a model that answers once, without tool calls, is
        never touched by the cutoff — proves the guard is cap-triggered,
        not unconditional.
        """
        monkeypatch.setenv("SPARK_MAX_TURNS", "50")
        from src.config import get_settings

        get_settings.cache_clear()

        fake = ToolCallingFakeChatModel(
            messages=iter([AIMessage(content="Hola, ¿en qué te puedo ayudar?")]),
        )
        agent = create_spark_agent(model=fake)

        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="hola")]},
            config={"configurable": {"thread_id": "t2"}},
        )

        ai_messages = [m for m in result["messages"] if isinstance(m, AIMessage)]
        assert len(ai_messages) == 1
        assert "límite" not in ai_messages[-1].content.lower()


class TestCheckpointerPersistsConversationAcrossInvocations:
    """Regression test for 6.F/6.G — a real ``thread_id`` round trip.

    ``create_spark_agent()`` accepts an optional ``checkpointer``; without
    one (the default, used by every other test in this file) each
    ``ainvoke`` starts from a blank slate regardless of ``thread_id``. These
    tests prove that when a checkpointer IS wired in, LangGraph genuinely
    persists conversation state keyed by ``thread_id`` — the whole point of
    Sprint 6 — and that different ``thread_id``s stay isolated from
    each other.
    """

    async def test_two_turns_with_the_same_thread_id_share_history(self):
        fake = ToolCallingFakeChatModel(
            messages=iter(
                [
                    AIMessage(content="Hola! ¿En qué puedo ayudarte?"),
                    AIMessage(content="Claro, ya recuerdo lo que conversamos antes."),
                ]
            ),
        )
        checkpointer = InMemorySaver()
        agent = create_spark_agent(model=fake, checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "same-thread"}}

        await agent.ainvoke(
            {"messages": [HumanMessage(content="hola, soy Juan y me gusta la biología")]},
            config=config,
        )
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="¿de qué hablamos antes?")]},
            config=config,
        )

        human_messages = [m for m in result["messages"] if isinstance(m, HumanMessage)]
        # Both turns' human messages are in the checkpointed state — proves
        # the second invocation did NOT start from a blank slate.
        assert len(human_messages) == 2
        assert "Juan" in human_messages[0].content

    async def test_different_thread_ids_do_not_share_history(self):
        fake = ToolCallingFakeChatModel(
            messages=iter([AIMessage(content="Hola!"), AIMessage(content="Hola, bienvenido!")]),
        )
        checkpointer = InMemorySaver()
        agent = create_spark_agent(model=fake, checkpointer=checkpointer)

        await agent.ainvoke(
            {"messages": [HumanMessage(content="hola, soy Juan")]},
            config={"configurable": {"thread_id": "thread-a"}},
        )
        result = await agent.ainvoke(
            {"messages": [HumanMessage(content="hola")]},
            config={"configurable": {"thread_id": "thread-b"}},
        )

        human_messages = [m for m in result["messages"] if isinstance(m, HumanMessage)]
        # thread-b starts fresh: only its own message, never Juan's from
        # thread-a. This is the isolation half of the DoD (full user_id
        # isolation lands in Sprint 7; this only proves thread-level
        # isolation of the checkpointer itself).
        assert len(human_messages) == 1
        assert "Juan" not in human_messages[0].content
