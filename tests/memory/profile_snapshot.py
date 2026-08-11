"""Tests de la lectura del perfil que alimenta la puerta de D8.

La propiedad que importa es que **falla cerrado**: cualquier cosa rara -- sin
store, sin perfil, un perfil con una forma que Pydantic no reconoce, el store
caido -- sale como perfil vacio, y el backend contesta "todavia no te toca
informe". Es el lado del que se puede salir conversando; el contrario seria
emitir un informe sobre un perfil que no sabemos leer.
"""

from __future__ import annotations

from typing import Any

from src.memory.profile_snapshot import PERFIL_VACIO, leer_perfil_para_la_puerta

USUARIO = "u-1"

#: Las seis puntuaciones RIASEC completas, que es lo que hace
#: `has_riasec_profile` verdadero.
SEIS_PUNTUACIONES = {
    "realistic": 7,
    "investigative": 9,
    "artistic": 4,
    "social": 6,
    "enterprising": 3,
    "conventional": 5,
}


class _Item:
    def __init__(self, value: Any) -> None:
        self.key = "profile"
        self.value = value


class FakeStore:
    """Devuelve lo que se le diga, y anota el namespace que le pidieron."""

    def __init__(self, valor: Any = None, revienta: bool = False) -> None:
        self._valor = valor
        self._revienta = revienta
        self.namespace: tuple[str, ...] | None = None

    async def asearch(self, namespace, limit=1):
        self.namespace = namespace
        if self._revienta:
            raise RuntimeError("el store no contesta")
        return [] if self._valor is None else [_Item(self._valor)]


async def test_sin_store_es_perfil_vacio():
    assert await leer_perfil_para_la_puerta(None, USUARIO) == PERFIL_VACIO


async def test_sin_perfil_guardado_es_perfil_vacio():
    # El caso de quien no ha conversado nunca. El backend lo rechaza con
    # `report.riasec_missing`, que es la respuesta correcta.
    assert await leer_perfil_para_la_puerta(FakeStore(), USUARIO) == PERFIL_VACIO


async def test_el_namespace_lleva_el_user_id_resuelto():
    # Sin esto se leeria el perfil literal de "{user_id}", que no existe, y
    # cualquiera veria la puerta cerrada.
    store = FakeStore({"name": "Ana"})

    await leer_perfil_para_la_puerta(store, USUARIO)

    assert store.namespace == ("spark-match", USUARIO, "profile")


async def test_la_completitud_sale_del_modelo_y_no_de_una_cuenta_de_aqui():
    # Se valida contra `StudentProfile` justamente para que la definicion de
    # "perfil completo" viva en un solo sitio.
    store = FakeStore({"name": "Ana", "age": 17, **SEIS_PUNTUACIONES, "riasec_code": "IRC"})

    puerta = await leer_perfil_para_la_puerta(store, USUARIO)

    # 8 de 12 puntos: nombre, edad y las seis puntuaciones; sin nivel
    # educativo y sin intereses.
    assert puerta.profile_completeness == 0.67


async def test_devuelve_el_codigo_cuando_las_seis_puntuaciones_estan():
    store = FakeStore({**SEIS_PUNTUACIONES, "riasec_code": "IRC"})

    assert (await leer_perfil_para_la_puerta(store, USUARIO)).riasec_code == "IRC"


async def test_un_codigo_sin_sus_puntuaciones_no_cuenta():
    """`riasec_code` es un campo guardado, no una property derivada.

    O sea que puede quedarse de una version anterior del perfil despues de que
    las puntuaciones se hayan perdido o cambiado. Servirlo tal cual seria
    puntuar la afinidad contra un codigo que ya no describe a nadie, asi que
    se exige que las seis lo respalden.
    """
    store = FakeStore({"riasec_code": "IRC", "realistic": 7})

    assert (await leer_perfil_para_la_puerta(store, USUARIO)).riasec_code is None


async def test_las_seis_puntuaciones_sin_codigo_tampoco_inventan_uno():
    # Derivar el codigo aqui seria una segunda implementacion del calculo que
    # ya hace el assessment, y las dos se separarian.
    store = FakeStore(dict(SEIS_PUNTUACIONES))

    assert (await leer_perfil_para_la_puerta(store, USUARIO)).riasec_code is None


