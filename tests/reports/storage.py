"""Tests de la subida del informe a S3 (fase 4' del ADR-019).

Sin AWS y sin WeasyPrint: `boto3.client` se sustituye por un doble que anota
lo que se le pidio, y el renderizado por un stub. Lo que se prueba aqui es la
LOGICA DE GUARDADO -- que claves, en que orden, con que metadatos -- y atarla
a que la maquina tenga pango instalado la haria saltarse justo donde importa.
"""

import hashlib
import json

import pytest

from src.config import get_settings
from src.reports import storage
from src.reports.pdf import PdfRenderingUnavailableError
from src.reports.storage import resolve_reports_bucket, upload_report
from tests.reports.fixtures import informe

PDF_FALSO = b"%PDF-1.7 contenido de mentira"
USER = "u-123"
REPORT = "r-abc"


class FakeS3:
    """Anota cada put_object en orden."""

    def __init__(self) -> None:
        self.puts: list[dict] = []

    def put_object(self, **kwargs):
        self.puts.append(kwargs)
        return {"VersionId": f"v{len(self.puts)}"}


class FakeSsm:
    def __init__(self) -> None:
        self.pedido: str | None = None

    # `Name` en mayuscula porque es el nombre del parametro en boto3, no una
    # eleccion de estilo: `get_parameter(Name=...)` es como lo llama el codigo.
    def get_parameter(self, Name):
        self.pedido = Name
        return {"Parameter": {"Value": "spark-match-reports-dev"}}


@pytest.fixture(autouse=True)
def _sin_cache():
    resolve_reports_bucket.cache_clear()
    get_settings.cache_clear()
    yield
    resolve_reports_bucket.cache_clear()
    get_settings.cache_clear()


@pytest.fixture
def s3(monkeypatch):
    """Un S3 falso, el bucket por override local y el PDF sin renderizar."""
    monkeypatch.setenv("SPARK_REPORTS_BUCKET", "bucket-de-pruebas")
    monkeypatch.setattr(storage, "report_to_pdf", lambda _: PDF_FALSO)
    doble = FakeS3()
    monkeypatch.setattr("boto3.client", lambda service, **_: doble)
    return doble


class TestElBucket:
    def test_el_override_local_gana_y_no_toca_aws(self, monkeypatch):
        # Hard rule #7: el perfil se tiene que poder probar sin credenciales.
        monkeypatch.setenv("SPARK_REPORTS_BUCKET", "mi-bucket")
        monkeypatch.setattr(
            "boto3.client", lambda *a, **k: pytest.fail("no deberia tocar AWS con override")
        )

        assert resolve_reports_bucket() == "mi-bucket"

    def test_sin_override_lo_lee_del_ssm_de_la_fase_3(self, monkeypatch):
        monkeypatch.delenv("SPARK_REPORTS_BUCKET", raising=False)
        ssm = FakeSsm()
        monkeypatch.setattr("boto3.client", lambda service, **_: ssm)

        bucket = resolve_reports_bucket()

        assert bucket == "spark-match-reports-dev"
        assert ssm.pedido == "/spark-match/dev/config/reports-bucket"

    def test_solo_pregunta_una_vez(self, monkeypatch):
        # Cada consulta a SSM es una llamada de red dentro del camino de
        # generar un informe.
        monkeypatch.delenv("SPARK_REPORTS_BUCKET", raising=False)
        ssm = FakeSsm()
        llamadas = {"n": 0}

        def cliente(service, **_):
            llamadas["n"] += 1
            return ssm

        monkeypatch.setattr("boto3.client", cliente)

        resolve_reports_bucket()
        resolve_reports_bucket()

        assert llamadas["n"] == 1


class TestLasClaves:
    def test_el_formato_es_el_del_adr(self, s3):
        upload_report(USER, REPORT, informe())

        claves = [p["Key"] for p in s3.puts]
        assert claves == [f"reports/{USER}/{REPORT}.json", f"reports/{USER}/{REPORT}.pdf"]

    def test_particiona_por_usuario(self, s3):
        # Es lo que permite acotar el acceso por prefijo desde IAM.
        upload_report("otro-usuario", REPORT, informe())

        assert all(p["Key"].startswith("reports/otro-usuario/") for p in s3.puts)


