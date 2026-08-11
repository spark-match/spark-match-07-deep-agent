"""Guardar en el perfil lo que el assessment midio (ADR-019, D8).

**El hueco que este modulo cierra.** `evaluate_riasec_profile` calculaba el
codigo RIASEC, se lo devolvia al modelo, y no lo guardaba en ningun sitio: era
una funcion pura. El unico que escribia el `StudentProfile` del store era el
extractor en segundo plano de langmem, que *infiere* las puntuaciones del texto
de la conversacion.

Y la puerta de D8 --`has_riasec_profile`-- exige las SEIS puntuaciones
presentes en el perfil guardado. Asi que un estudiante podia terminar el
cuestionario entero, ver su codigo en pantalla, y que el sistema siguiera
diciendo "todavia no tienes perfil RIASEC" al pedir su informe. Medido en dev
el 2026-08-11: el agente entro en bucle intentando "registrar" el perfil y el
turno murio por limite de recursion.

**Por que se escribe aqui y no se relaja la puerta.** La puerta pide algo
razonable; lo que faltaba era que alguien lo produjera. Y este es el mejor
momento posible para escribirlo: las puntuaciones del assessment son
*respuestas del estudiante pasadas por la validacion del handler*, no una
inferencia del extractor sobre el texto. Es el dato mas firme que hay.

**Lo que NO hace: tocar nada mas.** Fusiona sobre el perfil existente en vez de
reemplazarlo. El nombre, la edad, los intereses y los cuatro filtros de
busqueda los pone el extractor, y pisarlos aqui borraria en silencio lo que el
estudiante conto conversando.
"""

from __future__ import annotations

import logging
from typing import Any

from src.memory.profile_envelope import con_campos
from src.memory.profile_manager import PROFILE_NAMESPACE

logger = logging.getLogger(__name__)

#: Con la que langmem guarda el perfil dentro del namespace.
CLAVE_DEL_PERFIL = "profile"

#: Letra del codigo -> campo del `StudentProfile`.
CAMPOS_POR_LETRA = {
    "R": "realistic",
    "I": "investigative",
    "A": "artistic",
    "S": "social",
    "E": "enterprising",
    "C": "conventional",
}


def _namespace(user_id: str) -> tuple[str, ...]:
    return tuple(parte.replace("{user_id}", user_id) for parte in PROFILE_NAMESPACE)


async def guardar_riasec_medido(
    store: Any,
    user_id: str,
    scores: dict[str, int],
    riasec_code: str,
) -> bool:
    """Fusiona las seis puntuaciones y el codigo en el perfil guardado.

    Nunca lanza. Si el store esta caido, el assessment igual le sirve al
    estudiante --tiene su codigo en pantalla-- y lo unico que se pierde es
    poder emitir el informe sin repetir el cuestionario. Tumbar el turno por
    eso seria cambiar un problema por otro peor.

    Returns:
        Si se llego a escribir. Se devuelve para poder afirmarlo en un test
        sin leer logs, y para que quien llame decida si merece un aviso.
    """
    if store is None:
        logger.warning("Sin store: el perfil RIASEC medido no se guarda")
        return False

    campos: dict[str, Any] = {
        CAMPOS_POR_LETRA[letra]: valor
        for letra, valor in scores.items()
        if letra in CAMPOS_POR_LETRA
    }
    if len(campos) != len(CAMPOS_POR_LETRA):
        # Con menos de seis, `has_riasec_profile` seguiria en False y
        # habriamos escrito un perfil a medias que nadie puede usar.
        logger.warning(
            "El assessment no trajo las seis puntuaciones (%d); no se guarda",
            len(campos),
        )
        return False

    campos["riasec_code"] = riasec_code

    try:
        namespace = _namespace(user_id)
        existente = await store.asearch(namespace, limit=1)
        # `key` del item y no `CLAVE_DEL_PERFIL` a secas: si langmem cambiara
        # su clave, escribir en la nuestra crearia un SEGUNDO perfil y la
        # puerta seguiria leyendo el viejo, sin nada que lo delate.
        clave = existente[0].key if existente else CLAVE_DEL_PERFIL
        anterior = existente[0].value if existente else {}

        # `con_campos` y no `{**anterior, **campos}`: langmem guarda el perfil
        # dentro de `content`, y fusionar en la raiz dejaba las seis
        # puntuaciones fuera del perfil de verdad -- invisibles para la
        # completitud de D8 y para el propio extractor.
        await store.aput(namespace, clave, con_campos(anterior, campos))
    except Exception:
        logger.warning("No se pudo guardar el perfil RIASEC medido", exc_info=True)
        return False

    logger.info("Perfil RIASEC guardado desde el assessment (code=%s)", riasec_code)
    return True


__all__ = ["CAMPOS_POR_LETRA", "CLAVE_DEL_PERFIL", "guardar_riasec_medido"]
