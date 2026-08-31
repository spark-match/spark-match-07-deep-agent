"""Model router middleware (Sprint 8, task 8.4).

Routes each model call to a cheaper/faster model (Haiku, ``settings.fast_model_id``)
for simple turns (greetings, chitchat, short assessment replies, low-information
clarifications) and to the strong model (Sonnet, ``settings.model_id``) for
everything else — the "intent router" pattern ROADMAP-2026-08.md attributes
to the POC v2 (measured there at -26% latency, -44% cost with 38% Haiku
coverage).

Classification is a pure heuristic (:func:`src.agent.intent.classify_intent`)
with no extra LLM call, so routing itself adds no latency or cost.

The routing decision is logged as a single greppable line
(``intent_route intent=... model=...``). This is intentionally *not* a
metrics pipeline (dashboards, aggregation) — that is Sprint 11's
observability scope; a log line is the only concrete requirement this
task's DoD states ("métrica intent_route emitida").

Both the sync (``wrap_model_call``) and async (``awrap_model_call``) hooks
are implemented. Neither has a default cross-implementation in
``langchain.agents.middleware`` (unlike, say, ``BaseChatModel``'s
``_agenerate``/``_generate``) — defining only one raises
``NotImplementedError`` for every model call made in the other mode. The
production API (``ag-ui-langgraph``) drives the graph exclusively via
``astream``/``ainvoke``, so the async hook is required, not optional
(same reasoning already documented for ``AssessmentOnceMiddleware`` in
``src/agent/middleware.py``).
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.language_models.chat_models import BaseChatModel

from src.agent.intent import FAST_INTENTS, classify_intent

logger = logging.getLogger(__name__)


def _model_label(model: BaseChatModel) -> str:
    """Best-effort human-readable identifier for the ``intent_route`` log.

    Real Bedrock models expose ``model_id`` (the exact allowlisted model
    string); test fakes (``GenericFakeChatModel``) don't, so fall back to
    ``_llm_type`` — a property every ``BaseChatModel`` subclass must
    implement, unlike ``model_id``.
    """
    return getattr(model, "model_id", None) or model._llm_type


class IntentRouterMiddleware(AgentMiddleware[Any, Any, Any]):
    """Routes simple turns to a fast model and complex turns to the strong one.

    Args:
        fast_model: Model used when :func:`classify_intent` returns a value
            in :data:`~src.agent.intent.FAST_INTENTS`.
        strong_model: Model used for everything else (the "complex" default
            and any future intent this heuristic doesn't recognize).
    """

    def __init__(self, fast_model: BaseChatModel, strong_model: BaseChatModel) -> None:
        self._fast = fast_model
        self._strong = strong_model

    def _route(self, request: ModelRequest[Any]) -> tuple[str, BaseChatModel]:
        """Classify the request's messages and pick the model for this call."""
        intent = classify_intent(request.messages)
        model = self._fast if intent in FAST_INTENTS else self._strong
        logger.info("intent_route intent=%s model=%s", intent, _model_label(model))
        return intent, model

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """Classify the turn and route it to the fast or strong model."""
        _intent, model = self._route(request)
        return handler(request.override(model=model))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """Async counterpart of :meth:`wrap_model_call` — same routing logic."""
        _intent, model = self._route(request)
        return await handler(request.override(model=model))


__all__ = ["IntentRouterMiddleware"]
