"""Subida del informe a S3 (ADR-019, D3 y fase 4').

Dos objetos por informe, bajo la clave `reports/{user_id}/{report_id}`:

- `.json` — el contenido. **Es la fuente de verdad**: de el se puede volver a
  renderizar el PDF sin gastar una sola llamada al modelo.
- `.pdf` — lo que el estudiante descarga. Derivado.

**El orden de subida no es arbitrario.** Primero el JSON, despues el PDF. Si
falla el segundo queda un JSON huerfano, que es recuperable: se re-renderiza y
listo. Al reves quedaria un PDF sin su fuente, y para rehacer el JSON habria
que volver a pasar por el modelo -- otra llamada, otro resultado distinto, y
un documento que ya no se corresponde con sus propios datos.

Antes de subir nada se renderiza el PDF, para que un fallo de WeasyPrint no
deje a medias lo que ya esta en el bucket.

Lo que se devuelve son **claves, no URLs** (D3). Una prefirmada caduca y una
que no caduca es una capacidad permanente sobre el perfil psicometrico de un
menor; quien sirva el fichero es el backend, con el JWT del estudiante
delante.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from src.config import get_settings
from src.models.report import OrientationReport
from src.reports.pdf import report_to_pdf

logger = logging.getLogger(__name__)

PREFIJO = "reports"


@dataclass(frozen=True)
class ObjetoGuardado:
    """Lo que el backend necesita para volver a encontrar un objeto."""

    key: str
    version_id: str | None
    size_bytes: int
    checksum_sha256: str


@dataclass(frozen=True)
class InformeGuardado:
    """Los dos objetos de un informe, mas el bucket que los contiene."""

    bucket: str
    json: ObjetoGuardado
    pdf: ObjetoGuardado


@lru_cache(maxsize=1)
def resolve_reports_bucket() -> str:
    """Nombre del bucket de informes, del override local o de SSM.

    Cacheado: no cambia en la vida del proceso y cada consulta a SSM es una
    llamada de red en el camino de generar un informe.
    """
    settings = get_settings()
    if settings.reports_bucket:
        return settings.reports_bucket

    # Import local, como en `src/persistence/secrets.py`: mantiene boto3 fuera
    # de cualquier camino que se ejercite sin credenciales AWS (hard rule #7).
    import boto3

    ssm = boto3.client("ssm", region_name=settings.aws_region)
    valor: str = ssm.get_parameter(Name=settings.reports_bucket_ssm_param)["Parameter"]["Value"]
    return valor


def _guardar(
    cliente: Any, bucket: str, key: str, cuerpo: bytes, content_type: str
) -> ObjetoGuardado:
    """Sube un objeto y devuelve lo que hay que anotar en la fila del backend.

    No se pide cifrado explicito: el bucket lleva SSE-KMS por defecto con la
    CMK del proyecto (fase 3), asi que S3 lo aplica solo. Pedirlo aqui ademas
    duplicaria la decision en dos sitios que pueden divergir.

    `ChecksumAlgorithm` hace que S3 verifique la integridad en transito y
    rechace el objeto si no cuadra. El sha256 se calcula igualmente aqui
    porque es lo que se guarda en la fila: sirve para detectar que un objeto
    cambio despues, cosa que la verificacion en transito no cubre.
    """
    digest = hashlib.sha256(cuerpo).hexdigest()

    respuesta = cliente.put_object(
        Bucket=bucket,
        Key=key,
        Body=cuerpo,
        ContentType=content_type,
        ChecksumAlgorithm="SHA256",
    )

    return ObjetoGuardado(
        key=key,
        version_id=respuesta.get("VersionId"),
        size_bytes=len(cuerpo),
        checksum_sha256=digest,
    )


def upload_report(user_id: str, report_id: str, informe: OrientationReport) -> InformeGuardado:
    """Renderiza el PDF y sube los dos objetos del informe.

    Args:
        user_id: Dueño del informe. Particiona el bucket y es lo que permite
            que una politica de IAM acote el acceso por prefijo.
        report_id: Id de la fila que el backend ya creo en `pending`.
        informe: El contenido, ya ensamblado por `src.tools.report`.

    Raises:
        PdfRenderingUnavailableError: si faltan las bibliotecas de WeasyPrint.
            Se lanza **antes** de subir nada.
    """
    # Primero renderizar. Si esto falla, el bucket se queda como estaba.
    pdf = report_to_pdf(informe)
    cuerpo_json = json.dumps(informe.model_dump(), ensure_ascii=False, indent=2).encode("utf-8")

    bucket = resolve_reports_bucket()
    base = f"{PREFIJO}/{user_id}/{report_id}"

    import boto3

    s3 = boto3.client("s3", region_name=get_settings().aws_region)

    # JSON primero: es la fuente de la que se puede rehacer el PDF.
    guardado_json = _guardar(s3, bucket, f"{base}.json", cuerpo_json, "application/json")
    guardado_pdf = _guardar(s3, bucket, f"{base}.pdf", pdf, "application/pdf")

    logger.info(
        "Informe subido",
        extra={
            "bucket": bucket,
            "report_id": report_id,
            "json_bytes": guardado_json.size_bytes,
            "pdf_bytes": guardado_pdf.size_bytes,
        },
    )

    return InformeGuardado(bucket=bucket, json=guardado_json, pdf=guardado_pdf)


__all__ = [
    "PREFIJO",
    "InformeGuardado",
    "ObjetoGuardado",
    "resolve_reports_bucket",
    "upload_report",
]
