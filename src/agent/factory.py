"""Agent factory - assembles the Spark Match Deep Agent.

Creates the coordinator graph with subagents, memory, and tools.
"""

from collections.abc import Sequence
from typing import Any, cast

from deepagents import create_deep_agent
from deepagents.middleware.subagents import SubAgent
from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph.state import CompiledStateGraph
from langgraph.store.base import BaseStore
from langmem import create_manage_memory_tool, create_search_memory_tool

from src.agent.backends import SKILLS_ROOT, build_backend
from src.agent.content_filter import ContentFilterMiddleware
from src.agent.guardrails import GuardrailsMiddleware
from src.agent.memory_middleware import (
    MemorySeedMiddleware,
    ProfileHydrationMiddleware,
    ProfilePersistMiddleware,
)
from src.agent.middleware import AssessmentOnceMiddleware, MaxTurnsMiddleware
from src.agent.pii import PIIRedactionMiddleware
from src.agent.router_middleware import IntentRouterMiddleware
from src.agent.subagent_events import SubagentEventsMiddleware
from src.agent.subagents import (
    ASSESSMENT_SUBAGENT,
    MATCHING_SUBAGENT,
    PLANNING_SUBAGENT,
)
from src.auth.context import AgentContext
from src.config import get_settings
from src.prompts import SYSTEM_PROMPT
from src.tools import (
    calculate_affinity,
    evaluate_riasec_profile,
    search_careers,
    web_search,
)

# Preference namespace: langmem substitutes "{user_id}" from
# config["configurable"]["user_id"] at call time — set from the validated
# JWT since Sprint 7 (src.api.app.ag_ui_endpoint), src.agent.user_context
# ("local-user") for direct graph invocation without going through /ag-ui.
PREFS_NAMESPACE = ("spark-match", "{user_id}", "prefs")


def _resolve_model(model: str | BaseChatModel, *, max_tokens: int) -> BaseChatModel:
    """Resolve a model spec to a ``BaseChatModel``, applying ``max_tokens``.

    ``BaseChatModel`` instances (test fakes, e.g. ``GenericFakeChatModel``)
    pass through unchanged — ``max_tokens`` only applies to string specs
    resolved here.

    This mirrors deepagents' own model resolution
    (``deepagents._models.resolve_model``, re-exported without ``__all__``
    from ``deepagents.graph``, hence "mirrors" rather than "wraps": its
    signature is ``resolve_model(model) -> BaseChatModel`` with no kwargs
    parameter to thread ``max_tokens`` through) for the ``BaseChatModel``
    passthrough and the string -> ``init_chat_model`` resolution, but
    deliberately does **not** replicate its provider-profile step
    (``deepagents.profiles``, a beta API): its only built-in registrations
    today are OpenAI's Responses API default and OpenRouter attribution
    headers, neither of which applies to this project (Bedrock only) —
    confirmed empirically, not assumed:
    ``apply_provider_profile("bedrock:...", {"max_tokens": 2048})``
    returns ``{"max_tokens": 2048}`` unchanged, i.e. a no-op passthrough
    for every spec this project actually uses.
    """
    if isinstance(model, BaseChatModel):
        return model
    return init_chat_model(model, max_tokens=max_tokens)


