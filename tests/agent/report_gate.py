"""Tests de la puerta del informe adelantada.

Lo que hay que proteger aqui son dos cosas que se rompen en direcciones
opuestas:

- Que el `no` llegue **sin** que el subagente arranque. Es la razon de existir:
  44,9 s de redaccion medidos en dev el 2026-08-11 para acabar en un 429.
- Que un fallo de la comprobacion **no** se convierta en un `no`. Esto adelanta
  una puerta, no la sustituye, y equivocarse hacia el "no" le quita a un
  estudiante un informe al que tiene derecho.

El segundo grupo es el que mas casos tiene, y es a proposito.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from src.agent.report_gate import (
    REPORT_SUBAGENT_NAME,
    ReportGateMiddleware,
    _cuando_se_reabre,
    _subagente_de,
    decidir,
)
from src.backend.reports_client import BackendNoConfigurado, ErrorDelBackend
from src.memory.profile_snapshot import PERFIL_VACIO, PerfilParaLaPuerta

PERFIL_OK = PerfilParaLaPuerta(profile_completeness=0.8, riasec_code="SIA")
#: Con el cuestionario hecho y nada mas: el RIASEC solo da 0.50, por debajo del
#: 0.60 de D8. Es el caso mas comun despues del assessment, no una rareza.
PERFIL_CORTO = PerfilParaLaPuerta(profile_completeness=0.5, riasec_code="SIA")

ELEGIBLE: dict[str, Any] = {
    "limit": 3,
    "used": 1,
    "remaining": 2,
    "retryAfter": None,
    "generating": False,
    "minProfileCompleteness": 0.6,
}
EN_EL_TOPE: dict[str, Any] = {
    **ELEGIBLE,
    "used": 3,
    "remaining": 0,
    "retryAfter": "2026-08-12T11:40:00.000Z",
}


class _FakeRuntime:
    def __init__(self, store: Any = None, user_id: str = "u-1") -> None:
        self.store = store
        self.context = {"user_id": user_id}


class _FakeToolCallRequest:
    def __init__(self, tool_call: dict[str, Any], runtime: Any = None) -> None:
        self.tool_call = tool_call
        self.runtime = runtime if runtime is not None else _FakeRuntime()


def _delegacion(subagent_type: str = REPORT_SUBAGENT_NAME, call_id: str = "call_1", **kwargs):
    return _FakeToolCallRequest(
        {
            "name": "task",
            "id": call_id,
            "args": {"description": "redacta el informe", "subagent_type": subagent_type},
        },
        **kwargs,
    )


class TestSubagenteDe:
    def test_otra_herramienta_no_es_una_delegacion(self):
        assert (
            _subagente_de(_FakeToolCallRequest({"name": "recommend_programs", "args": {}})) is None
        )

    def test_lee_el_destino_de_la_delegacion(self):
        assert _subagente_de(_delegacion("matching")) == "matching"

    def test_unos_args_sin_parsear_no_revientan(self):
        # El modelo puede emitir JSON invalido. Ahi no se sabe a quien iba, y
        # deepagents ya contesta a eso con su propio error.
        peticion = _FakeToolCallRequest({"name": "task", "id": "c", "args": '{"roto'})
        assert _subagente_de(peticion) is None


class TestDecidir:
    def test_con_todo_en_regla_deja_pasar(self):
        assert decidir(PERFIL_OK, ELEGIBLE) is None

    def test_sin_riasec_manda_al_assessment(self):
        rechazo = decidir(PERFIL_VACIO, ELEGIBLE)

        assert rechazo is not None
        assert rechazo.motivo == "riasec_missing"
        assert "assessment" in rechazo.mensaje

    def test_el_riasec_se_mira_sin_preguntar_al_backend(self):
        # Es la unica de las cuatro condiciones que el agente sabe solo, y la
        # mas comun en un estudiante nuevo. No debe costar una peticion HTTP.
        rechazo = decidir(PERFIL_VACIO, None)

        assert rechazo is not None
        assert rechazo.motivo == "riasec_missing"

    def test_el_perfil_corto_se_mide_contra_el_umbral_del_backend(self):
        rechazo = decidir(PERFIL_CORTO, ELEGIBLE)

        assert rechazo is not None
        assert rechazo.motivo == "profile_incomplete"

    def test_el_umbral_sale_de_ssm_asi_que_dev_puede_bajarlo(self):
        # Sin esto, el umbral seria una constante duplicada aqui que se
        # desalinearia del backend en cuanto alguien tocara el parametro.
        assert decidir(PERFIL_CORTO, {**ELEGIBLE, "minProfileCompleteness": 0.4}) is None

    def test_una_generacion_viva_pide_esperar_no_reintentar(self):
        rechazo = decidir(PERFIL_OK, {**ELEGIBLE, "generating": True})

        assert rechazo is not None
        assert rechazo.motivo == "already_generating"

    def test_en_el_tope_dice_cuando_podra_pedir_otro(self):
        rechazo = decidir(PERFIL_OK, EN_EL_TOPE)

        assert rechazo is not None
        assert rechazo.motivo == "daily_limit_reached"
        # 11:40Z son las 06:40 en Peru.
        assert "06:40" in rechazo.mensaje

    def test_el_mensaje_del_tope_no_promete_un_reintento(self):
        # El coordinador que promete "lo intento otra vez" deja al estudiante
        # esperando algo que no va a pasar.
        rechazo = decidir(PERFIL_OK, EN_EL_TOPE)

        assert rechazo is not None
        assert "sin prometerle" in rechazo.mensaje

    def test_ningun_mensaje_le_ensena_al_modelo_jerga_nuestra(self):
        # `report.daily_limit_reached` no significa nada para un estudiante de
        # secundaria, y dejar que el modelo lo traduzca es pedirle que
        # improvise sobre un codigo interno.
        for perfil, elegibilidad in (
            (PERFIL_VACIO, ELEGIBLE),
            (PERFIL_CORTO, ELEGIBLE),
            (PERFIL_OK, {**ELEGIBLE, "generating": True}),
            (PERFIL_OK, EN_EL_TOPE),
        ):
            rechazo = decidir(perfil, elegibilidad)
            assert rechazo is not None
            assert "report." not in rechazo.mensaje
            assert rechazo.motivo not in rechazo.mensaje

    def test_el_orden_es_el_del_backend(self):
        # Con dos condiciones incumplidas gana la que el backend comprobaria
        # primero. Si los dos ordenaran distinto, un estudiante que reintenta
        # recibiria dos motivos distintos para la misma negativa.
        rechazo = decidir(PERFIL_VACIO, EN_EL_TOPE)

        assert rechazo is not None
        assert rechazo.motivo == "riasec_missing"


class TestDecidirFallaAbierto:
    """Todo lo que no se sabe se resuelve delegando. Lo contrario quita informes."""

    def test_sin_elegibilidad_solo_se_mira_lo_que_el_agente_sabe(self):
        assert decidir(PERFIL_CORTO, None) is None

    def test_un_bloque_vacio_no_rechaza(self):
        assert decidir(PERFIL_OK, {}) is None

    def test_un_remaining_que_no_es_un_numero_no_rechaza(self):
        assert decidir(PERFIL_OK, {**ELEGIBLE, "remaining": "cero"}) is None

    def test_un_remaining_booleano_no_pasa_por_un_entero(self):
        # `False` es `int` en Python y valdria 0, o sea "sin plazas". Un backend
        # que mande un booleano por error dejaria sin informes a todo el mundo.
        assert decidir(PERFIL_OK, {**ELEGIBLE, "remaining": False}) is None

    def test_un_generating_que_no_es_true_no_rechaza(self):
        assert decidir(PERFIL_OK, {**ELEGIBLE, "generating": "si"}) is None

    def test_un_umbral_que_no_es_un_numero_no_rechaza(self):
        assert decidir(PERFIL_CORTO, {**ELEGIBLE, "minProfileCompleteness": "0.6"}) is None

    def test_en_el_tope_sin_fecha_legible_sigue_rechazando(self):
        # La fecha adorna el mensaje; el tope no depende de ella.
        rechazo = decidir(PERFIL_OK, {**EN_EL_TOPE, "retryAfter": "el jueves"})

        assert rechazo is not None
        assert rechazo.motivo == "daily_limit_reached"


class TestCuandoSeReabre:
    def test_traduce_a_hora_de_peru(self):
        # El backend manda UTC y el estudiante vive en UTC-5. Darle la hora
        # cruda son cinco horas de error esperando a que alguien las cometa.
        assert "06:40" in _cuando_se_reabre("2026-08-12T11:40:00.000Z")

    def test_una_fecha_ilegible_no_revienta(self):
        assert _cuando_se_reabre("mañana por la tarde") == ""

    def test_sin_fecha_no_hay_frase(self):
        assert _cuando_se_reabre(None) == ""
        assert _cuando_se_reabre("") == ""

    def test_una_fecha_sin_zona_se_lee_como_utc(self):
        # El backend siempre manda `Z`, pero un ISO sin zona interpretado como
        # hora local movería la respuesta según dónde corra el contenedor.
        assert "06:40" in _cuando_se_reabre("2026-08-12T11:40:00")


class _Registro:
    """Un handler que anota si llegaron a llamarlo."""

    def __init__(self) -> None:
        self.llamado = False

    async def __call__(self, request: Any) -> str:
        self.llamado = True
        return "el subagente escribio el informe"


@pytest.fixture
def fijar_perfil(monkeypatch):
    """El perfil que lea la puerta, sin tocar el store de verdad."""

    def fijar(perfil: PerfilParaLaPuerta) -> None:
        async def leer(_store, _user_id):
            return perfil

        monkeypatch.setattr("src.agent.report_gate.leer_perfil_para_la_puerta", leer)

    return fijar


@pytest.fixture
def fijar_elegibilidad(monkeypatch):
    """Lo que conteste el backend, sin red. Devuelve la lista de consultas hechas."""

    def fijar(respuesta: Any) -> list[str]:
        consultas: list[str] = []

        async def consultar(token):
            consultas.append(token)
            if isinstance(respuesta, Exception):
                raise respuesta
            return respuesta

        monkeypatch.setattr("src.agent.report_gate.reports_client.report_eligibility", consultar)
        monkeypatch.setattr("src.agent.report_gate.get_request_token", lambda: "jwt")
        return consultas

    return fijar


class TestReportGateMiddleware:
    async def test_otra_delegacion_no_se_toca(self, fijar_perfil, fijar_elegibilidad):
        # La puerta es del informe. Un rechazo colado en la delegación de
        # matching dejaría al estudiante sin carreras por una regla que no le
        # aplica.
        fijar_perfil(PERFIL_VACIO)
        fijar_elegibilidad(EN_EL_TOPE)
        handler = _Registro()

        await ReportGateMiddleware().awrap_tool_call(_delegacion("matching"), handler)

        assert handler.llamado

    async def test_otra_herramienta_no_se_toca(self, fijar_perfil, fijar_elegibilidad):
        fijar_perfil(PERFIL_VACIO)
        fijar_elegibilidad(EN_EL_TOPE)
        handler = _Registro()

        peticion = _FakeToolCallRequest({"name": "recommend_programs", "id": "c", "args": {}})
        await ReportGateMiddleware().awrap_tool_call(peticion, handler)

        assert handler.llamado

    async def test_con_todo_en_regla_delega(self, fijar_perfil, fijar_elegibilidad):
        fijar_perfil(PERFIL_OK)
        fijar_elegibilidad(ELEGIBLE)
        handler = _Registro()

        salida = await ReportGateMiddleware().awrap_tool_call(_delegacion(), handler)

        assert handler.llamado
        assert salida == "el subagente escribio el informe"

    async def test_sin_riasec_no_se_le_pregunta_al_backend(self, fijar_perfil, fijar_elegibilidad):
        # Lo que se decide sin salir de aqui se decide sin salir de aqui. Es el
        # caso mas comun de los cuatro -- un estudiante que aun no ha hecho el
        # cuestionario -- y pedir su tope para tirar la respuesta es pagar una
        # peticion por nada.
        fijar_perfil(PERFIL_VACIO)
        consultas = fijar_elegibilidad(ELEGIBLE)
        handler = _Registro()

        await ReportGateMiddleware().awrap_tool_call(_delegacion(), handler)

        assert not handler.llamado
        assert consultas == []

    async def test_en_el_tope_el_subagente_no_arranca(self, fijar_perfil, fijar_elegibilidad):
        # Esto es lo que ahorra los 44,9 s.
        fijar_perfil(PERFIL_OK)
        fijar_elegibilidad(EN_EL_TOPE)
        handler = _Registro()

        salida = await ReportGateMiddleware().awrap_tool_call(_delegacion(), handler)

        assert not handler.llamado
        assert isinstance(salida, ToolMessage)
        assert "06:40" in str(salida.content)

    async def test_el_rechazo_contesta_a_la_llamada_que_lo_provoco(
        self, fijar_perfil, fijar_elegibilidad
    ):
        # Sin el `tool_call_id` correcto, la `tool_call` se queda sin respuesta
        # y Anthropic rechaza el historial entero en el turno siguiente.
        fijar_perfil(PERFIL_OK)
        fijar_elegibilidad(EN_EL_TOPE)

        salida = await ReportGateMiddleware().awrap_tool_call(
            _delegacion(call_id="call_42"), _Registro()
        )

        assert isinstance(salida, ToolMessage)
        assert salida.tool_call_id == "call_42"

    async def test_el_rechazo_no_va_marcado_como_error(self, fijar_perfil, fijar_elegibilidad):
        # Para el coordinador no ha fallado nada: la respuesta a "emiteme el
        # informe" es que hoy no toca. Marcarlo como error le invita a
        # reintentar la delegacion que acabamos de evitar.
        fijar_perfil(PERFIL_OK)
        fijar_elegibilidad(EN_EL_TOPE)

        salida = await ReportGateMiddleware().awrap_tool_call(_delegacion(), _Registro())

        assert isinstance(salida, ToolMessage)
        assert salida.status != "error"


class TestElMiddlewareFallaAbierto:
    async def test_sin_token_delega(self, fijar_perfil, monkeypatch):
        # Pasa en una invocacion directa del grafo. No es culpa del estudiante
        # y no puede costarle su informe.
        fijar_perfil(PERFIL_OK)
        monkeypatch.setattr("src.agent.report_gate.get_request_token", lambda: "")
        handler = _Registro()

        await ReportGateMiddleware().awrap_tool_call(_delegacion(), handler)

        assert handler.llamado

    async def test_sin_backend_configurado_delega(self, fijar_perfil, fijar_elegibilidad):
        fijar_perfil(PERFIL_OK)
        fijar_elegibilidad(BackendNoConfigurado("no hay URL"))
        handler = _Registro()

        await ReportGateMiddleware().awrap_tool_call(_delegacion(), handler)

        assert handler.llamado

    async def test_un_backend_caido_delega(self, fijar_perfil, fijar_elegibilidad):
        fijar_perfil(PERFIL_OK)
        fijar_elegibilidad(ErrorDelBackend(503, "backend.unreachable", "sin ruta", {}))
        handler = _Registro()

        await ReportGateMiddleware().awrap_tool_call(_delegacion(), handler)

        assert handler.llamado

    async def test_una_excepcion_inesperada_delega(self, fijar_perfil, fijar_elegibilidad):
        fijar_perfil(PERFIL_OK)
        fijar_elegibilidad(RuntimeError("algo raro"))
        handler = _Registro()

        await ReportGateMiddleware().awrap_tool_call(_delegacion(), handler)

        assert handler.llamado

    async def test_sin_store_el_riasec_sigue_cortando(self, fijar_elegibilidad):
        # `leer_perfil_para_la_puerta` sin store devuelve el perfil vacio, y eso
        # SI es un rechazo legitimo: el backend contestaria lo mismo. Falla
        # cerrado hacia el lado del que se sale conversando.
        fijar_elegibilidad(ELEGIBLE)
        handler = _Registro()

        salida = await ReportGateMiddleware().awrap_tool_call(_delegacion(), handler)

        assert not handler.llamado
        assert isinstance(salida, ToolMessage)
        assert "assessment" in str(salida.content)
