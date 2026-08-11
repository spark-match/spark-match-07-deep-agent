"""Memory middlewares — hydration, background persistence, and seeding.

Three ``AgentMiddleware``s that turn the langmem profile manager and the
per-user memory-files backend into an actual working long-term memory
system (Sprint 6, tasks 6.C/6.D/6.E):

- :class:`ProfileHydrationMiddleware` — ``wrap_model_call``: reads the
  previously-extracted ``StudentProfile`` from the store (if any) and
  appends it to the request's **system message** so the model doesn't
  re-ask what it already knows. Deliberately not a message appended to
  state — see the class docstring for the Bedrock failure that caused.
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

from src.agent.pii import redact_messages
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


def _render_if_present(items: list[Any]) -> str | None:
    """Render the first stored profile, or ``None`` when there is nothing yet."""
    if not items:
        return None
    profile = items[0].value
    if not isinstance(profile, dict) or not profile:
        return None
    return _render_profile_block(profile)


def _with_extra_system_text(request: Any, block: str | None) -> Any:
    """Return ``request`` with ``block`` appended to its system message.

    ``ModelRequest`` is frozen, so this goes through ``override``. A
    ``None`` block returns the request untouched — a student with no
    extracted profile pays nothing.
    """
    if block is None:
        return request

    existing = request.system_message
    existing_text = getattr(existing, "content", "") if existing is not None else ""
    combined = f"{existing_text}\n\n{block}" if existing_text else block
    return request.override(system_message=SystemMessage(content=combined))


class ProfileHydrationMiddleware(AgentMiddleware):
    """Puts the previously-extracted ``StudentProfile`` in the system prompt.

    The profile goes into ``ModelRequest.system_message``, not into the
    message list, and that distinction is the whole point.

    The original implementation appended a ``SystemMessage`` to state from
    ``before_agent``. It worked right up until a student actually had a
    profile, and then every turn died before the model:

        ValueError: Received multiple non-consecutive system messages.
        During task with name 'model'

    ``langchain_aws``'s Anthropic adapter refuses a message list whose
    system messages are not contiguous at the front, and appending puts the
    profile *after* the human turn — behind the agent's own system prompt,
    with conversation in between. Worse, appending to state means the block
    is checkpointed, so from the second turn on it also sits in the middle
    of the persisted history.

    Writing to ``system_message`` is both correct and narrower: nothing is
    persisted, nothing can end up out of order, and the profile reaches the
    model as what it always was — instructions, not conversation.
    """

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        block = await self._profile_block_async(request.runtime)
        return await handler(_with_extra_system_text(request, block))

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        block = self._profile_block_sync(request.runtime)
        return handler(_with_extra_system_text(request, block))

    def _profile_block_sync(self, runtime: Runtime[Any]) -> str | None:
        store = runtime.store
        if store is None:
            return None
        user_id = get_user_id(runtime)
        return _render_if_present(store.search(("spark-match", user_id, "profile"), limit=1))

    async def _profile_block_async(self, runtime: Runtime[Any]) -> str | None:
        store = runtime.store
        if store is None:
            return None
        user_id = get_user_id(runtime)
        items = await store.asearch(("spark-match", user_id, "profile"), limit=1)
        return _render_if_present(items)


def _avisar_si_la_extraccion_falla(futuro: Any, user_id: str) -> None:
    """Cuelga del future un log para cuando la extraccion se caiga.

    ``ReflectionExecutor.submit`` devuelve un ``Future`` y guarda ahi dentro
    la excepcion si el trabajo revienta (``task.future.set_exception``). Nadie
    llama nunca a ``.result()``, asi que hasta ahora esa excepcion se moria
    donde nacia: la extraccion de perfil podia estar fallando en cada uno de los
    turnos y el servicio se veia perfectamente sano.

    No es teoria. Asi se paso desapercibido que el extractor volvia troceado
    por ``max_tokens`` (ver :func:`src.memory.profile_manager
    ._modelo_de_extraccion`): el sintoma que llego fue "no me deja generar el
    informe", a cuatro capas de distancia de la causa, y hubo que sacarlo de
    las trazas de LangSmith porque en el log del servicio no habia ni una
    linea. Con esto, la proxima vez lo dice el propio servicio.

    Solo observa: no reintenta ni propaga. El turno del estudiante ya termino
    y su perfil se completara en el siguiente -- convertir esto en un error
    visible para el seria cambiar un fallo silencioso por uno ruidoso e
    igual de inutil para quien esta conversando.
    """
    if not hasattr(futuro, "add_done_callback"):  # pragma: no cover - executor ajeno
        return

    def _mirar(f: Any) -> None:
        # `cancelled()` primero, y no un `try/except` alrededor de
        # `exception()`: en un future cancelado, `exception()` lanza
        # `CancelledError`, y de que hereda esa clase depende de la version.
        # En 3.8 se alias a la de `asyncio`, que cuelga de `BaseException`;
        # en la 3.14 que corremos vuelve a ser una clase propia bajo
        # `Exception` (comprobado, no supuesto). Preguntar por el estado no
        # depende de eso: si algun dia el arbol vuelve a moverse, la
        # excepcion no se escapa por el callback hacia quien llamo a
        # `cancel()` -- que es el `shutdown(cancel_futures=True)` del apagado.
        if f.cancelled():
            return
        fallo = f.exception()
        if fallo is not None:
            logger.error(
                "La extraccion de perfil fallo para user_id=%r; su completitud se "
                "queda como estaba y la puerta del informe puede rechazarle",
                user_id,
                exc_info=fallo,
            )

    futuro.add_done_callback(_mirar)


class ProfilePersistMiddleware(AgentMiddleware):
    """Encodes the turn into the background ``StudentProfile`` extraction.

    ``executor`` is the return value of
    :func:`src.memory.build_reflection_executor`, built once per graph
    (needs a real store). Pass ``None`` (e.g. when the graph was built
    without a store, as in most unit tests) to disable it — ``after_agent``
    becomes a no-op.

    Sprint 9, task 9.A.2: messages are redacted via
    :func:`src.agent.pii.redact_messages` before submission, so the
    background extraction model never sees a student's raw email, phone,
    or DNI number, even if the ``StudentProfile`` extraction itself has
    no reason to persist those fields.
    """

    def __init__(self, executor: Any | None = None) -> None:
        super().__init__()
        self._executor = executor

    def after_agent(self, state: AgentState, runtime: Runtime[Any]) -> dict[str, Any] | None:
        self._submit(state, runtime)
        return None

    # Si, el cuerpo es identico al de `after_agent`, y tiene que serlo.
    # LangChain elige uno u otro segun invoques el grafo con `invoke` o con
    # `ainvoke`, asi que el middleware esta obligado a ofrecer los dos. Lo
    # unico que hacen es delegar en `_submit`, que ya es no bloqueante -- deja
    # el trabajo en un executor y vuelve -- de modo que la version async no
    # tiene nada que esperar. Unificarlos exigiria que el camino sincrono
    # arrancara un bucle de eventos para nada.
    async def aafter_agent(  # NOSONAR
        self, state: AgentState, runtime: Runtime[Any]
    ) -> dict[str, Any] | None:
        self._submit(state, runtime)
        return None

    def _submit(self, state: AgentState, runtime: Runtime[Any]) -> None:
        if self._executor is None:
            return
        user_id = get_user_id(runtime)
        settings = get_settings()
        futuro = self._executor.submit(
            {"messages": redact_messages(state.get("messages", []))},
            config={"configurable": {"user_id": user_id}},
            after_seconds=settings.reflection_delay_seconds,
        )
        _avisar_si_la_extraccion_falla(futuro, user_id)


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
