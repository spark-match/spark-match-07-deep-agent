"""Las dos cifras del perfil que la puerta de D8 necesita.

Se leen **del store, no del modelo**. Es la misma regla que gobierna el
informe (ADR-019 D6): las cifras salen del dato. Aqui pesa el doble, porque
estas dos no son adorno del documento sino lo que decide si el estudiante
tiene derecho a el -- dejar que el modelo las escriba seria pedirle que se
autoevalue el permiso.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from src.memory.profile_envelope import perfil_de
from src.memory.profile_manager import PROFILE_NAMESPACE
from src.models.profile import StudentProfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PerfilParaLaPuerta:
    """Lo que `POST /v1/reports` mira antes de abrir la fila."""

    profile_completeness: float
    riasec_code: str | None


#: Perfil que no existe todavia. Completitud cero y sin codigo: el backend lo
#: rechaza con `report.riasec_missing`, que es la respuesta correcta para
#: alguien que no ha conversado nunca.
PERFIL_VACIO = PerfilParaLaPuerta(profile_completeness=0.0, riasec_code=None)


def _namespace(user_id: str) -> tuple[str, ...]:
    return tuple(parte.replace("{user_id}", user_id) for parte in PROFILE_NAMESPACE)


async def leer_perfil_para_la_puerta(store: Any, user_id: str) -> PerfilParaLaPuerta:
    """Completitud y codigo RIASEC del estudiante, o :data:`PERFIL_VACIO`.

    Nunca lanza. Un store caido o un perfil con una forma que el modelo de
    Pydantic no reconoce salen como perfil vacio, y el estudiante recibe "aun
    no puedo emitirte el informe" en vez de una excepcion a mitad de turno.
    Falla cerrado a proposito: la puerta de D8 se salta hacia el "no", que es
    el lado del que se puede salir conversando.
    """
    if store is None:
        return PERFIL_VACIO

    try:
        items = await store.asearch(_namespace(user_id), limit=1)
    except Exception:
        logger.warning("No se pudo leer el perfil para la puerta de D8", exc_info=True)
        return PERFIL_VACIO

    if not items:
        return PERFIL_VACIO

    # `perfil_de` y no `items[0].value` a secas: langmem guarda el perfil
    # dentro de un sobre `{"kind", "content"}`. Validar el sobre no falla --
    # en `StudentProfile` no hay un solo campo obligatorio -- sino que
    # devuelve un perfil entero a `None`, y esta puerta respondia
    # `riasec_missing` a estudiantes con las seis puntuaciones guardadas.
    # Ver `src.memory.profile_envelope`.
    crudo = perfil_de(items[0].value)
    if not crudo:
        return PERFIL_VACIO

    try:
        # `model_validate` y no construir a mano: `profile_completeness` y
        # `riasec_code` son properties del modelo, y calcularlas aqui seria
        # una segunda definicion de "perfil completo" que se desalinearia de
        # la de D8 en cuanto alguien tocara una de las dos.
        perfil = StudentProfile.model_validate(crudo)
    except Exception:
        logger.warning("El perfil guardado no encaja en StudentProfile", exc_info=True)
        return PERFIL_VACIO

    return PerfilParaLaPuerta(
        profile_completeness=perfil.profile_completeness,
        riasec_code=perfil.riasec_code if perfil.has_riasec_profile else None,
    )


__all__ = ["PERFIL_VACIO", "PerfilParaLaPuerta", "leer_perfil_para_la_puerta"]
