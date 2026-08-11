"""El sobre en que langmem guarda el perfil, y como abrirlo.

``create_memory_store_manager`` no guarda el :class:`StudentProfile` tal cual.
Lo mete en un sobre con el nombre del esquema delante::

    {"kind": "StudentProfile", "content": {"age": 22, "realistic": 8, ...}}

Eso no se documenta en ningun sitio del codigo que lo consume, y cada lector
del store llego a su manera a asumir que el valor **era** el perfil. Con
Pydantic esa suposicion no falla ruidosamente, que es lo que la hizo cara:
``StudentProfile`` tiene todos los campos opcionales, asi que
``model_validate`` sobre el sobre **valida sin quejarse** y devuelve un perfil
con los dieciseis campos a ``None``. Ni excepcion, ni warning, ni traza. La
puerta de D8 leia completitud 0.0 y ningun codigo RIASEC de un estudiante que
tenia las seis puntuaciones guardadas, y respondia ``riasec_missing`` -- que
es exactamente lo que se veia desde fuera: "no se pudo generar el reporte".

Medido en dev el 2026-08-11: el bloque de perfil que llega al modelo empieza
por ``- kind: StudentProfile`` y ``- content: {...}``, mientras la misma
llamada a ``publish_orientation_report`` se rechaza por perfil vacio.

Las dos funciones de aqui son la unica forma correcta de leer y de escribir
ese item. Quien vaya a tocar el perfil en el store, que pase por aqui.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: La clave del sobre donde langmem mete el perfil de verdad.
CLAVE_CONTENIDO = "content"


def _es_sobre(valor: Any) -> bool:
    """Si esto es un sobre de langmem y no un perfil suelto.

    Se mira ``content`` y no ``kind`` a proposito. ``kind`` es un nombre que
    langmem elige (el del esquema) y podria cambiar; lo que no puede cambiar
    sin que esto deje de ser un sobre es que el perfil viva dentro de un
    diccionario anidado. Y ``StudentProfile`` no tiene ningun campo
    ``content``, asi que la comprobacion no puede confundir un perfil suelto
    con un sobre.
    """
    return isinstance(valor, dict) and isinstance(valor.get(CLAVE_CONTENIDO), dict)


def perfil_de(valor: Any) -> dict[str, Any]:
    """El perfil que hay dentro de lo que devolvio el store.

    Acepta las dos formas a proposito. El sobre es lo que escribe langmem,
    pero un perfil suelto es lo que queda si algo escribio en el namespace
    antes de que el extractor pasara por primera vez, y ese caso tiene que
    seguir leyendose bien en vez de convertirse en el siguiente fallo mudo.

    Cualquier otra cosa --``None``, una lista, un texto-- sale como
    diccionario vacio: los lectores de esto deciden permisos, y ahi la
    respuesta segura es "no se sabe nada de esta persona".
    """
    if _es_sobre(valor):
        return dict(valor[CLAVE_CONTENIDO])
    if isinstance(valor, dict):
        return dict(valor)
    return {}


def con_campos(valor: Any, campos: Mapping[str, Any]) -> dict[str, Any]:
    """El item entero para volver a escribir, con ``campos`` ya fusionados.

    Devuelve el **item**, no el perfil: lo que se le pasa a ``store.aput``.
    Si habia sobre, los campos entran dentro de ``content`` y el sobre se
    conserva; si no lo habia, se fusionan en la raiz.

    Meterlos fuera del sobre --que es lo que se hacia-- tiene dos efectos, y
    ninguno avisa. Uno: la puerta de D8 calcula la completitud sobre el
    contenido, asi que las seis puntuaciones escritas por el assessment no le
    subian nada y el estudiante se quedaba clavado en 0.50, por debajo del
    0.60 que se pide. Dos: el extractor de langmem tampoco las ve al
    actualizar el perfil, de modo que lo que el assessment midio no llegaba
    nunca a la memoria que el extractor mantiene.
    """
    if _es_sobre(valor):
        return {**valor, CLAVE_CONTENIDO: {**valor[CLAVE_CONTENIDO], **campos}}
    base = dict(valor) if isinstance(valor, dict) else {}
    return {**base, **campos}


__all__ = ["CLAVE_CONTENIDO", "con_campos", "perfil_de"]