async def test_un_perfil_con_forma_rara_es_perfil_vacio():
    store = FakeStore({"realistic": "muchisimo"})

    assert await leer_perfil_para_la_puerta(store, USUARIO) == PERFIL_VACIO


async def test_un_valor_que_no_es_dict_es_perfil_vacio():
    store = FakeStore("esto no es un perfil")

    assert await leer_perfil_para_la_puerta(store, USUARIO) == PERFIL_VACIO


class TestElPerfilVieneEnUnSobre:
    """El fallo que dejaba sin informe a quien si tenia perfil.

    langmem no guarda el `StudentProfile` tal cual, lo envuelve en
    `{"kind", "content"}`. Validar el sobre no lanza --en `StudentProfile` no
    hay un solo campo obligatorio-- sino que devuelve un perfil entero a
    `None`, asi que esta puerta contestaba `riasec_missing` y completitud 0.0
    a un estudiante con las seis puntuaciones guardadas. Sin excepcion, sin
    warning y sin traza: el unico sintoma era "no se pudo generar el reporte".

    Medido en dev el 2026-08-11.
    """

    def _sobre(self, contenido):
        return {"kind": "StudentProfile", "content": contenido}

    async def test_el_codigo_se_lee_de_dentro_del_sobre(self):
        store = FakeStore(self._sobre({**SEIS_PUNTUACIONES, "riasec_code": "IRC"}))

        assert (await leer_perfil_para_la_puerta(store, USUARIO)).riasec_code == "IRC"

    async def test_la_completitud_se_cuenta_sobre_el_contenido(self):
        store = FakeStore(
            self._sobre({"name": "Ana", "age": 17, **SEIS_PUNTUACIONES, "riasec_code": "IRC"})
        )

        puerta = await leer_perfil_para_la_puerta(store, USUARIO)

        assert puerta.profile_completeness == 0.67

    async def test_el_sobre_sin_abrir_daba_perfil_vacio(self):
        """La forma exacta del fallo, fijada para que no vuelva.

        Si alguien quitara el desempaquetado, este es el test que lo dice: el
        mismo perfil que el test de arriba lee con codigo y 0.67, leido sin
        abrir el sobre sale como PERFIL_VACIO.
        """
        from src.models.profile import StudentProfile

        sobre = self._sobre({**SEIS_PUNTUACIONES, "riasec_code": "IRC"})

        # Valida sin protestar, y sale un perfil que no es el de nadie.
        colado = StudentProfile.model_validate(sobre)

        assert colado.has_riasec_profile is False
        assert colado.profile_completeness == 0.0

    async def test_un_perfil_suelto_se_sigue_leyendo(self):
        # Lo que queda si algo escribio en el namespace antes de que el
        # extractor pasara por primera vez.
        store = FakeStore({**SEIS_PUNTUACIONES, "riasec_code": "IRC"})

        assert (await leer_perfil_para_la_puerta(store, USUARIO)).riasec_code == "IRC"

    async def test_un_sobre_vacio_por_dentro_es_perfil_vacio(self):
        store = FakeStore({"kind": "StudentProfile", "content": {}})

        assert await leer_perfil_para_la_puerta(store, USUARIO) == PERFIL_VACIO


async def test_el_store_caido_no_tumba_el_turno():
    # Un informe que no se puede emitir es recuperable; una excepcion a mitad
    # de turno se lleva por delante la conversacion entera.
    assert await leer_perfil_para_la_puerta(FakeStore(revienta=True), USUARIO) == PERFIL_VACIO


async def test_las_preferencias_no_suben_la_completitud():
    # ADR-019 D12: quien no ha dicho presupuesto merece su informe igual.
    solo_riasec = FakeStore({**SEIS_PUNTUACIONES})
    con_preferencias = FakeStore(
        {
            **SEIS_PUNTUACIONES,
            "preferred_region": "Arequipa",
            "preferred_management": "publica",
            "preferred_institution_type": "universidad",
            "max_annual_budget": 3000,
        }
    )

    sin = await leer_perfil_para_la_puerta(solo_riasec, USUARIO)
    con = await leer_perfil_para_la_puerta(con_preferencias, USUARIO)

    assert sin.profile_completeness == con.profile_completeness
