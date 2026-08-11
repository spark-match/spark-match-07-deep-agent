"""Emision del informe de punta a punta (ADR-019, la enmienda de D4).

El circuito completo, en un solo paso desde fuera:

    ensamblar -> abrir la fila -> renderizar y subir -> cerrar la fila

**Por que es UNA herramienta y no dos.** Lo natural seria dejar
``build_orientation_report`` como esta y anadir un ``publish`` que reciba el
informe ya armado. Seria un error del mismo tipo que el que
``handler.py`` documenta: el informe tendria que salir de una llamada a
herramienta y volver a entrar por la siguiente, o sea **atravesar el contexto
del modelo**, con sus cifras dentro. La mayoria de las veces saldria intacto.
Aqui el informe nunca sale: se arma y se sube en la misma llamada, y lo que
ve el modelo es un identificador.

**El orden importa y no es el obvio.** Se ensambla ANTES de abrir la fila. Al
reves parece mas logico -- reservar el hueco y luego trabajar -- pero el
ensamblado es donde fallan los errores del modelo (una carrera que el motor no
recomendo, una sin explicar), que son corregibles y se reintentan. Con la fila
ya abierta, el reintento chocaria contra el indice de un solo pendiente, y
ademas habria gastado una plaza del tope diario por una equivocacion que el
modelo iba a arreglar solo. Ensamblar es CPU y no cuesta nada; la llamada al
modelo ya se pago antes de llegar aqui.

**Cualquier fallo posterior a abrir la fila la cierra.** Sin eso, un
WeasyPrint caido deja al estudiante mirando "Generando tu reporte..." hasta
que el barrido de diez minutos del backend se apiade. Es la diferencia entre
un error y un planton.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from src.agent.subagent_carryover import anotar
from src.agent.subagent_events import avisar_informe_listo
from src.backend import reports_client
from src.backend.reports_client import BackendNoConfigurado, ErrorDelBackend
from src.config import get_settings
from src.memory.profile_snapshot import leer_perfil_para_la_puerta
from src.models.report import OrientationReport
from src.reports.storage import InformeGuardado, upload_report
from src.threads.activity import INFORME
from src.tools.report.handler import build_orientation_report_handler

logger = logging.getLogger(__name__)


def _error(mensaje: str) -> dict[str, Any]:
    return {"status": "error", "data": None, "errors": [mensaje]}


#: Lo que el agente le dice al modelo ante cada motivo de rechazo de la puerta.
#: Texto y no solo el codigo: lo que sigue a esto es una frase para un
#: estudiante de secundaria, y dejar que el modelo improvise sobre
#: "report.riasec_missing" es pedirle que traduzca jerga nuestra.
#:
#: Quien lee esto es el **subagente de informes**, y solo tiene dos
#: herramientas: `recommend_programs` y esta. No puede delegar ni hablar con
#: el estudiante. Antes le deciamos "delega en el subagente de assessment" y
#: "preguntale un par de cosas mas", dos cosas que desde ahi no se pueden
#: hacer; ante una instruccion imposible el modelo se invento una salida y
#: escribio el informe entero en su respuesta (medido en dev el 2026-08-11:
#: «El sistema me indica un error, pero segun los datos que me
#: proporcionaste...»). Cada frase de aqui tiene que ser ejecutable con lo
#: que ese subagente tiene en la mano, y eso es: parar y devolver.
_EXPLICACION_DE_LA_PUERTA = {
    reports_client.CODIGO_SIN_RIASEC: (
        "Todavia no se le puede emitir el informe porque no tiene las seis "
        "puntuaciones RIASEC guardadas. Que las hayas visto en el contexto no "
        "significa que esten registradas, y lo que cuenta es lo registrado. Para "
        "aqui: no lo reintentes y no escribas el informe. Devuelve al coordinador "
        "que hace falta completar el cuestionario vocacional primero."
    ),
    reports_client.CODIGO_PERFIL_CORTO: (
        "El perfil se queda corto para un informe que merezca la pena. Para aqui: "
        "no lo reintentes y no escribas el informe. Devuelve al coordinador que "
        "faltan datos basicos del estudiante (edad, nivel educativo, que le "
        "interesa) y que los pregunte el; sus preferencias de region y presupuesto "
        "no cuentan para esto."
    ),
    reports_client.CODIGO_YA_EN_CURSO: (
        "Ya hay un informe generandose para este estudiante. Para aqui: no lo "
        "pidas otra vez ni escribas nada. Devuelve al coordinador que hay uno en "
        "marcha y que espere unos segundos."
    ),
    reports_client.CODIGO_TOPE_DIARIO: (
        "Ha llegado al tope de informes por dia. Para aqui: no lo reintentes y no "
        "escribas el informe. Devuelve al coordinador cuando podra pedir otro."
    ),
}


def _mensaje_de_rechazo(err: ErrorDelBackend) -> str:
    """El error del backend, ya convertido en instruccion para el modelo."""
    explicacion = _EXPLICACION_DE_LA_PUERTA.get(err.code)
    if explicacion is None:
        return f"El backend rechazo la apertura del informe: {err}"
    # El `meta` viaja tal cual porque lleva las cifras que hacen la frase util:
    # cuanto le falta de completitud, o a que hora se le reabre el tope.
    return f"{explicacion} (detalle del backend: {err.message} {err.meta or ''})".strip()


def _a_objeto(guardado: Any) -> dict[str, Any]:
    return {
        "key": guardado.key,
        "versionId": guardado.version_id,
        "sizeBytes": guardado.size_bytes,
        "checksumSha256": guardado.checksum_sha256,
    }


def _cuerpo_del_cierre(
    informe: OrientationReport,
    guardado: InformeGuardado,
    profile_completeness: float,
    generation_ms: int,
) -> dict[str, Any]:
    """Traduce lo subido al `CompleteReportInput` que espera el backend.

    Es el unico sitio donde conviven los dos vocabularios: ``snake_case`` de
    Pydantic aqui, ``camelCase`` del contrato HTTP alli. Tenerlo en una sola
    funcion es lo que hace que un cambio de nombre en el backend se arregle en
    un punto y no en cuatro.
    """
    return {
        "bucket": guardado.bucket,
        "objects": {"json": _a_objeto(guardado.json), "pdf": _a_objeto(guardado.pdf)},
        "schemaVersion": informe.schema_version,
        "riasecCode": informe.riasec_code,
        "datasetSource": informe.dataset_source,
        "datasetSnapshotDate": informe.dataset_snapshot_date.isoformat(),
        "topCareers": [carrera.career for carrera in informe.careers],
        "profileCompleteness": profile_completeness,
        "modelId": get_settings().model_id,
        "langsmithRunId": _traza_actual(),
        "generationMs": generation_ms,
    }


def _traza_actual() -> str | None:
    """El id de la traza de LangSmith de este turno, si hay tracing.

    Es lo que permite saltar desde un informe en la interfaz a la ejecucion
    exacta que lo produjo (D10). Se degrada a ``None`` sin ruido: con el
    tracing apagado no hay traza que apuntar, y eso no es un fallo.
    """
    try:
        from langsmith.run_helpers import get_current_run_tree

        arbol = get_current_run_tree()
    except Exception:
        return None
    return str(arbol.id) if arbol is not None else None


async def publish_orientation_report_handler(
    *,
    user_id: str,
    token: str,
    store: Any,
    riasec_code: str,
    profile_summary: str,
    insights: list[dict[str, str]],
    region: str | None = None,
    management_type: str | None = None,
    institution_type: str | None = None,
    max_annual_cost: float | None = None,
) -> dict[str, Any]:
    """Arma el informe, lo sube y lo registra. Devuelve el id, no el informe."""
    if not token:
        # Pasa en una invocacion directa del grafo y detras del authorizer de
        # API Gateway. No es culpa del modelo y no lo va a arreglar
        # reintentando, asi que se dice tal cual.
        return _error(
            "No hay credencial del estudiante en este turno, asi que el informe no "
            "se puede registrar. Es un problema de despliegue, no del estudiante."
        )

    # 1. Ensamblar. Aqui es donde salen los errores que el modelo puede
    #    corregir, y por eso va antes de tocar el backend.
    armado = build_orientation_report_handler(
        riasec_code=riasec_code,
        profile_summary=profile_summary,
        insights=insights,
        region=region,
        management_type=management_type,
        institution_type=institution_type,
        max_annual_cost=max_annual_cost,
    )
    if armado["status"] == "error":
        return armado
    informe = OrientationReport.model_validate(armado["data"])

    # 2. Abrir la fila. Las dos cifras de la puerta salen del store y no de
    #    los argumentos: son las que deciden el permiso, y pedirselas al
    #    modelo seria pedirle que se autoevalue.
    puerta = await leer_perfil_para_la_puerta(store, user_id)
    try:
        abierto = await reports_client.open_report(
            token,
            profile_completeness=puerta.profile_completeness,
            riasec_code=puerta.riasec_code,
        )
    except BackendNoConfigurado as exc:
        return _error(str(exc))

    if isinstance(abierto, ErrorDelBackend):
        return _error(_mensaje_de_rechazo(abierto))

    report_id = str(abierto.get("id") or "")
    if not report_id:
        return _error("El backend abrio el informe pero no devolvio su id.")

    # 3. Renderizar y subir. A partir de aqui, cualquier fallo cierra la fila.
    empezado = time.monotonic()
    try:
        # `to_thread` y no la llamada directa: `upload_report` es sincrona
        # (WeasyPrint y boto3) y tarda segundos. Llamarla en el bucle de
        # eventos congelaria TODOS los SSE abiertos del contenedor, no solo
        # este turno.
        guardado = await asyncio.to_thread(upload_report, user_id, report_id, informe)
    except Exception as exc:
        logger.exception("Fallo la subida del informe", extra={"report_id": report_id})
        await _cerrar_como_fallido(token, report_id, f"No se pudo generar el PDF: {exc}")
        return _error(
            "El informe no se pudo generar. Dile al estudiante que ha fallado y que "
            "puede volver a pedirlo; no lo reintentes tu solo."
        )

    generation_ms = int((time.monotonic() - empezado) * 1000)

    # 4. Cerrar la fila. Si esto falla, el objeto esta en S3 pero la fila
    #    sigue abierta: el barrido de diez minutos la marcara fallida y el
    #    estudiante podra pedir otro.
    cierre = _cuerpo_del_cierre(informe, guardado, puerta.profile_completeness, generation_ms)
    cerrado = await reports_client.complete_report(token, report_id, cierre)
    if isinstance(cerrado, ErrorDelBackend):
        logger.error(
            "El informe se subio pero no se pudo cerrar",
            extra={"report_id": report_id, "error": str(cerrado)},
        )
        return _error(
            "El informe se genero pero no se pudo registrar, asi que el estudiante "
            "todavia no puede abrirlo. Dile que lo intente de nuevo en un minuto."
        )

    logger.info(
        "Informe emitido",
        extra={"report_id": report_id, "generation_ms": generation_ms},
    )

    # El aviso a la pantalla va DESPUES de cerrar la fila, no antes: hasta que
    # el backend no la cierra, el informe no se puede abrir todavia, y un
    # boton que lleva a una pantalla vacia es peor que no tener boton.
    await avisar_informe_listo(report_id, [carrera.career for carrera in informe.careers])

    # Y ademas se deja escrito para el padre. El aviso de arriba solo existe
    # mientras el turno corre: al recargar la pagina el enlace desaparecia,
    # porque esto pasa dentro del subagente de report y de ahi no vuelve mas
    # que el texto final. Ver `src/agent/subagent_carryover.py`.
    anotar(INFORME, report_id)

    return {
        "status": "success",
        # Deliberadamente escueto: el id para poder enlazarlo, y lo justo para
        # que el modelo pueda anunciarlo con verdad. El contenido del informe
        # NO vuelve al contexto -- son decenas de miles de caracteres que se
        # pagarian en cada turno posterior de la conversacion, y el estudiante
        # lo va a leer en su pantalla, no en el chat.
        "data": {
            "report_id": report_id,
            "careers": [carrera.career for carrera in informe.careers],
            "riasec_code": informe.riasec_code,
            "generation_ms": generation_ms,
        },
        "errors": None,
    }


async def _cerrar_como_fallido(token: str, report_id: str, motivo: str) -> None:
    """Marca la fila como fallida sin dejar que el intento tape el error real.

    Si el propio cierre falla se registra y se sigue: quien llama ya esta en
    el camino de error y tiene algo que contarle al estudiante. Dejar subir
    esta excepcion cambiaria "no se pudo generar el PDF" por un fallo del
    backend, que es la causa equivocada.
    """
    try:
        resultado = await reports_client.fail_report(token, report_id, motivo)
    except Exception:
        logger.exception("Tampoco se pudo marcar el informe como fallido")
        return
    if isinstance(resultado, ErrorDelBackend):
        logger.error(
            "Tampoco se pudo marcar el informe como fallido",
            extra={"report_id": report_id, "error": str(resultado)},
        )


__all__ = ["publish_orientation_report_handler"]
