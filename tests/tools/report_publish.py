"""Tests de la emision del informe de punta a punta (ADR-019, enmienda de D4).

Sin red, sin S3 y sin WeasyPrint: el ensamblador, la subida y las tres
llamadas al backend se sustituyen por dobles. Lo que se prueba es la
ORQUESTACION, que es donde estan las decisiones: en que orden pasan las cosas,
que se manda al cerrar, y en especial que ningun camino de error deja la fila
abierta.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.backend.reports_client import ErrorDelBackend
from src.config import get_settings
from src.memory.profile_snapshot import PerfilParaLaPuerta
from src.reports.storage import InformeGuardado, ObjetoGuardado
from src.tools.report import publish as modulo
from src.tools.report.publish import publish_orientation_report_handler
from tests.reports.fixtures import informe as informe_de_ejemplo

USUARIO = "u-1"
TOKEN = "jwt-del-estudiante"
INFORME = "22222222-2222-4222-8222-222222222222"

ARGUMENTOS = {
    "riasec_code": "IRC",
    "profile_summary": "Un retrato de dos parrafos.",
    "insights": [
        {"career": "Ingeniería Civil", "insight": "Encaja con lo que le gusta."},
        {"career": "Química Industrial", "insight": "El mismo método, más laboratorio."},
    ],
}

GUARDADO = InformeGuardado(
    bucket="spark-match-reports-dev",
    json=ObjetoGuardado(f"reports/{USUARIO}/{INFORME}.json", "vj", 12_000, "aaa"),
    pdf=ObjetoGuardado(f"reports/{USUARIO}/{INFORME}.pdf", "vp", 240_000, "bbb"),
)

PUERTA_ABIERTA = PerfilParaLaPuerta(profile_completeness=0.75, riasec_code="IRC")


class Espia:
    """Anota cada llamada y devuelve lo que se le haya preparado."""

    def __init__(self, resultado: Any = None, revienta: Exception | None = None) -> None:
        self.resultado = resultado
        self.revienta = revienta
        self.llamadas: list[tuple[tuple, dict]] = []

    async def __call__(self, *args, **kwargs):
        self.llamadas.append((args, kwargs))
        if self.revienta is not None:
            raise self.revienta
        return self.resultado

    @property
    def llamado(self) -> bool:
        return bool(self.llamadas)


@pytest.fixture
def montaje(monkeypatch):
    """El camino feliz entero mockeado. Cada test rompe la pieza que le toca."""
    orden: list[str] = []

    def ensamblar(**_kwargs):
        orden.append("ensamblar")
        return {
            "status": "success",
            "data": informe_de_ejemplo().model_dump(mode="json"),
            "errors": None,
        }

    def subir(*_args, **_kwargs):
        orden.append("subir")
        return GUARDADO

    abrir = Espia({"id": INFORME, "status": "pending"})
    cerrar = Espia({"id": INFORME, "status": "ready"})
    fallar = Espia({"id": INFORME, "status": "failed"})

    async def abrir_anotando(*args, **kwargs):
        orden.append("abrir")
        return await abrir(*args, **kwargs)

    async def cerrar_anotando(*args, **kwargs):
        orden.append("cerrar")
        return await cerrar(*args, **kwargs)

    async def leer_puerta(_store, _user_id):
        return PUERTA_ABIERTA

    monkeypatch.setattr(modulo, "build_orientation_report_handler", ensamblar)
    monkeypatch.setattr(modulo, "upload_report", subir)
    monkeypatch.setattr(modulo, "leer_perfil_para_la_puerta", leer_puerta)
    monkeypatch.setattr(modulo.reports_client, "open_report", abrir_anotando)
    monkeypatch.setattr(modulo.reports_client, "complete_report", cerrar_anotando)
    monkeypatch.setattr(modulo.reports_client, "fail_report", fallar)

    return {
        "orden": orden,
        "abrir": abrir,
        "cerrar": cerrar,
        "fallar": fallar,
        "monkeypatch": monkeypatch,
    }


async def _emitir(**overrides) -> dict[str, Any]:
    argumentos: dict[str, Any] = {
        "user_id": USUARIO,
        "token": TOKEN,
        "store": object(),
        **ARGUMENTOS,
    }
    argumentos.update(overrides)
    return await publish_orientation_report_handler(**argumentos)


class TestCaminoFeliz:
    async def test_devuelve_el_id_y_no_el_informe(self, montaje):
        # El documento pesa decenas de miles de caracteres y volveria a
        # pagarse en cada turno posterior de la conversacion.
        salida = await _emitir()

        assert salida["status"] == "success"
        assert salida["data"]["report_id"] == INFORME
        assert "profile_summary" not in salida["data"]
        assert "careers_matched" not in salida["data"]

    async def test_devuelve_las_carreras_para_poder_anunciarlo(self, montaje):
        salida = await _emitir()

        assert salida["data"]["careers"] == ["Ingeniería Civil", "Química Industrial"]

    async def test_el_orden_es_ensamblar_abrir_subir_cerrar(self, montaje):
        await _emitir()

        assert montaje["orden"] == ["ensamblar", "abrir", "subir", "cerrar"]

    async def test_no_marca_nada_como_fallido(self, montaje):
        await _emitir()

        assert not montaje["fallar"].llamado

    async def test_sube_con_el_id_que_devolvio_el_backend(self, montaje, monkeypatch):
        # Si se subiera con otro id, el objeto quedaria en una clave que la
        # fila no apunta: un informe `ready` que no se puede abrir.
        vistos: list[tuple] = []
        monkeypatch.setattr(
            modulo, "upload_report", lambda *args: (vistos.append(args), GUARDADO)[1]
        )

        await _emitir()

        assert vistos[0][0] == USUARIO
        assert vistos[0][1] == INFORME


class TestPuertaDeCompletitud:
    async def test_las_cifras_salen_del_store_y_no_de_los_argumentos(self, montaje):
        # Son las que deciden el permiso. Pedirselas al modelo seria pedirle
        # que se autoevalue.
        await _emitir()

        _, kwargs = montaje["abrir"].llamadas[0]
        assert kwargs["profile_completeness"] == 0.75
        assert kwargs["riasec_code"] == "IRC"

    async def test_un_rechazo_por_riasec_se_traduce_a_una_instruccion(self, montaje, monkeypatch):
        async def rechazar(*_args, **_kwargs):
            return ErrorDelBackend(409, "report.riasec_missing", "Sin codigo Holland.", {})

        monkeypatch.setattr(modulo.reports_client, "open_report", rechazar)

        salida = await _emitir()

        assert salida["status"] == "error"
        # El modelo tiene que leer que hacer, no un codigo nuestro.
        assert "assessment" in salida["errors"][0]

    async def test_un_perfil_corto_dice_que_preguntar(self, montaje, monkeypatch):
        async def rechazar(*_args, **_kwargs):
            return ErrorDelBackend(
                409, "report.profile_incomplete", "Falta contexto.", {"required": 0.6}
            )

        monkeypatch.setattr(modulo.reports_client, "open_report", rechazar)

        salida = await _emitir()

        assert "edad" in salida["errors"][0]

    async def test_el_tope_diario_no_se_reintenta(self, montaje, monkeypatch):
        async def rechazar(*_args, **_kwargs):
            return ErrorDelBackend(
                429, "report.daily_limit_reached", "Solo 3 al dia.", {"retryAfter": "manana"}
            )

        monkeypatch.setattr(modulo.reports_client, "open_report", rechazar)

        salida = await _emitir()

        assert salida["status"] == "error"
        # El meta viaja porque lleva la hora, que es lo unico accionable.
        assert "manana" in salida["errors"][0]

    async def test_un_codigo_desconocido_sale_tal_cual(self, montaje, monkeypatch):
        # No hace falta enumerar lo imprevisto, pero tampoco tragarselo.
        async def rechazar(*_args, **_kwargs):
            return ErrorDelBackend(500, "internal.unknown", "boom", {})

        monkeypatch.setattr(modulo.reports_client, "open_report", rechazar)

        salida = await _emitir()

        assert "boom" in salida["errors"][0]

    async def test_un_rechazo_no_sube_nada(self, montaje, monkeypatch):
        async def rechazar(*_args, **_kwargs):
            return ErrorDelBackend(409, "report.riasec_missing", "no", {})

        monkeypatch.setattr(modulo.reports_client, "open_report", rechazar)

        await _emitir()

        assert "subir" not in montaje["orden"]


class TestErroresDelModelo:
    async def test_un_ensamblado_fallido_no_abre_la_fila(self, montaje, monkeypatch):
        """La razon por la que se ensambla antes de abrir.

        Los errores del ensamblado son del modelo y se reintentan. Con la fila
        ya abierta, el reintento chocaria contra el indice de un solo
        pendiente y ademas habria gastado una plaza del tope diario por una
        equivocacion que el modelo iba a arreglar solo.
        """
        monkeypatch.setattr(
            modulo,
            "build_orientation_report_handler",
            lambda **_k: {"status": "error", "data": None, "errors": ["falta explicar Derecho"]},
        )

        salida = await _emitir()

        assert salida["errors"] == ["falta explicar Derecho"]
        assert not montaje["abrir"].llamado
        assert not montaje["fallar"].llamado

    async def test_el_error_del_ensamblado_llega_intacto(self, montaje, monkeypatch):
        # Es lo que le dice al modelo que corregir; reescribirlo aqui lo
        # dejaria sin la pista concreta.
        fallo = {"status": "error", "data": None, "errors": ["'Derecho' aparece dos veces"]}
        monkeypatch.setattr(modulo, "build_orientation_report_handler", lambda **_k: fallo)

        salida = await _emitir()

        assert "dos veces" in salida["errors"][0]


class TestFallosDespuesDeAbrir:
    async def test_si_falla_la_subida_se_cierra_la_fila(self, montaje, monkeypatch):
        # Sin esto el estudiante mira "Generando tu reporte..." hasta que el
        # barrido de diez minutos del backend se apiade.
        def reventar(*_args, **_kwargs):
            raise RuntimeError("WeasyPrint no esta disponible")

        monkeypatch.setattr(modulo, "upload_report", reventar)

        salida = await _emitir()

        assert salida["status"] == "error"
        assert montaje["fallar"].llamado
        args, _ = montaje["fallar"].llamadas[0]
        assert args[1] == INFORME
        assert "WeasyPrint" in args[2]

    async def test_si_falla_la_subida_no_se_intenta_cerrar_como_listo(self, montaje, monkeypatch):
        monkeypatch.setattr(
            modulo, "upload_report", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )

        await _emitir()

        assert not montaje["cerrar"].llamado

    async def test_un_fail_que_tambien_falla_no_tapa_el_error_real(self, montaje, monkeypatch):
        """Quien llama ya tiene algo que contarle al estudiante.

        Dejar subir la excepcion del `fail` cambiaria "no se pudo generar el
        PDF" por un fallo del backend, que es la causa equivocada.
        """
        monkeypatch.setattr(
            modulo, "upload_report", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        )
        monkeypatch.setattr(
            modulo.reports_client, "fail_report", Espia(revienta=RuntimeError("tampoco"))
        )

        salida = await _emitir()

        assert salida["status"] == "error"
        assert "no se pudo generar" in salida["errors"][0].lower()

    async def test_un_cierre_fallido_se_cuenta_como_tal(self, montaje, monkeypatch):
        # El objeto esta en S3 pero la fila sigue abierta. El barrido la
        # marcara fallida y el estudiante podra pedir otro.
        async def no_cierra(*_args, **_kwargs):
            return ErrorDelBackend(503, "db.unavailable", "la base no contesta", {})

        monkeypatch.setattr(modulo.reports_client, "complete_report", no_cierra)

        salida = await _emitir()

        assert salida["status"] == "error"
        assert "registrar" in salida["errors"][0]


class TestCuerpoDelCierre:
    async def test_traduce_a_camelCase_lo_que_el_backend_espera(self, montaje):
        await _emitir()

        args, _ = montaje["cerrar"].llamadas[0]
        cuerpo = args[2]
        assert cuerpo["bucket"] == "spark-match-reports-dev"
        assert cuerpo["schemaVersion"]
        assert cuerpo["riasecCode"] == "IRC"
        assert cuerpo["datasetSource"] == "Ponte en Carrera (MINEDU)"
        assert cuerpo["datasetSnapshotDate"] == "2026-06-13"

    async def test_los_dos_objetos_van_con_su_version_y_su_checksum(self, montaje):
        # Sin `versionId` el backend serviria la ultima version de la clave, y
        # el `checksumSha256` de la fila dejaria de describir lo que se sirve.
        await _emitir()

        cuerpo = montaje["cerrar"].llamadas[0][0][2]
        assert cuerpo["objects"]["json"] == {
            "key": f"reports/{USUARIO}/{INFORME}.json",
            "versionId": "vj",
            "sizeBytes": 12_000,
            "checksumSha256": "aaa",
        }
        assert cuerpo["objects"]["pdf"]["key"].endswith(".pdf")

    async def test_las_carreras_van_en_el_orden_del_motor(self, montaje):
        await _emitir()

        cuerpo = montaje["cerrar"].llamadas[0][0][2]
        assert cuerpo["topCareers"] == ["Ingeniería Civil", "Química Industrial"]

    async def test_guarda_la_completitud_con_la_que_se_abrio(self, montaje):
        await _emitir()

        assert montaje["cerrar"].llamadas[0][0][2]["profileCompleteness"] == 0.75

    async def test_guarda_el_modelo_para_poder_auditarlo_sin_la_traza(self, montaje):
        # D10: la traza de LangSmith caduca a los catorce dias; esto no.
        await _emitir()

        assert montaje["cerrar"].llamadas[0][0][2]["modelId"] == get_settings().model_id

    async def test_mide_cuanto_tardo(self, montaje):
        await _emitir()

        assert isinstance(montaje["cerrar"].llamadas[0][0][2]["generationMs"], int)


class TestSinCredencial:
    async def test_sin_token_no_se_toca_el_backend(self, montaje):
        # Pasa en una invocacion directa del grafo y detras del authorizer de
        # API Gateway. No lo arregla un reintento.
        salida = await _emitir(token="")

        assert salida["status"] == "error"
        assert "despliegue" in salida["errors"][0]
        assert not montaje["abrir"].llamado

    async def test_sin_token_ni_siquiera_ensambla(self, montaje):
        await _emitir(token="")

        assert montaje["orden"] == []


class TestBackendSinConfigurar:
    async def test_la_url_ausente_sale_como_error_legible(self, montaje, monkeypatch):
        from src.backend.reports_client import BackendNoConfigurado

        async def sin_url(*_args, **_kwargs):
            raise BackendNoConfigurado("SPARK_BACKEND_API_URL no esta configurada")

        monkeypatch.setattr(modulo.reports_client, "open_report", sin_url)

        salida = await _emitir()

        assert salida["status"] == "error"
        assert "SPARK_BACKEND_API_URL" in salida["errors"][0]


class TestRespuestaRaraDelBackend:
    async def test_un_abierto_sin_id_no_sigue_adelante(self, montaje, monkeypatch):
        async def sin_id(*_args, **_kwargs):
            return {"status": "pending"}

        monkeypatch.setattr(modulo.reports_client, "open_report", sin_id)

        salida = await _emitir()

        assert salida["status"] == "error"
        assert "no devolvio su id" in salida["errors"][0]
        assert "subir" not in montaje["orden"]
