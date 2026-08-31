"""Small shared helpers for guardrail middlewares that inspect user turns.

Extracted out of ``src/agent/guardrails.py`` once a second guardrail
(``src/agent/content_filter.py``, Sprint 9 task 9.A.3) needed the exact
same "what did the student just say" lookup — duplicating a 6-line
function twice was worse than one shared, public, tested home for it.
"""

from langchain.agents.middleware import AgentState
from langchain_core.messages import HumanMessage


def last_human_message_text(state: AgentState) -> str:
    """Return the content of the most recent ``HumanMessage`` in state.

    Returns an empty string if there is none yet (first ``before_model``
    call of a turn where the graph was invoked with no human input, e.g.
    a resumed tool loop) — callers can treat an empty string as "nothing
    to check".
    """
    messages = state.get("messages", [])
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


__all__ = ["last_human_message_text"]
