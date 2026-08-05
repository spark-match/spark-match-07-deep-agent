"""Content-safety classifier guardrail (Sprint 9, task 9.A.3, Option A).

Classifies the last ``HumanMessage`` as safe/unsafe using a Haiku call
before the main model runs, blocking clearly harmful requests (violence,
self-harm, illegal activity, sexual content involving minors, hate
speech) the same way :class:`~src.agent.guardrails.GuardrailsMiddleware`
blocks prompt-injection attempts — a canonical refusal, no main-model
call, via ``before_model`` + ``jump_to="end"``.

Option A vs Option B (confirmed with the user before implementing, not
assumed): ``ROADMAP-2026-08.md`` presents two choices for this task.
Option B (AWS Bedrock Guardrails) needs the ``bedrock:ApplyGuardrail``
IAM permission, which does not exist today and would require a PR to
``spark-match-02-infrastructure`` — the same kind of external blocker
already documented for parts of Sprint 10 (AGENTS.md SS7.2, "no dupliques
esos pipelines aquí, se piden upstream"). Option A (this module) stays
100% portable: it only calls the already-allowlisted Haiku model, so the
local-dev-without-AWS-account requirement (AGENTS.md hard rule #7) still
holds — the classifier call itself needs AWS in production, exactly like
every other model call in this project, but adds no *new* AWS dependency
beyond what already exists.

Fail-open policy (confirmed with the user before implementing): if the
classifier call raises (network error, timeout) or returns unparseable
output, the turn proceeds normally rather than being blocked. Rationale:
this is a *secondary*, defense-in-depth guardrail — the pure-heuristic
``GuardrailsMiddleware`` (task 9.A.1) already blocks the most obvious
attacks with zero dependency on any LLM call succeeding. A classifier
hiccup is far more likely to be a transient infrastructure problem than
a sign of genuinely unsafe content, and fail-closed here would mean a
Haiku outage blocks every legitimate student in the product, which is a
worse failure mode for a vocational-guidance tool than occasionally
missing a content check during an outage.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from src.agent.message_utils import last_human_message_text

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)

# This classifier runs INSIDE the graph, so its tokens travel the same
# `astream_events` channel as the assistant's actual reply. ag_ui_langgraph
# turns every `on_chat_model_stream` event into AG-UI TEXT_MESSAGE_* events
# unless the run's metadata opts out:
#
#     should_emit_messages = (event.get("metadata") or {}).get("emit-messages", True)
#
# Without that opt-out the verdict — `{"safe": true, "reason": "..."}` —
# reaches the browser as a complete START/CONTENT/END message sequence,
# rendered as if the advisor had answered a student's question about their
# future with a JSON blob. Two of them per turn, before the real answer.
#
# A safety check is machinery, not conversation. It gets no voice.
_SILENT_CLASSIFIER_CONFIG: RunnableConfig = {
    "metadata": {"emit-messages": False, "emit-tool-calls": False},
    "tags": ["internal", "content-filter"],
}

CANONICAL_UNSAFE_CONTENT_REFUSAL = (
    "No puedo ayudarte con eso. Si estás pasando por una situación difícil, "
    "te recomiendo hablar con un profesional o una línea de ayuda de tu "
    "país. Sigo aquí para acompañarte en tu orientación vocacional si "
    "quieres continuar."
)

_CONTENT_FILTER_PROMPT = """You are a content-safety classifier for Spark Match, \
a vocational guidance assistant for students.

Classify the STUDENT MESSAGE below as safe or unsafe.

UNSAFE means the message:
- Requests help planning violence against people
- Expresses intent to self-harm, or asks for self-harm methods
- Requests help with clearly illegal activity unrelated to career guidance
- Contains sexual content involving minors
- Contains hate speech or content that dehumanizes a group

SAFE means everything else, INCLUDING messages that are simply off-topic \
for vocational guidance (that's a separate concern, handled elsewhere, not \
a safety one) and anything you are genuinely unsure about — when in doubt, \
classify as safe.

STUDENT MESSAGE:
{message}

OUTPUT FORMAT (strict JSON): {{"safe": true or false, "reason": "brief reason"}}
Respond with ONLY the JSON object, no prose.
"""


def _parse_classification(text: str) -> tuple[bool, str]:
    """Parse the classifier's JSON response into ``(is_safe, reason)``.

    Fails open (same policy as a raised exception — see the module
    docstring): malformed JSON or a missing ``"safe"`` key defaults to
    ``True``, never to blocking a turn on a parsing accident.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return True, f"fail-open: classifier did not return valid JSON: {text[:200]!r}"

    if not isinstance(data, dict):
        return True, f"fail-open: classifier JSON was not an object: {text[:200]!r}"

    safe = data.get("safe", True)
    reason = str(data.get("reason", "no reason provided"))
    return bool(safe), reason


def _build_block_update(reason: str) -> dict[str, Any]:
    logger.warning("guardrail_blocked reason=unsafe_content classifier_reason=%r", reason)
    return {
        "messages": [AIMessage(content=CANONICAL_UNSAFE_CONTENT_REFUSAL)],
        "jump_to": "end",
    }


class ContentFilterMiddleware(AgentMiddleware):
    """Blocks unsafe content before the main model runs (Sprint 9, 9.A.3).

    Args:
        classifier_model: The (already-resolved) model used to classify
            each turn — Haiku in production (``settings.fast_model_string``,
            the same model :class:`~src.agent.router_middleware.IntentRouterMiddleware`
            already routes simple turns to), any ``BaseChatModel`` fake in
            tests.

    Implements both ``before_model`` and ``abefore_model`` — unlike
    :class:`~src.agent.guardrails.GuardrailsMiddleware` (a pure heuristic,
    verified to work sync-only under async invocation because
    ``before_model``/``after_model`` are bridged automatically by
    LangGraph's ``RunnableCallable``), this middleware makes a real model
    call every turn, so a true async implementation
    (``await classifier_model.ainvoke(...)``) matters for not blocking the
    event loop, not just for correctness.
    """

    def __init__(self, classifier_model: BaseChatModel) -> None:
        self._classifier = classifier_model

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        text = last_human_message_text(state)
        if not text:
            return None
        try:
            response = self._classifier.invoke(
                _CONTENT_FILTER_PROMPT.format(message=text),
                config=_SILENT_CLASSIFIER_CONFIG,
            )
            safe, reason = _parse_classification(str(response.content))
        except Exception:
            logger.warning("content_filter_error text=%r", text[:200], exc_info=True)
            return None  # fail-open

        return None if safe else _build_block_update(reason)

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: AgentState, runtime: Runtime) -> dict[str, Any] | None:
        text = last_human_message_text(state)
        if not text:
            return None
        try:
            response = await self._classifier.ainvoke(
                _CONTENT_FILTER_PROMPT.format(message=text),
                config=_SILENT_CLASSIFIER_CONFIG,
            )
            safe, reason = _parse_classification(str(response.content))
        except Exception:
            logger.warning("content_filter_error text=%r", text[:200], exc_info=True)
            return None  # fail-open

        return None if safe else _build_block_update(reason)


__all__ = [
    "CANONICAL_UNSAFE_CONTENT_REFUSAL",
    "ContentFilterMiddleware",
]
