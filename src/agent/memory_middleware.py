"""Memory middlewares — hydration, background persistence, and seeding.

Three ``AgentMiddleware``s that turn the langmem profile manager and the
per-user memory-files backend into an actual working long-term memory
system (Sprint 6, tasks 6.C/6.D/6.E):

- :class:`ProfileHydrationMiddleware` — ``before_agent``: reads the
  previously-extracted ``StudentProfile`` from the store (if any) and
  injects it as a ``SystemMessage`` so the model doesn't re-ask what it
  already knows.
- :class:`ProfilePersistMiddleware` — ``after_agent``: submits the
  conversation to the background reflection executor, which extracts /
  updates the ``StudentProfile`` in the store without blocking the turn.
- :class:`MemorySeedMiddleware` — ``before_agent``: writes
  ``/memories/AGENTS.md`` (from :data:`src.prompts.USER_MEMORY_SEED`) the
  first time a user's memory-files namespace is empty. Idempotent:
  ``StoreBackend.write`` returns an (ignored) error result rather than
  raising when the file already exists.

All three are ``None``-safe when ``runtime.store`` is absent (e.g. tests
that build the graph without a store) or when no reflection executor was
constructed (store-less ``create_spark_agent`` calls) — they simply no-op.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from deepagents.backends import StoreBackend
from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import SystemMessage

from src.agent.user_context import get_user_id
from src.config import get_settings
from src.prompts import USER_MEMORY_SEED

if TYPE_CHECKING:
    from langgraph.runtime import Runtime

logger = logging.getLogger(__name__)

MEMORY_SEED_FILENAME = "AGENTS.md"


def _memory_files_backend(runtime: Runtime[Any], store: Any) -> StoreBackend:
    """Build a ``StoreBackend`` scoped to this run's per-user files namespace.

    Mirrors :func:`src.agent.backends._memory_namespace` so seeding writes
    to the exact same namespace the ``/memories/`` route reads from.
    """
    user_id = get_user_id(runtime)
    return StoreBackend(store=store, namespace=lambda _rt: ("spark-match", user_id, "files"))


def _render_profile_block(profile: dict[str, Any]) -> str:
    """Render the stored ``StudentProfile`` dict as a system-prompt block."""
    lines = [f"- {key}: {value}" for key, value in profile.items() if value is not None]
    body = "\n".join(lines) if lines else "(sin datos todavía)"
    return (
        "## Perfil vocacional ya conocido de este estudiante\n\n"
        f"{body}\n\n"
        "No vuelvas a preguntar lo que ya está aquí; confírmalo solo si el "
        "estudiante lo contradice."
    )


class ProfileHydrationMiddleware(AgentMiddleware):
    """Injects the previously-extracted ``StudentProfile`` into the turn."""

    async def abefore_agent(
        self, state: AgentState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        return await self._hydrate(runtime)

    def before_agent(self, state: AgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        store = runtime.store
        if store is None:
            return None
        user_id = get_user_id(runtime)
        items = store.search(("spark-match", user_id, "profile"), limit=1)
        return self._build_update(items)

    async def _hydrate(self, runtime: Runtime[Any]) -> dict[str, Any] | None:
        store = runtime.store
        if store is None:
            return None
        user_id = get_user_id(runtime)
        items = await store.asearch(("spark-match", user_id, "profile"), limit=1)
        return self._build_update(items)

    def _build_update(self, items: list[Any]) -> dict[str, Any] | None:
        if not items:
            return None
        profile = items[0].value
        if not isinstance(profile, dict) or not profile:
            return None
        return {"messages": [SystemMessage(content=_render_profile_block(profile))]}


class ProfilePersistMiddleware(AgentMiddleware):
    """Encodes the turn into the background ``StudentProfile`` extraction.

    ``executor`` is the return value of
    :func:`src.memory.build_reflection_executor`, built once per graph
    (needs a real store). Pass ``None`` (e.g. when the graph was built
    without a store, as in most unit tests) to disable it — ``after_agent``
    becomes a no-op.
    """

    def __init__(self, executor: Any | None = None) -> None:
        super().__init__()
        self._executor = executor

    def after_agent(self, state: AgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        self._submit(state, runtime)
        return None

    async def aafter_agent(self, state: AgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        self._submit(state, runtime)
        return None

    def _submit(self, state: AgentState, runtime: Runtime[Any]) -> None:
        if self._executor is None:
            return
        user_id = get_user_id(runtime)
        settings = get_settings()
        self._executor.submit(
            {"messages": state.get("messages", [])},
            config={"configurable": {"user_id": user_id}},
            after_seconds=settings.reflection_delay_seconds,
        )


class MemorySeedMiddleware(AgentMiddleware):
    """Seeds ``/memories/AGENTS.md`` the first time a user's namespace is empty."""

    async def abefore_agent(
        self, state: AgentState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        store = runtime.store
        if store is None:
            return None
        backend = _memory_files_backend(runtime, store)
        result = await backend.awrite(MEMORY_SEED_FILENAME, USER_MEMORY_SEED)
        if result.path is not None:
            logger.info("Seeded %s for user_id=%s", MEMORY_SEED_FILENAME, get_user_id(runtime))
        return None

    def before_agent(self, state: AgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        store = runtime.store
        if store is None:
            return None
        backend = _memory_files_backend(runtime, store)
        result = backend.write(MEMORY_SEED_FILENAME, USER_MEMORY_SEED)
        if result.path is not None:
            logger.info("Seeded %s for user_id=%s", MEMORY_SEED_FILENAME, get_user_id(runtime))
        return None


__all__ = [
    "MEMORY_SEED_FILENAME",
    "MemorySeedMiddleware",
    "ProfileHydrationMiddleware",
    "ProfilePersistMiddleware",
]
