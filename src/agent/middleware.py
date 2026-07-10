"""Agent middlewares for runtime guards.

Implements two enforcement hooks that prevent the agent from running away
or repeating itself:

- :class:`MaxTurnsMiddleware` — stops the agent cleanly when the number of
  model turns exceeds ``settings.max_turns``. Without this, LangGraph's
  ``recursion_limit=9999`` produces a cryptic error when hit.

- :class:`AssessmentOnceMiddleware` — rejects the second call to
  ``evaluate_riasec_profile`` within the same session. The assessment
  subagent's system prompt instructs it to call the tool ONCE at the end
  of the questionnaire, but LLM drift can produce extra calls.

Both are added to the agent via the ``middleware=[...]`` argument to
:func:`deepagents.create_deep_agent`.
"""

import logging

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage
from langgraph.graph import END

from src.config import get_settings

logger = logging.getLogger(__name__)

ASSESSMENT_TOOL_NAME = "evaluate_riasec_profile"


def _get_session_id() -> str:
    """Best-effort active-session id.

    Tries the budget tracker (Sprint 2) if available; falls back to
    ``"unknown"``. This keeps the middleware decoupled from the optional
    budget module.
    """
    try:
        from src.budget import get_active_session

        return get_active_session()
    except ImportError:
        return "unknown"


class MaxTurnsMiddleware(AgentMiddleware):
    """Stops the agent cleanly when turn count exceeds ``settings.max_turns``.

    Counts each model invocation. When the cap is reached, the middleware
    returns a state update with a final ``AIMessage`` explaining the cutoff
    and routes the graph to ``END`` via ``goto``.

    The turn count is approximate (counts every model call, including
    subagent delegations). For an exact cap use LangGraph's
    ``recursion_limit`` (we set a high value to let our guard fire first).
    """

    def after_model(self, state: AgentState, runtime: object) -> dict | None:
        """Inspect messages after each model call; cap if too many."""
        settings = get_settings()
        cap = settings.max_turns

        messages = state.get("messages", [])
        # Only count model-emitted messages (AIMessage) to avoid counting
        # human turns or tool messages.
        ai_count = sum(1 for m in messages if isinstance(m, AIMessage))

        if ai_count >= cap:
            session = _get_session_id()
            logger.warning(
                "Max turns reached (%d/%d) for session=%s — stopping agent",
                ai_count,
                cap,
                session,
            )
            # Return a final assistant message and route to END. The goto
            # in middleware state updates takes effect on the next node.
            return {
                "messages": [
                    AIMessage(
                        content=(
                            f"He alcanzado el límite de {cap} turnos de "
                            "razonamiento en esta sesión. Si necesitas más "
                            "ayuda, abre una nueva conversación."
                        ),
                    ),
                ],
                "goto": END,
            }
        return None


class AssessmentOnceMiddleware(AgentMiddleware):
    """Rejects repeat calls to ``evaluate_riasec_profile`` within one session.

    The assessment subagent's system prompt instructs it to call the tool
    once at the end of the questionnaire. LLM drift can produce extra
    calls, which waste tokens and re-evaluate the same profile.

    Inspects the conversation history for prior calls to the tool and
    short-circuits repeats with a clear error message.
    """

    def wrap_tool_call(self, request, handler):
        """Inspect the tool name; bypass if it's a repeat assessment call."""
        tool_call = request.tool_call
        if tool_call.get("name") != ASSESSMENT_TOOL_NAME:
            return handler(request)

        # Inspect messages for prior tool calls with this name. If found,
        # refuse the call with a clear error.
        state = request.state
        messages = state.get("messages", []) if state else []
        prior_calls = sum(
            1
            for m in messages
            for tc in getattr(m, "tool_calls", []) or []
            if tc.get("name") == ASSESSMENT_TOOL_NAME
        )
        if prior_calls > 0:
            session = _get_session_id()
            logger.warning(
                "Refusing repeat %s call (prior calls=%d) for session=%s",
                ASSESSMENT_TOOL_NAME,
                prior_calls,
                session,
            )
            from langchain_core.messages import ToolMessage

            return ToolMessage(
                content=(
                    f"Error: {ASSESSMENT_TOOL_NAME} was already called in this "
                    "session. Each conversation may evaluate the RIASEC profile "
                    "only once. If the result was lost, ask the user to redo "
                    "the assessment."
                ),
                tool_call_id=tool_call.get("id", ""),
            )
        return handler(request)


__all__ = [
    "ASSESSMENT_TOOL_NAME",
    "AssessmentOnceMiddleware",
    "MaxTurnsMiddleware",
]
