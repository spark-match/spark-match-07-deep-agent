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
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ToolCallRequest,
    hook_config,
)
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.runtime import Runtime
from langgraph.types import Command

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
    and routes the graph to ``"end"`` via ``jump_to``.

    The turn count is approximate (counts every model call, including
    subagent delegations). For an exact cap use LangGraph's
    ``recursion_limit`` (we set a high value to let our guard fire first).
    """

    @hook_config(can_jump_to=["end"])
    def after_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
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
            # jump_to is the real LangChain 1.x contract for cutting the
            # graph short from a middleware hook. LangGraph silently drops
            # unknown state keys, so a plain "goto" key here is a no-op:
            # the graph keeps running past settings.max_turns. The
            # @hook_config(can_jump_to=["end"]) decorator above is what
            # registers the conditional edge that makes "end" a valid
            # destination for this hook.
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
                "jump_to": "end",
            }
        return None


class AssessmentOnceMiddleware(AgentMiddleware):
    """Rejects repeat calls to ``evaluate_riasec_profile`` within one session.

    The assessment subagent's system prompt instructs it to call the tool
    once at the end of the questionnaire. LLM drift can produce extra
    calls, which waste tokens and re-evaluate the same profile.

    Inspects the conversation history for prior calls to the tool and
    short-circuits repeats with a clear error message.

    Implements both the sync (``wrap_tool_call``) and async
    (``awrap_tool_call``) hooks. LangChain's middleware framework requires
    whichever one matches the graph's invocation mode (``invoke``/``stream``
    vs ``ainvoke``/``astream``) — defining only one raises
    ``NotImplementedError`` for every tool call in the other mode. The
    production API (``ag-ui-langgraph``) drives the graph exclusively via
    ``astream_events``, so the async hook is required, not optional.
    """

    def _cuajo(self, messages: list[Any], call_id: str) -> bool:
        """Si esa llamada llego a devolver un perfil, y no un error.

        Sin respuesta tambien es ``False``: el turno se murio antes de que
        llegara (asi acabo el del ``GraphRecursionError`` del 2026-08-11), y
        una evaluacion que nadie recibio no es una evaluacion hecha.
        """
        for m in messages:
            if not isinstance(m, ToolMessage) or m.tool_call_id != call_id:
                continue
            if getattr(m, "status", None) == "error":
                return False
            return not str(m.content).startswith("Error:")
        return False

    def _find_repeat_response(self, request: ToolCallRequest) -> ToolMessage | None:
        """Return a refusal ``ToolMessage`` if this is a repeat assessment call.

        Returns ``None`` when the call should proceed (either it targets a
        different tool, or no prior assessment call cuajo).

        Dos cosas que este guard hacia mal y dejaban al estudiante sin
        informe, medidas en dev el 2026-08-11:

        1. **Se contaba a si mismo.** Cuando corre el wrapper, el
           ``AIMessage`` con esta misma llamada YA esta en ``state``, asi que
           la primera llamada de la conversacion se veia a si misma y salia
           "prior calls=1". En la traza se ve entero: el modelo la emite a
           las 04:18:29 y a las 04:18:32 se rechaza por repetida. Se excluye
           por ``id``, que es lo unico que distingue una llamada de otra.
        2. **Contaba intentos, no evaluaciones.** Una llamada que reventó, o
           que se quedo sin respuesta porque el turno murio, bloqueaba el
           reintento -- justo el que el texto del rechazo recomienda pedir.
           Ahora solo cuentan las que cuajaron.

        Lo que el guard existe para evitar sigue evitado: reevaluar un perfil
        que ya esta hecho. Eso no cambia.
        """
        tool_call = request.tool_call
        if tool_call.get("name") != ASSESSMENT_TOOL_NAME:
            return None

        state = request.state
        messages = state.get("messages", []) if state else []
        id_actual = tool_call.get("id")
        anteriores = [
            tc.get("id")
            for m in messages
            for tc in getattr(m, "tool_calls", []) or []
            if tc.get("name") == ASSESSMENT_TOOL_NAME and tc.get("id") != id_actual
        ]
        hechas = [cid for cid in anteriores if self._cuajo(messages, cid)]
        if not hechas:
            return None

        session = _get_session_id()
        logger.warning(
            "Refusing repeat %s call (%d hechas, %d intentos) for session=%s",
            ASSESSMENT_TOOL_NAME,
            len(hechas),
            len(anteriores),
            session,
        )
        return ToolMessage(
            content=(
                f"Error: {ASSESSMENT_TOOL_NAME} was already called in this "
                "session. Each conversation may evaluate the RIASEC profile "
                "only once. If the result was lost, ask the user to redo "
                "the assessment."
            ),
            tool_call_id=tool_call.get("id", ""),
            status="error",
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Inspect the tool name; bypass if it's a repeat assessment call."""
        refusal = self._find_repeat_response(request)
        return refusal if refusal is not None else handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Async counterpart of :meth:`wrap_tool_call` — same refusal logic."""
        refusal = self._find_repeat_response(request)
        return refusal if refusal is not None else await handler(request)


__all__ = [
    "ASSESSMENT_TOOL_NAME",
    "AssessmentOnceMiddleware",
    "MaxTurnsMiddleware",
]
