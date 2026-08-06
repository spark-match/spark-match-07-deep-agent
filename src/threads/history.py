"""Rehydrating a conversation from the checkpointer.

The checkpointer already holds every turn — that is what makes the agent
remember within a session. What was missing is a way for the frontend to
*read* it: on a page reload the browser has no record of the conversation
it was in the middle of, and the AG-UI endpoint only streams new turns.

Not everything in the checkpoint is conversation. ``ProfileHydrationMiddleware``
injects a ``SystemMessage`` containing the student's extracted vocational
profile on every turn, and those land in the same ``messages`` channel.
Returning the checkpoint verbatim would ship that block — the profile plus
the "no vuelvas a preguntar lo que ya está aquí" instruction — to the
browser as part of the chat history. So this filters down to what the
student actually said and what the advisor actually answered.
"""

from __future__ import annotations

from typing import Any, Protocol


class SupportsGetState(Protocol):
    """The slice of a compiled LangGraph this module needs.

    Reading the checkpointer directly does not work: ``aget_tuple`` returns
    the *latest* checkpoint, whose ``channel_values`` holds only the
    channels that step touched — on a finished turn that is
    ``skills_metadata`` and ``memory_contents``, with no ``messages`` in
    sight. Reconstructing the full state from checkpoint blobs and channel
    versions is exactly the job ``aget_state`` already does, so this goes
    through the graph instead.
    """

    async def aget_state(self, config: dict[str, Any]) -> Any: ...


# Only these reach the client. Tool messages and tool-call-only assistant
# turns are machinery: they carry no text a student would recognize as
# part of their conversation, and some carry raw search results.
_CLIENT_VISIBLE_ROLES = {"human": "user", "ai": "assistant"}


def _text_of(content: Any) -> str:
    """Flatten LangChain message content into plain text.

    Content is a string for most providers but a list of typed blocks for
    others (and always, once a turn includes reasoning or images). Only
    text blocks survive.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""


async def load_thread_messages(
    graph: SupportsGetState | None,
    thread_id: str,
) -> list[dict[str, Any]]:
    """Return the client-visible message history of ``thread_id``.

    An unknown or never-used thread yields an empty list rather than an
    error: to a caller reopening a conversation, "no messages yet" and
    "this conversation was never started" are the same thing, and
    distinguishing them would leak whether a derived id exists.
    """
    if graph is None:
        return []

    snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    messages = (getattr(snapshot, "values", None) or {}).get("messages") or []

    history: list[dict[str, Any]] = []
    for message in messages:
        role = _CLIENT_VISIBLE_ROLES.get(getattr(message, "type", ""))
        if role is None:
            continue
        text = _text_of(getattr(message, "content", ""))
        if not text.strip():
            # An assistant turn that only carried tool calls. Real to the
            # graph, invisible to the conversation.
            continue
        history.append({"id": getattr(message, "id", None), "role": role, "content": text})

    return history


__all__ = ["load_thread_messages"]
