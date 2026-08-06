"""Unit tests for conversation rehydration (src/threads/history.py).

Uses a real compiled LangGraph with a real checkpointer rather than a
stub. That matters here: the first version of this module read the
checkpointer directly and looked correct, but ``aget_tuple`` returns only
the channels the *last* step touched, so on a finished turn it came back
with ``skills_metadata`` and ``memory_contents`` and no messages at all. A
fake ``aget_state`` would have happily agreed with the broken code.
"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, MessagesState, StateGraph

from src.threads.history import load_thread_messages


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
