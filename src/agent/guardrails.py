"""Prompt-injection / jailbreak guardrail (Sprint 9, task 9.A.1).

Blocks a turn *before* the model is ever invoked when the last
``HumanMessage`` matches a known prompt-injection / jailbreak pattern
(bilingual: English and Spanish, matching the LANGUAGE RULE work of
task 9.A.5). On a match, the middleware short-circuits with a canonical
refusal and ends the turn — no model call, no tokens spent on a request
that's going to be blocked anyway.

Implementation note — why ``before_model``, not ``wrap_model_call``
(the roadmap's literal wording for this task):
``ROADMAP-2026-08.md`` describes this as
``GuardrailsMiddleware.wrap_model_call`` combined with ``jump_to="end"``.
That combination does not exist: ``langchain.agents.factory`` explicitly
raises ``NotImplementedError`` if a ``wrap_model_call`` handler's
``Command`` carries a ``goto`` — its own error message says so verbatim:
"Command goto is not yet supported in wrap_model_call middleware. Use
the jump_to state field with before_model/after_model hooks instead."
(Confirmed by reading ``langchain/agents/factory.py`` directly, not
assumed — the same kind of mismatch that caused this repo's bug B1,
see hard rule #2 in ``AGENTS.md``.) ``before_model`` is the right hook
here anyway: it runs *before* the model call (like ``after_model``, but
without first paying for the LLM turn it's about to discard), exactly
mirroring the existing, tested pattern in
:class:`~src.agent.middleware.MaxTurnsMiddleware`.

Implementation note — why no ``abefore_model`` override: unlike
``wrap_model_call``/``wrap_tool_call`` (function-handler composition,
see ``router_middleware.py`` and ``middleware.py`` — both hooks
genuinely require separate sync/async implementations or raise
``NotImplementedError`` in the mismatched mode), ``before_model`` and
``after_model`` are plain LangGraph nodes wrapped in a
``RunnableCallable(sync_fn, async_fn)``, which transparently supports
running a sync-only implementation under ``.ainvoke()``/``.astream()``.
Verified empirically (not assumed) with a fake model that raises if
invoked: a sync-only ``before_model`` with ``jump_to="end"`` correctly
short-circuits under async invocation and the model is never called.
This mirrors ``MaxTurnsMiddleware``, which also defines only the sync
``after_model``.
"""

import logging
import re
import unicodedata
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

CANONICAL_INJECTION_REFUSAL = (
    "No puedo seguir instrucciones que intenten cambiar mi rol, ignorar mis "
    "reglas o revelar mis instrucciones internas. Sigo aquí para ayudarte "
    "con tu orientación vocacional — ¿en qué te puedo ayudar?"
)


def _strip_accents(text: str) -> str:
    """Normalize accented Spanish characters to their plain-ASCII base.

    ``"Muéstrame"`` -> ``"Muestrame"``, ``"actúa"`` -> ``"actua"``. Without
    this, every pattern below would need an accented AND unaccented variant
    to catch real user input (accents are frequently dropped or typo'd,
    and this was a real, caught-by-testing bug: the first version of
    ``_INJECTION_PATTERNS`` used a literal ``"muestra(me)?"``, which does
    not match ``"Muéstrame"`` — the actual word Spanish speakers use — since
    regex ``e`` never matches ``é``). Patterns below are written in their
    unaccented form to match text after this normalization.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


# Bilingual, case-insensitive, accent-insensitive (see _strip_accents).
# Deliberately specific phrasing (e.g. "actua como si (no tuvieras|fueras)"
# rather than a bare "actua como") to avoid flagging legitimate requests
# like "actúa como un consejero" — false positives on the 30 legitimate
# eval cases are a hard DoD item (ROADMAP-2026-08.md, Sprint 9 DoD).
_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"ignor[ae]\s+(todas\s+)?las\s+instrucciones\s+(anteriores|previas)",
        r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions",
        r"disregard\s+(all\s+)?(previous|prior)\s+(instructions|rules)",
        r"olvida\s+que\s+eres",
        r"forget\s+(that\s+)?you\s+are\s+(an?|spark)",
        r"eres\s+ahora\s+(dan\b|(un\s+)?modo\s+desarrollador)",
        r"you\s+are\s+now\s+(dan\b|in\s+developer\s+mode)",
        r"\bdo\s+anything\s+now\b",
        r"muestra(me)?\s+tu\s+(system\s+prompt|las\s+instrucciones\s+internas)",
        r"reveal\s+your\s+(system\s+prompt|internal\s+instructions)",
        r"repite\s+(tu|el)\s+system\s+prompt",
        r"repeat\s+your\s+system\s+prompt",
        r"actua\s+como\s+si\s+(no\s+tuvieras|fueras)",
        r"act\s+as\s+if\s+you\s+(have\s+no|are)\s+(restrictions|unrestricted|unfiltered|jailbroken)",
        r"nuevo\s+system\s+prompt\s*:",
        r"new\s+system\s+prompt\s*:",
    )
)


def _last_human_message_text(state: AgentState) -> str:
    """Return the content of the most recent ``HumanMessage`` in state.

    Returns an empty string if there is none yet (first ``before_model``
    call of a turn where the graph was invoked with no human input, e.g.
    a resumed tool loop) — an empty string never matches any pattern.
    """
    messages = state.get("messages", [])
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            content = message.content
            return content if isinstance(content, str) else str(content)
    return ""


def detect_prompt_injection(text: str) -> str | None:
    """Return the matched pattern's source string, or ``None`` if clean.

    Exposed as a module-level function (not just a private method) so
    tests can exercise the heuristic directly, without building a full
    agent graph for every phrase in the pattern list.
    """
    normalized = _strip_accents(text)
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(normalized):
            return pattern.pattern
    return None


class GuardrailsMiddleware(AgentMiddleware):
    """Blocks prompt-injection / jailbreak attempts before the model runs.

    Inspects the last ``HumanMessage`` against :data:`_INJECTION_PATTERNS`.
    On a match, ends the turn immediately with
    :data:`CANONICAL_INJECTION_REFUSAL` — the model is never invoked (see
    the module docstring for the ``before_model``-vs-``wrap_model_call``
    verification).
    """

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        """Check the latest user turn; short-circuit on an injection match."""
        text = _last_human_message_text(state)
        matched_pattern = detect_prompt_injection(text)
        if matched_pattern is None:
            return None

        logger.warning(
            "guardrail_blocked reason=prompt_injection pattern=%r",
            matched_pattern,
        )
        return {
            "messages": [AIMessage(content=CANONICAL_INJECTION_REFUSAL)],
            "jump_to": "end",
        }


__all__ = [
    "CANONICAL_INJECTION_REFUSAL",
    "GuardrailsMiddleware",
    "detect_prompt_injection",
]