def create_spark_agent(
    model: str | BaseChatModel | None = None,
    fast_model: str | BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    store: BaseStore | None = None,
    reflection_executor: Any | None = None,
    enable_rubric: bool = False,
) -> CompiledStateGraph[Any, Any, Any, Any]:
    """Create and configure the Spark Match Deep Agent.

    Returns a compiled LangGraph state graph ready for invocation or streaming.

    Args:
        model: Override for the strong chat model. Accepts a Bedrock model
            string or a pre-built ``BaseChatModel`` (e.g.
            ``GenericFakeChatModel`` in tests). Defaults to
            ``settings.model_string`` when omitted, so production callers
            are unaffected.
        fast_model: Override for the fast chat model used by
            ``IntentRouterMiddleware`` (Sprint 8, task 8.4) for simple
            turns. Defaults to ``settings.fast_model_string`` when
            omitted. Tests that only override ``model`` get the *same*
            instance for both — routing still exercises the middleware,
            it just never needs to reach a real fast model, since there's
            only one fake to route to either way.
        checkpointer: Short-term memory (per-``thread_id`` conversation
            turns). Built by :func:`src.persistence.build_persistence` in
            the FastAPI lifespan. ``None`` in tests that don't exercise
            multi-turn persistence — the graph still compiles and runs a
            single turn fine without it.
        store: Long-term memory (profile, preferences, memory files),
            partitioned per ``user_id`` from the validated JWT since Sprint 7
            (``src.auth``/``src.api.app``).
            Same ``None``-safe behavior as ``checkpointer``.
        reflection_executor: Background ``StudentProfile`` extraction
            worker (:func:`src.memory.build_reflection_executor`).
            Deliberately **not** built inside this factory: its
            ``LocalReflectionExecutor`` spawns a non-daemon worker thread on
            construction that only ``.shutdown()`` can stop, so its
            lifecycle must be owned by whoever can guarantee a matching
            shutdown call (the FastAPI lifespan in production; nothing in
            most tests, hence the ``None`` default). Ignored when ``store``
            is ``None`` — there's nowhere durable to persist into anyway.
        enable_rubric: When ``True``, wires deepagents' ``RubricMiddleware``
            (Sprint 9, task 9.B.5) into the stack so callers can supply a
            ``rubric`` on invocation state to drive self-evaluated
            iteration against the rubric. Default ``False`` because the
            middleware is ``.. beta::`` in deepagents 0.6.12 (unstable API)
            and adds an LLM-call-per-turn cost when activated. See
            ``docs/rubric-middleware-evaluation.md`` for the full
            rationale (does NOT substitute the post-loop
            ``SparkMatchJudge``; only complements it in production).

    Architecture:
    - Coordinator (this agent): routes user intent, manages conversation flow.
    - Assessment subagent: administers the RIASEC questionnaire conversationally.
    - Matching subagent: calculates affinity and ranks careers.
    - Planning subagent: generates personalized action plans.

    Memory (Sprint 6):
    - ``/memories/AGENTS.md`` is seeded per user on first contact and is
      readable/writable by the agent itself via the composite backend.
    - langmem extracts StudentProfile from conversations in the background
      and injects it back into the system prompt on later turns.
    - ``manage_prefs``/``search_memory`` tools let the agent record & recall
      user preferences (language, tone) directly.

    Skills (Sprint 8, task 8.3):
    - ``deepagents``' ``SkillsMiddleware`` loads ``SKILL.md`` files from
      ``/skills/`` (routed by ``build_backend()`` to a ``FilesystemBackend``
      scoped to the repo's ``skills/`` directory — see
      ``src/agent/backends.py`` for the security rationale) and injects
      their name/description into the system prompt. The model reads a
      skill's full content on demand via ``read_file`` when its
      description matches the current task (progressive disclosure).
      ``skills/vocational_advisor/SKILL.md`` is the first skill exposed
      this way — previously dead content, never loaded by the agent.

    Model routing (Sprint 8, task 8.4):
    - ``IntentRouterMiddleware`` classifies each turn with a pure heuristic
      (:func:`src.agent.intent.classify_intent`, no extra LLM call) and
      swaps the model used for that call between ``fast_model`` (Haiku —
      greetings, chitchat, short assessment replies, low-information
      clarifications) and ``model`` (Sonnet — everything else, including
      any turn the heuristic doesn't recognize). See
      ``src/agent/router_middleware.py``.
    - Both models are capped at ``settings.max_tokens`` (Sprint 8, task
      8.6 — POC v2 lesson 9: bounding generation length reduces latency
      on plan-generation turns). Applied when resolving a string spec
      (production); ignored for ``BaseChatModel`` overrides (test fakes),
      which don't consume it.

    Guardrails (Sprint 9, tasks 9.A.1/9.A.3):
    - ``GuardrailsMiddleware`` runs first (``before_model``) on every turn
      and blocks prompt-injection / jailbreak attempts before the model is
      ever invoked — no tokens spent on a request that's going to be
      refused anyway. See ``src/agent/guardrails.py`` for the pattern list
      and for why this uses ``before_model`` rather than the
      ``wrap_model_call`` hook the roadmap's wording literally suggests
      (that combination doesn't exist — ``jump_to`` isn't supported by
      ``wrap_model_call``, only by ``before_model``/``after_model``).
    - ``ContentFilterMiddleware`` runs second (also ``before_model``,
      chained after ``GuardrailsMiddleware`` — cheaper checks first) and
      classifies the turn with a Haiku call (Option A from the roadmap —
      confirmed with the user; Option B needs an IAM permission that
      doesn't exist), fail-open on any classifier error. See
      ``src/agent/content_filter.py``.

    PII redaction (Sprint 9, task 9.A.2):
    - ``PIIRedactionMiddleware`` strips email/phone/DNI occurrences from
      the ``manage_memory`` tool's ``content`` argument before a
      preference is actually written to the store.
    - Separately, ``ProfilePersistMiddleware`` (Sprint 6) now redacts the
      same categories from every message before submitting the
      conversation to the background ``StudentProfile`` extractor — see
      ``src/agent/pii.py`` for both integration points and the explicit
      non-goal note (``/memories/AGENTS.md`` writes via deepagents' own
      filesystem tools are not covered).

    Visibilidad de la delegacion:
    - ``SubagentEventsMiddleware`` emite un par de eventos custom alrededor
      de cada llamada a la herramienta ``task``, que es como deepagents
      expone a los tres subagentes. Sin eso la delegacion es invisible
      desde fuera: al navegador solo le llega una tool call generica y el
      subagente entero corre en silencio dentro de ella. Ver
      ``src/agent/subagent_events.py``.

    The coordinator decides when to delegate:
    - Quiero descubrir mi perfil -> assessment subagent
    - Que carreras me convienen -> matching subagent
    - Dame un plan para llegar a X -> planning subagent
    - General questions -> coordinator handles directly
    """
    settings = get_settings()

    # Resolve both models up front (BaseChatModel, not bare strings): the
    # router middleware assigns whichever one it picks directly onto
    # ModelRequest.model, so both must already be real model instances by
    # the time IntentRouterMiddleware is constructed below. Sprint 8, task
    # 8.6: max_tokens is threaded through both (see _resolve_model).
    strong_model = _resolve_model(
        model if model is not None else settings.model_string,
        max_tokens=settings.max_tokens,
    )
    # Precedencia del modelo rapido, de mas especifico a menos: el que se pase
    # explicitamente; si no, el `model` general -- para que quien pasa un solo
    # override lo vea aplicado a los dos y no se le cuele el de settings por
    # detras; y solo si tampoco hay, el de settings.
    if fast_model is not None:
        fast_model_choice = fast_model
    elif model is not None:
        fast_model_choice = model
    else:
        fast_model_choice = settings.fast_model_string

    fast_model_resolved = _resolve_model(fast_model_choice, max_tokens=settings.max_tokens)

    # SubAgent is a TypedDict; mypy sees plain dict[str, Sequence[object]] from
    # the imported constants, so we cast to satisfy the SubAgent contract.
    subagents: Sequence[SubAgent] = cast(
        "Sequence[SubAgent]",
        [ASSESSMENT_SUBAGENT, MATCHING_SUBAGENT, PLANNING_SUBAGENT],
    )

    manage_prefs = create_manage_memory_tool(
        namespace=PREFS_NAMESPACE,
        actions_permitted=("create", "update"),  # no delete: avoid accidental wipes
    )
    search_memory = create_search_memory_tool(namespace=PREFS_NAMESPACE)
    # Both tools resolve "{user_id}" from config["configurable"]["user_id"]
    # at call time. Since Sprint 7, src.api.app.ag_ui_endpoint always sets
    # that key from the validated JWT before invoking the graph, so this
    # works end-to-end for real requests. Direct graph invocation without
    # going through /ag-ui (most unit tests) never sets it — langmem then
    # raises a clear ConfigurationError instead of silently corrupting a
    # namespace, which is why factory/memory-middleware unit tests avoid
    # actually calling these two tools.

    # deepagents' own memory=[...] middleware eagerly downloads the listed
    # paths through the backend on every turn, which needs a real store
    # behind the /memories/ route. Without one (store=None, e.g. most unit
    # tests) it would AttributeError trying to read from a None store, so
    # both the memory-seeded system prompt and our own memory middlewares
    # are opt-in on store being present.
    memory_sources = ["/memories/AGENTS.md"] if store is not None else None
    middleware: list[Any] = [
        # Primero de la lista, o sea el mas externo de los que envuelven
        # llamadas a herramientas: asi la duracion que reporta es la que el
        # estudiante espera de verdad, con la redaccion de PII y el guard de
        # assessment ya incluidos dentro. No tiene hooks de modelo, asi que
        # ponerlo aqui no altera el orden de Guardrails -> ContentFilter.
        SubagentEventsMiddleware(),
        GuardrailsMiddleware(),
        ContentFilterMiddleware(classifier_model=fast_model_resolved),
    ]
    if store is not None:
        middleware.append(MemorySeedMiddleware())
        middleware.append(ProfileHydrationMiddleware())
    middleware.append(MaxTurnsMiddleware())
    middleware.append(AssessmentOnceMiddleware())
    middleware.append(PIIRedactionMiddleware())
    if enable_rubric:
        # Sprint 9, task 9.B.5. Wired AFTER MaxTurnsMiddleware /
        # PIIRedactionMiddleware so the grader sub-agent runs with the
        # token budget already bounded and on redacted (PII-safe)
        # content. No-op when the caller doesn't supply a ``rubric`` on
        # invocation state, so production callers that don't use it pay
        # zero per-turn cost -- only the model instantiation cost
        # (which is amortized across requests). See
        # docs/rubric-middleware-evaluation.md SS4-5 for the rationale.
        from deepagents.middleware import RubricMiddleware

        middleware.append(RubricMiddleware(model=strong_model))
    middleware.append(
        IntentRouterMiddleware(fast_model=fast_model_resolved, strong_model=strong_model)
    )
    if store is not None:
        middleware.append(ProfilePersistMiddleware(reflection_executor))

    agent = create_deep_agent(
        model=strong_model,
        tools=[
            evaluate_riasec_profile,
            search_careers,
            calculate_affinity,
            web_search,
            manage_prefs,
            search_memory,
        ],
        subagents=subagents,
        system_prompt=SYSTEM_PROMPT,
        name=settings.agent_name,
        backend=build_backend(),
        skills=[SKILLS_ROOT],
        memory=memory_sources,
        checkpointer=checkpointer,
        store=store,
        middleware=middleware,
        context_schema=AgentContext,
    )

    return agent  # noqa: RET504
