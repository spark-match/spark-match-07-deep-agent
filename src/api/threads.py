"""HTTP surface for chat sessions: list, read, delete.

``POST /ag-ui`` streams a turn and nothing else, which is enough for one
conversation held open in one tab and not enough for anything a chat
product does. Reload the page and the history is gone; open the sidebar
and there is nothing to list; start a conversation you regret and there
is no way to remove it. The data was always there — the checkpointer keeps
every turn and the store keeps the index — it just had no door.

Every route derives the effective thread id from the caller's own
``user_id`` and then re-checks ownership, exactly like the streaming
endpoint. A thread id is not a capability: knowing one grants nothing.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from src.auth import AuthContext, assert_thread_ownership, derive_thread_id, require_auth
from src.threads import (
    MAX_TITLE_LENGTH,
    TituloInvalido,
    active_run,
    forget_thread,
    list_threads,
    load_thread_messages,
    rename_thread,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/threads", tags=["threads"])


@router.get("")
async def get_threads(
    request: Request,
    auth: Annotated[AuthContext, Depends(require_auth)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    """List the caller's conversations, most recently active first."""
    threads = await list_threads(request.app.state.store, auth.user_id, limit=limit, offset=offset)
    # The derived id is an internal detail: it is the checkpointer key, and
    # the client addresses conversations by its own id.
    for thread in threads:
        thread.pop("derived_thread_id", None)
    return {"threads": threads}


@router.get("/{client_thread_id}/messages")
async def get_thread_messages(
    client_thread_id: str,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict[str, Any]:
    """Return one conversation's history, for rehydrating the chat view."""
    store = request.app.state.store
    thread_id = derive_thread_id(auth.user_id, client_thread_id)
    # Raises 403 on a mismatch. On a thread this caller never opened it
    # registers ownership instead of rejecting -- harmless, since the
    # history read below then returns an empty list.
    await assert_thread_ownership(store, thread_id, auth.user_id)

    messages = await load_thread_messages(request.app.state.graph, thread_id)
    # `running` es lo que separa «esta conversacion termino asi» de «hay una
    # respuesta escribiendose ahora mismo que todavia no esta en el
    # checkpoint». Sin el, quien vuelve a abrir una conversacion con un turno
    # en curso ve su pregunta sin respuesta y la vuelve a hacer -- que ademas
    # ahora se la rechazarian con un 409. Ver src/threads/lease.py.
    return {
        "thread_id": client_thread_id,
        "messages": messages,
        "running": await active_run(store, thread_id) is not None,
    }


class RenombrarConversacion(BaseModel):
    """Cuerpo de ``PATCH /threads/{id}``."""

    # El tope de verdad lo pone `limpiar_titulo`, que primero colapsa los
    # espacios: sin eso, sesenta espacios y una letra serian un titulo
    # rechazado por largo cuando en realidad mide uno. Aqui solo se acota lo
    # que se acepta leer, para no cargar en memoria una cadena enorme antes
    # de mirarla.
    title: str = Field(max_length=MAX_TITLE_LENGTH * 10)


@router.patch("/{client_thread_id}")
async def rename_thread_endpoint(
    client_thread_id: str,
    body: RenombrarConversacion,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict[str, Any]:
    """Cambia el nombre de una conversación.

    El titulo automatico es el primer mensaje recortado, y eso sirve para
    reconocer una conversacion recien tenida, no para encontrarla dentro de
    tres semanas entre otras diez que empiezan igual.
    """
    store = request.app.state.store
    thread_id = derive_thread_id(auth.user_id, client_thread_id)
    await assert_thread_ownership(store, thread_id, auth.user_id)

    try:
        renombrada = await rename_thread(store, auth.user_id, thread_id, body.title)
    except TituloInvalido as invalido:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(invalido)) from invalido

    if renombrada is None:
        # Una conversacion sin entrada en el indice es una que nunca tuvo un
        # turno. No hay nada que renombrar, y decir 404 es mas honesto que
        # crear la entrada aqui: se creo al hablar, y esto no es hablar.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Esa conversación no existe.")

    renombrada.pop("derived_thread_id", None)
    return renombrada


@router.delete("/{client_thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    client_thread_id: str,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> None:
    """Delete a conversation: its checkpoints, its index entry, its owner record."""
    store = request.app.state.store
    checkpointer = request.app.state.checkpointer
    thread_id = derive_thread_id(auth.user_id, client_thread_id)
    await assert_thread_ownership(store, thread_id, auth.user_id)

    if checkpointer is not None:
        # Checkpoints first. If this fails the conversation is still
        # listed and still readable, which is a recoverable state; doing
        # it the other way round would orphan the history -- unreachable
        # through the API but still in the database.
        await checkpointer.adelete_thread(thread_id)

    await forget_thread(store, auth.user_id, thread_id)
    logger.info("thread_deleted user_id=%r thread_id=%r", auth.user_id, thread_id)


__all__ = ["router"]
