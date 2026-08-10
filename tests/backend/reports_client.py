"""Tests del cliente HTTP hacia los endpoints de informes del backend.

Sin red: `httpx.AsyncClient` se sustituye por un doble con un `MockTransport`,
que es la via que el propio httpx documenta para esto. Lo que se prueba es la
TRADUCCION -- que cabecera se manda, que cuerpo, y en especial como se convierte
el sobre de error del backend en algo con lo que una herramienta puede razonar.
"""

from __future__ import annotations

import json

import httpx
import pytest

from src.backend import reports_client
from src.backend.reports_client import (
    BackendNoConfigurado,
    ErrorDelBackend,
    complete_report,
    fail_report,
    open_report,
)
from src.config import get_settings

BASE = "https://api-de-prueba.test/dev"
TOKEN = "jwt-del-estudiante"
INFORME = "22222222-2222-4222-8222-222222222222"


@pytest.fixture(autouse=True)
def _backend_configurado(monkeypatch):
    monkeypatch.setenv("SPARK_BACKEND_API_URL", BASE)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _instalar(monkeypatch, handler) -> list[httpx.Request]:
    """Sustituye el AsyncClient por uno con transporte falso. Devuelve las peticiones."""
    vistas: list[httpx.Request] = []

    def registrar(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        return handler(request)

    original = httpx.AsyncClient

    def fabricar(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(registrar)
        return original(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", fabricar)
    return vistas


def _ok(datos: dict) -> httpx.Response:
    return httpx.Response(200, json={"success": True, "data": datos})


def _error(status: int, code: str, message: str, meta: dict | None = None) -> httpx.Response:
    detalle: dict = {"code": code, "message": message}
    if meta is not None:
        detalle["meta"] = meta
    return httpx.Response(
        status,
        json={
            "success": False,
            "error": {"code": "conflict", "message": "generico", "details": [detalle]},
        },
    )


class TestOpenReport:
    async def test_manda_las_dos_cifras_de_la_puerta(self, monkeypatch):
        vistas = _instalar(monkeypatch, lambda _: _ok({"id": INFORME, "status": "pending"}))

        await open_report(TOKEN, profile_completeness=0.75, riasec_code="SIA")

        assert json.loads(vistas[0].content) == {
            "profileCompleteness": 0.75,
            "riasecCode": "SIA",
        }

    async def test_va_con_el_jwt_del_estudiante(self, monkeypatch):
        # El agente reenvia, no firma. Si esta cabecera dejara de salir, el
        # backend abriria el informe a nombre de nadie -- o de quien sea.
        vistas = _instalar(monkeypatch, lambda _: _ok({"id": INFORME}))

        await open_report(TOKEN, profile_completeness=1.0, riasec_code="SIA")

        assert vistas[0].headers["authorization"] == f"Bearer {TOKEN}"

    async def test_pega_a_la_ruta_correcta(self, monkeypatch):
        vistas = _instalar(monkeypatch, lambda _: _ok({"id": INFORME}))

        await open_report(TOKEN, profile_completeness=1.0, riasec_code="SIA")

        assert str(vistas[0].url) == f"{BASE}/v1/reports"

    async def test_devuelve_el_data_desenvuelto(self, monkeypatch):
        _instalar(monkeypatch, lambda _: _ok({"id": INFORME, "status": "pending"}))

        salida = await open_report(TOKEN, profile_completeness=1.0, riasec_code="SIA")

        assert salida == {"id": INFORME, "status": "pending"}

    async def test_un_riasec_ausente_viaja_como_null(self, monkeypatch):
        # Que falte es el caso normal de quien no ha terminado el assessment, y
        # el backend lo espera como null para poder contestar con su 409.
        vistas = _instalar(monkeypatch, lambda _: _ok({"id": INFORME}))

        await open_report(TOKEN, profile_completeness=0.2, riasec_code=None)

        assert json.loads(vistas[0].content)["riasecCode"] is None


class TestTraduccionDeErrores:
    async def test_coge_el_codigo_del_detalle_y_no_el_generico(self, monkeypatch):
        # El de arriba es el del status (`conflict`) y sirve para poco; el de
        # abajo distingue "no tiene RIASEC" de "le falta contexto", que es la
        # unica diferencia que cambia lo que hace el agente despues.
        _instalar(
            monkeypatch,
            lambda _: _error(409, "report.riasec_missing", "Sin codigo Holland todavia."),
        )

        salida = await open_report(TOKEN, profile_completeness=1.0, riasec_code=None)

        assert isinstance(salida, ErrorDelBackend)
        assert salida.code == "report.riasec_missing"
        assert salida.status == 409

    async def test_conserva_el_meta_con_las_cifras(self, monkeypatch):
        # Sin el meta, el agente no puede decir "puedes pedir otro a las diez".
        _instalar(
            monkeypatch,
            lambda _: _error(
                429,
                "report.daily_limit_reached",
                "Solo 3 al dia.",
                {"limit": 3, "used": 3, "retryAfter": "2026-08-11T09:00:00.000Z"},
            ),
        )

        salida = await open_report(TOKEN, profile_completeness=1.0, riasec_code="SIA")

        assert isinstance(salida, ErrorDelBackend)
        assert salida.meta["retryAfter"] == "2026-08-11T09:00:00.000Z"

    async def test_un_cuerpo_que_no_es_json_no_revienta(self, monkeypatch):
        _instalar(monkeypatch, lambda _: httpx.Response(502, text="<html>bad gateway</html>"))

        salida = await open_report(TOKEN, profile_completeness=1.0, riasec_code="SIA")

        assert isinstance(salida, ErrorDelBackend)
        assert salida.status == 502
        assert salida.code == ""

    async def test_un_error_sin_details_cae_al_mensaje_de_arriba(self, monkeypatch):
        _instalar(
            monkeypatch,
            lambda _: httpx.Response(
                500, json={"success": False, "error": {"code": "internal", "message": "boom"}}
            ),
        )

        salida = await open_report(TOKEN, profile_completeness=1.0, riasec_code="SIA")

        assert isinstance(salida, ErrorDelBackend)
        assert salida.message == "boom"

    async def test_la_red_caida_sale_como_503_y_no_como_excepcion(self, monkeypatch):
        # Quien llama esta dentro de un turno de chat: necesita algo que
        # contarle al estudiante, no un traceback.
        def caer(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("no hay ruta al host")

        _instalar(monkeypatch, caer)

        salida = await open_report(TOKEN, profile_completeness=1.0, riasec_code="SIA")

        assert isinstance(salida, ErrorDelBackend)
        assert salida.status == 503
        assert salida.code == "backend.unreachable"

    def test_el_str_del_error_lleva_status_y_codigo(self):
        texto = str(ErrorDelBackend(409, "report.already_closed", "Ya estaba cerrado.", {}))

        assert "409" in texto
        assert "report.already_closed" in texto


class TestCierre:
    async def test_complete_pega_a_la_ruta_del_informe(self, monkeypatch):
        vistas = _instalar(monkeypatch, lambda _: _ok({"id": INFORME, "status": "ready"}))

        await complete_report(TOKEN, INFORME, {"bucket": "b"})

        assert str(vistas[0].url) == f"{BASE}/v1/reports/{INFORME}/complete"

    async def test_fail_manda_el_motivo(self, monkeypatch):
        vistas = _instalar(monkeypatch, lambda _: _ok({"id": INFORME, "status": "failed"}))

        await fail_report(TOKEN, INFORME, "WeasyPrint no esta disponible")

        assert json.loads(vistas[0].content) == {"reason": "WeasyPrint no esta disponible"}

    async def test_fail_recorta_el_motivo_a_500(self, monkeypatch):
        # El backend responde 400 por encima de 500, y esto se llama desde el
        # camino de error: un fallo al reportar un fallo deja la fila colgada.
        vistas = _instalar(monkeypatch, lambda _: _ok({"id": INFORME}))

        await fail_report(TOKEN, INFORME, "x" * 900)

        assert len(json.loads(vistas[0].content)["reason"]) == 500


class TestSinConfigurar:
    async def test_sin_url_del_backend_es_un_error_de_despliegue(self, monkeypatch):
        monkeypatch.delenv("SPARK_BACKEND_API_URL", raising=False)
        get_settings.cache_clear()

        with pytest.raises(BackendNoConfigurado):
            await open_report(TOKEN, profile_completeness=1.0, riasec_code="SIA")

    async def test_la_barra_final_no_duplica_la_de_la_ruta(self, monkeypatch):
        monkeypatch.setenv("SPARK_BACKEND_API_URL", f"{BASE}/")
        get_settings.cache_clear()
        vistas = _instalar(monkeypatch, lambda _: _ok({"id": INFORME}))

        await open_report(TOKEN, profile_completeness=1.0, riasec_code="SIA")

        assert str(vistas[0].url) == f"{BASE}/v1/reports"


def test_los_codigos_conocidos_son_los_del_backend():
    # Si el backend renombra uno, este test no lo detecta -- pero deja escrito
    # cual es el contrato, que es lo que hay que ir a mirar cuando el agente
    # empiece a contestar en generico a un rechazo que antes explicaba.
    assert reports_client.CODIGO_SIN_RIASEC == "report.riasec_missing"
    assert reports_client.CODIGO_PERFIL_CORTO == "report.profile_incomplete"
    assert reports_client.CODIGO_TOPE_DIARIO == "report.daily_limit_reached"
    assert reports_client.CODIGO_YA_EN_CURSO == "report.already_generating"
