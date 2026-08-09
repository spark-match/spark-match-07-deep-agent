"""HTTP surface for the student's profile: read it, edit its preferences.

The profile already existed and already worked — langmem extracts it from the
conversation in the background and ``ProfileHydrationMiddleware`` puts it back
in the system prompt. What it had no door for was the *other* direction: a
student could not see what the system believed about them, nor correct it
except by talking until the extractor changed its mind.

That matters more than it sounds, because four of those fields
(``preferred_region``, ``preferred_management``, ``preferred_institution_type``,
``max_annual_budget``) become **hard filters** in ``recommend_programs``. A
wrong RIASEC score produces an odd recommendation the student can argue with; a
wrong budget produces options that are never shown at all. The screen at
``/filters`` is where that error becomes visible and fixable, and this router is
its wire.

Scope on purpose: ``PUT`` touches **only those four fields**. The vocational
half of the profile is the extractor's, and letting a form overwrite RIASEC
scores would give a student a way to fake an affinity they do not have.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from src.auth import AuthContext, require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profile", tags=["profile"])

# Mismo namespace que `ProfileHydrationMiddleware` y `PROFILE_NAMESPACE` de
# `src.memory.profile_manager`, con `{user_id}` ya resuelto.
_NAMESPACE = ("spark-match", "{user_id}", "profile")

# Clave usada SOLO al crear el primer perfil desde aqui. Cuando ya hay uno se
# reutiliza la suya, venga de donde venga: langmem trabaja con
# `enable_inserts=False`, o sea un unico item por namespace que actualiza en
# sitio, y escribir con otra clave dejaria dos perfiles donde el lector
# (`store.search(..., limit=1)`) elegiria uno cualquiera.
_CLAVE_INICIAL = "profile"

# Los unicos campos que esta API escribe. Ver el docstring del modulo.
PREFERENCIAS = (
    "preferred_region",
    "preferred_management",
    "preferred_institution_type",
    "max_annual_budget",
)


class PreferencesPayload(BaseModel):
    """Las cuatro preferencias de busqueda, todas opcionales.

    Semantica PUT: se reemplaza el bloque entero. Un campo ausente se guarda
    como ``None``, que significa **"no lo sabemos"** y no "sin filtro" -- es lo
    que hace que `recommend_programs` no lo aplique. Enviar solo `region`
    borra, por tanto, el presupuesto anterior; es intencionado, porque quien
    envia es la pantalla y la pantalla siempre manda las cuatro.
    """

    preferred_region: str | None = None
    preferred_management: str | None = None
    preferred_institution_type: str | None = None
    max_annual_budget: float | None = Field(default=None, ge=0)


def _namespace(user_id: str) -> tuple[str, ...]:
    return tuple(part.replace("{user_id}", user_id) for part in _NAMESPACE)


async def _leer_perfil(store: Any, user_id: str) -> tuple[str | None, dict[str, Any]]:
    """Devuelve ``(clave, perfil)``; clave ``None`` cuando aun no hay ninguno."""
    if store is None:
        return None, {}
    items = await store.asearch(_namespace(user_id), limit=1)
    if not items:
        return None, {}
    valor = items[0].value
    return items[0].key, dict(valor) if isinstance(valor, dict) else {}


@router.get("")
async def get_profile(
    request: Request,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict[str, Any]:
    """Lo que el sistema cree saber de quien llama.

    Devuelve ``profile: null`` cuando todavia no hay nada extraido, que es el
    estado normal antes de la primera conversacion.
    """
    _, perfil = await _leer_perfil(request.app.state.store, auth.user_id)
    return {"profile": perfil or None}


@router.put("/preferences")
async def put_preferences(
    payload: PreferencesPayload,
    request: Request,
    auth: Annotated[AuthContext, Depends(require_auth)],
) -> dict[str, Any]:
    """Fija las cuatro preferencias de busqueda, dejando el resto intacto.

    Se lee, se fusiona y se vuelve a escribir en vez de sobrescribir el item:
    el perfil que hay debajo lo mantiene el extractor conversacional, y
    reemplazarlo entero desde un formulario borraria el codigo RIASEC, los
    intereses y todo lo demas que costo una conversacion entera reunir.
    """
    store = request.app.state.store
    if store is None:
        # Sin store no hay memoria de largo plazo (perfil `memory` sin
        # persistencia, o un despliegue mal configurado). Se dice claramente en
        # vez de aceptar la escritura y perderla en silencio.
        return {"profile": None, "persisted": False}

    clave, perfil = await _leer_perfil(store, auth.user_id)
    perfil.update(payload.model_dump())

    await store.aput(_namespace(auth.user_id), clave or _CLAVE_INICIAL, perfil)
    logger.info(
        "profile_preferences_updated user_id=%r fields=%r",
        auth.user_id,
        [campo for campo in PREFERENCIAS if perfil.get(campo) is not None],
    )
    return {"profile": perfil, "persisted": True}


__all__ = ["PREFERENCIAS", "PreferencesPayload", "router"]
