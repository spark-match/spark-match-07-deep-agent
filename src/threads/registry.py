"""Per-user index of chat threads.

``src/auth/thread_guard.py`` already records who owns a thread, but that
record is deliberately shaped for one job: answering "is this caller
allowed to touch this derived id?" in a single O(1) lookup. It is keyed by
the *derived* thread id in a global namespace and holds nothing else, so
it cannot answer the question a chat sidebar actually asks — "what
conversations does this student have, most recent first?"

Two things block that. First, the namespace is global, so listing it would
walk every user's threads. Second, the derived id is a one-way
``sha256(user_id:client_thread_id)``: the server cannot recover the id the
client uses to address the conversation, so even a correct listing would
return values the frontend can't do anything with.

This module adds the missing half — a per-user, listable index that keeps
the client-side id, a human title and a last-activity timestamp. Ownership
enforcement stays where it is: this is an index, not a permission check,
and the two should not be confused. Every read here is still gated by
``assert_thread_ownership`` upstream.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langgraph.store.base import BaseStore

logger = logging.getLogger(__name__)

# Longest title kept from the opening message. Sized for a sidebar entry:
# long enough to tell two conversations about careers apart, short enough
# not to wrap.
MAX_TITLE_LENGTH = 60

DEFAULT_TITLE = "Nueva conversación"

# Marca en la entrada del indice: este titulo lo escribio el estudiante y el
# relleno automatico no lo toca. Ver `record_thread_activity`.
TITULO_DEL_USUARIO = "title_set_by_user"

# The store's `asearch` has no ordering guarantee, so "most recent first"
# has to be done here, which means reading the whole index before paging
# it. This caps that read. A student with more threads than this keeps
# every one of them -- nothing is deleted -- but the tail stops appearing
# in the sidebar, and the truncation is logged rather than silently
# pretending the list is complete.
MAX_INDEXED_THREADS = 500


def thread_index_namespace(user_id: str) -> tuple[str, ...]:
    """Namespace holding one entry per conversation owned by ``user_id``."""
    return ("spark-match", user_id, "threads")


def build_title(seed: str | None) -> str:
    """Turn the opening message into a sidebar label.

    Deliberately mechanical: the first thing the student typed, collapsed
    and truncated. Generating a nicer title with a model is possible (the
    fast model is already wired for the content filter) but it would put a
    second inference call in the path of every new conversation, and a
    truncated first message is what a student recognizes anyway.
    """
    if not seed:
        return DEFAULT_TITLE

    collapsed = " ".join(seed.split())
    if not collapsed:
        return DEFAULT_TITLE
    if len(collapsed) <= MAX_TITLE_LENGTH:
        return collapsed
    return collapsed[: MAX_TITLE_LENGTH - 1].rstrip() + "…"


async def record_thread_activity(
    store: BaseStore | None,
    user_id: str,
    thread_id: str,
    client_thread_id: str,
    title_seed: str | None = None,
) -> None:
    """Create or refresh this thread's index entry.

    Called on every turn: ``updated_at`` is what orders the sidebar, so it
    has to move even when nothing else about the conversation changed. The
    title is written once, on creation, and then left alone — a
    conversation whose label kept changing under the student as it went on
    would be worse than a slightly stale one.

    A ``None`` store makes this a no-op, matching
    :func:`~src.auth.thread_guard.assert_thread_ownership`: with no durable
    store there is nothing to index and nothing to list.
    """
    if store is None:
        return

    now = datetime.now(UTC).isoformat()
    namespace = thread_index_namespace(user_id)

    existing = await store.aget(namespace, thread_id)
    if existing is not None and isinstance(existing.value, dict):
        entry = dict(existing.value)
        entry["updated_at"] = now
        # Backfill for entries written before a title could be derived --
        # a turn that arrived with no readable text, say.
        #
        # `TITULO_DEL_USUARIO` es lo que salva un titulo escrito a mano. Sin
        # esa marca, comparar con DEFAULT_TITLE bastaria casi siempre y
        # fallaria justo en el caso mas facil de dar: el estudiante que
        # renombra su conversacion a "Nueva conversación" y ve como el
        # siguiente turno se lo pisa. Un caso raro, pero de los que no tienen
        # explicacion posible cuando pasan.
        if not entry.get(TITULO_DEL_USUARIO) and (
            not entry.get("title") or entry.get("title") == DEFAULT_TITLE
        ):
            entry["title"] = build_title(title_seed)
    else:
        entry = {
            "client_thread_id": client_thread_id,
            "title": build_title(title_seed),
            "created_at": now,
            "updated_at": now,
        }

    await store.aput(namespace, thread_id, entry)


class TituloInvalido(ValueError):
    """El título propuesto no sirve como etiqueta de conversación."""


def limpiar_titulo(propuesto: str) -> str:
    """Normaliza un título escrito por el estudiante, o falla.

    Mismo colapso de espacios y mismo tope que ``build_title``: el sidebar
    no distingue de dónde salió un título y no debería tener dos reglas de
    tamaño. Lo que cambia es qué se hace con lo que no vale — un título
    automático puede caer al genérico, pero un renombrado vacío es una
    equivocación de quien lo pidió y se le dice.

    Los caracteres de control se van antes de medir. Son invisibles, así
    que sin quitarlos un título de dos letras podría ocupar sesenta y
    llevarse el recorte por delante.
    """
    limpio = " ".join(propuesto.split())
    limpio = "".join(caracter for caracter in limpio if caracter.isprintable())

    if not limpio:
        raise TituloInvalido("El título no puede estar vacío.")
    if len(limpio) > MAX_TITLE_LENGTH:
        raise TituloInvalido(f"El título no puede pasar de {MAX_TITLE_LENGTH} caracteres.")
    return limpio


async def rename_thread(
    store: BaseStore | None,
    user_id: str,
    thread_id: str,
    titulo: str,
) -> dict[str, Any] | None:
    """Pone el título que escribió el estudiante. ``None`` si no hay hilo.

    No mueve ``updated_at``: ese campo ordena el sidebar por actividad de la
    conversación, y renombrar no es conversar. Si lo moviera, ponerle nombre
    a un hilo viejo lo mandaría a lo alto de la lista por encima de otro en
    el que se acaba de hablar.
    """
    if store is None:
        return None

    namespace = thread_index_namespace(user_id)
    existing = await store.aget(namespace, thread_id)
    if existing is None or not isinstance(existing.value, dict):
        return None

    entry = dict(existing.value)
    entry["title"] = limpiar_titulo(titulo)
    entry[TITULO_DEL_USUARIO] = True
    await store.aput(namespace, thread_id, entry)

    return _to_summary(thread_id, entry)


def _to_summary(key: str, value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    client_thread_id = value.get("client_thread_id")
    if not isinstance(client_thread_id, str) or not client_thread_id:
        # Written by an older version, or corrupt. Without the client-side
        # id the entry is unusable: the frontend could not reopen it.
        return None
    return {
        "thread_id": client_thread_id,
        "title": value.get("title") or DEFAULT_TITLE,
        "created_at": value.get("created_at", ""),
        "updated_at": value.get("updated_at", ""),
        "derived_thread_id": key,
    }


async def list_threads(
    store: BaseStore | None,
    user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return ``user_id``'s conversations, most recently active first."""
    if store is None:
        return []

    items = await store.asearch(thread_index_namespace(user_id), limit=MAX_INDEXED_THREADS)
    if len(items) >= MAX_INDEXED_THREADS:
        logger.warning(
            "thread_index_truncated user_id=%r cap=%d — older conversations are stored "
            "but will not appear in the listing",
            user_id,
            MAX_INDEXED_THREADS,
        )

    summaries = [s for item in items if (s := _to_summary(item.key, item.value)) is not None]
    summaries.sort(key=lambda entry: entry["updated_at"], reverse=True)
    return summaries[offset : offset + limit]


async def forget_thread(store: BaseStore | None, user_id: str, thread_id: str) -> None:
    """Drop the index entry and the ownership record for ``thread_id``.

    The ownership record goes too, deliberately. Leaving it behind would
    keep a derived id permanently claimed, so a student who deleted a
    conversation and then started a new one under the same client-side id
    would be met with a 403 on their own thread.
    """
    if store is None:
        return

    from src.auth.thread_guard import THREAD_OWNER_NAMESPACE

    await store.adelete(thread_index_namespace(user_id), thread_id)
    await store.adelete(THREAD_OWNER_NAMESPACE, thread_id)


__all__ = [
    "DEFAULT_TITLE",
    "MAX_INDEXED_THREADS",
    "MAX_TITLE_LENGTH",
    "TITULO_DEL_USUARIO",
    "TituloInvalido",
    "build_title",
    "forget_thread",
    "limpiar_titulo",
    "list_threads",
    "record_thread_activity",
    "rename_thread",
    "thread_index_namespace",
]
