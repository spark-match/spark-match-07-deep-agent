"""Cliente de los endpoints de informes de spark-match-03-backend (ADR-019).

Tres llamadas y un contrato de dos tiempos: se abre la fila, se sube el
artefacto a S3, se cierra la fila. El backend es el registro; este modulo es
lo unico del agente que sabe hablar con el.

**Se reenvia el JWT del estudiante, el agente no firma nada.** El agente lee
de SSM el mismo secreto que el backend usa para validar, asi que tecnicamente
podria emitirse un token a nombre de quien quisiera. No se hace: eso convierte
un secreto de *validacion* en capacidad de *emision* en dos servicios, y a
partir de ahi cualquier fallo en el agente es una suplantacion. Reenviar el
token que el estudiante ya presento no amplia el permiso de nadie -- el
backend decide exactamente lo mismo que decidiria si el estudiante llamara
solo. La caducidad no estorba: son 24 h contra una generacion de veinte
segundos.

**Los errores se devuelven, no se lanzan.** Quien llama es una herramienta
dentro de un turno de chat, y su trabajo es convertir un 409 en una pregunta
al estudiante. Una excepcion obligaria a cada punto de llamada a traducir
codigos HTTP; un resultado con el codigo del backend ya dentro deja esa
traduccion en un solo sitio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from src.config import get_settings

logger = logging.getLogger(__name__)

#: Codigos del backend que el agente sabe explicar. El resto salen como estan
#: y el prompt los cuenta en generico; no hace falta enumerar lo imprevisto.
CODIGO_SIN_RIASEC = "report.riasec_missing"
CODIGO_PERFIL_CORTO = "report.profile_incomplete"
CODIGO_TOPE_DIARIO = "report.daily_limit_reached"
CODIGO_YA_EN_CURSO = "report.already_generating"


class BackendNoConfigurado(RuntimeError):
    """No hay URL del backend. Es un fallo de despliegue, no del estudiante."""


@dataclass(frozen=True)
class ErrorDelBackend:
    """Un 4xx/5xx traducido a algo con lo que una herramienta puede razonar."""

    status: int
    #: El `details[0].code` del backend (`report.*`, `validation.*`...), o "".
    code: str
    message: str
    meta: dict[str, Any]

    def __str__(self) -> str:
        return f"[{self.status} {self.code or 'sin codigo'}] {self.message}"


def _base_url() -> str:
    url = (get_settings().backend_api_url or "").rstrip("/")
    if not url:
        raise BackendNoConfigurado(
            "SPARK_BACKEND_API_URL no esta configurada, asi que el agente no "
            "sabe a que backend registrar el informe."
        )
    return url


def _traducir_error(respuesta: httpx.Response) -> ErrorDelBackend:
    """Saca el primer `detail` del sobre de error del backend.

    El sobre es `{success, error: {code, message, details: [...]}}`. Se coge
    `details[0]` y no el `error.code` de arriba porque el de arriba es el
    generico del status (`conflict`, `too_many_requests`) y el de abajo es el
    que distingue "no tiene RIASEC" de "le falta contexto" -- que es la unica
    diferencia que cambia lo que el agente hace despues.
    """
    try:
        cuerpo = respuesta.json()
    except ValueError:
        cuerpo = {}

    error = cuerpo.get("error") if isinstance(cuerpo, dict) else None
    error = error if isinstance(error, dict) else {}
    detalles = error.get("details")
    primero = detalles[0] if isinstance(detalles, list) and detalles else {}
    primero = primero if isinstance(primero, dict) else {}

    meta = primero.get("meta")
    return ErrorDelBackend(
        status=respuesta.status_code,
        code=str(primero.get("code") or ""),
        message=str(primero.get("message") or error.get("message") or respuesta.reason_phrase),
        meta=meta if isinstance(meta, dict) else {},
    )


async def _llamar(
    token: str, metodo: str, ruta: str, cuerpo: dict[str, Any]
) -> dict[str, Any] | ErrorDelBackend:
    """Una peticion autenticada, con el error ya traducido."""
    ajustes = get_settings()
    url = f"{_base_url()}{ruta}"

    try:
        async with httpx.AsyncClient(timeout=ajustes.backend_timeout_seconds) as cliente:
            respuesta = await cliente.request(
                metodo,
                url,
                json=cuerpo,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
    except httpx.HTTPError as exc:
        # Una red caida no es un error de negocio, pero quien llama tiene que
        # poder distinguirlo igual: un 503 sintetico le dice "reintenta o
        # rindete", que es lo mismo que le diria el backend si contestara.
        logger.warning("El backend no contesto", extra={"url": url, "error": str(exc)})
        return ErrorDelBackend(
            503, "backend.unreachable", f"No se pudo hablar con el backend: {exc}", {}
        )

    if respuesta.is_success:
        cuerpo_ok = respuesta.json()
        datos = cuerpo_ok.get("data") if isinstance(cuerpo_ok, dict) else None
        return datos if isinstance(datos, dict) else {}

    return _traducir_error(respuesta)


async def open_report(
    token: str, *, profile_completeness: float, riasec_code: str | None
) -> dict[str, Any] | ErrorDelBackend:
    """`POST /v1/reports`. Abre la fila en `pending` y devuelve el informe.

    Las dos cifras del cuerpo son las entradas de la puerta de D8. Las manda el
    agente porque el backend no puede calcularlas: el `StudentProfile` vive en
    el store de aqui.
    """
    return await _llamar(
        token,
        "POST",
        "/v1/reports",
        {"profileCompleteness": profile_completeness, "riasecCode": riasec_code},
    )


async def complete_report(
    token: str, report_id: str, resultado: dict[str, Any]
) -> dict[str, Any] | ErrorDelBackend:
    """`POST /v1/reports/{id}/complete`. Cierra la fila como `ready`."""
    return await _llamar(token, "POST", f"/v1/reports/{report_id}/complete", resultado)


async def fail_report(token: str, report_id: str, motivo: str) -> dict[str, Any] | ErrorDelBackend:
    """`POST /v1/reports/{id}/fail`. Cierra la fila como `failed`.

    El motivo se recorta a 500 caracteres, que es lo que el backend acepta.
    Recortar aqui y no dejar que responda 400 es deliberado: esto se llama
    desde el camino de error, y un fallo al reportar un fallo deja la fila
    colgada hasta que el barrido de diez minutos la mate.
    """
    return await _llamar(token, "POST", f"/v1/reports/{report_id}/fail", {"reason": motivo[:500]})


__all__ = [
    "CODIGO_PERFIL_CORTO",
    "CODIGO_SIN_RIASEC",
    "CODIGO_TOPE_DIARIO",
    "CODIGO_YA_EN_CURSO",
    "BackendNoConfigurado",
    "ErrorDelBackend",
    "complete_report",
    "fail_report",
    "open_report",
]