class TestElOrdenDeSubida:
    """El JSON antes que el PDF, y no es indiferente."""

    def test_el_json_va_primero(self, s3):
        # Si falla el segundo queda un JSON huerfano, recuperable
        # re-renderizando. Al reves quedaria un PDF sin fuente, y rehacer el
        # JSON exigiria otra llamada al modelo -- con otro resultado.
        upload_report(USER, REPORT, informe())

        assert s3.puts[0]["Key"].endswith(".json")
        assert s3.puts[1]["Key"].endswith(".pdf")

    def test_si_el_pdf_no_se_puede_renderizar_no_se_sube_nada(self, monkeypatch):
        monkeypatch.setenv("SPARK_REPORTS_BUCKET", "bucket-de-pruebas")
        doble = FakeS3()
        monkeypatch.setattr("boto3.client", lambda service, **_: doble)

        def revienta(_):
            raise PdfRenderingUnavailableError("faltan libpango y compania")

        monkeypatch.setattr(storage, "report_to_pdf", revienta)

        # La muestra se construye FUERA del `raises`: dentro, un fallo
        # armandola seria indistinguible del que se quiere probar.
        muestra = informe()

        with pytest.raises(PdfRenderingUnavailableError):
            upload_report(USER, REPORT, muestra)

        assert doble.puts == [], "el bucket no debe quedar a medias"


class TestLoQueSeGuarda:
    def test_los_tipos_de_contenido(self, s3):
        upload_report(USER, REPORT, informe())

        tipos = [p["ContentType"] for p in s3.puts]
        assert tipos == ["application/json", "application/pdf"]

    def test_el_json_es_el_informe_y_se_puede_releer(self, s3):
        # De este objeto se re-renderiza el PDF sin volver a pagar el modelo,
        # asi que tiene que contener el informe entero.
        original = informe()

        upload_report(USER, REPORT, original)

        recuperado = json.loads(s3.puts[0]["Body"].decode("utf-8"))
        assert recuperado == original.model_dump()

    def test_el_json_no_escapa_los_acentos(self, s3):
        # `ensure_ascii=True` guardaria "Ingeniería", que es correcto y
        # es ilegible cuando alguien abre el objeto para depurar.
        upload_report(USER, REPORT, informe())

        assert "Ingeniería" in s3.puts[0]["Body"].decode("utf-8")

    def test_el_pdf_va_tal_cual(self, s3):
        upload_report(USER, REPORT, informe())

        assert s3.puts[1]["Body"] == PDF_FALSO


class TestIntegridad:
    def test_el_checksum_es_el_sha256_del_cuerpo(self, s3):
        guardado = upload_report(USER, REPORT, informe())

        assert guardado.pdf.checksum_sha256 == hashlib.sha256(PDF_FALSO).hexdigest()
        assert guardado.json.checksum_sha256 == hashlib.sha256(s3.puts[0]["Body"]).hexdigest()

    def test_le_pide_a_s3_que_verifique_en_transito(self, s3):
        # El sha256 propio detecta que un objeto cambio DESPUES; esto detecta
        # que llego mal. No son lo mismo y hacen falta los dos.
        upload_report(USER, REPORT, informe())

        assert all(p["ChecksumAlgorithm"] == "SHA256" for p in s3.puts)

    def test_no_pide_cifrado_explicito(self, s3):
        # El bucket lleva SSE-KMS por defecto con la CMK del proyecto
        # (fase 3). Repetirlo aqui duplicaria la decision en dos sitios que
        # pueden divergir, y el que se quedaria viejo seria este.
        upload_report(USER, REPORT, informe())

        assert all("ServerSideEncryption" not in p for p in s3.puts)


class TestLoQueSeDevuelve:
    def test_devuelve_claves_y_no_urls(self, s3):
        # D3: una prefirmada caduca y una que no caduca es una capacidad
        # permanente sobre el perfil psicometrico de un menor.
        guardado = upload_report(USER, REPORT, informe())

        texto = repr(guardado)
        assert "http" not in texto
        assert guardado.bucket == "bucket-de-pruebas"

    def test_anota_la_version_de_cada_objeto(self, s3):
        # El bucket tiene versionado; sin el version_id, la fila apunta a
        # "la ultima", que no tiene por que ser la que se sirvio.
        guardado = upload_report(USER, REPORT, informe())

        assert guardado.json.version_id == "v1"
        assert guardado.pdf.version_id == "v2"

    def test_anota_el_tamano(self, s3):
        guardado = upload_report(USER, REPORT, informe())

        assert guardado.pdf.size_bytes == len(PDF_FALSO)
        assert guardado.json.size_bytes == len(s3.puts[0]["Body"])
