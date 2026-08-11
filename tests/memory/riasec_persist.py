"""El assessment deja escrito lo que midio (ADR-019, D8).

Sin esto, un estudiante terminaba el cuestionario entero, veia su codigo en
pantalla, y al pedir su informe el sistema le decia que todavia no tenia perfil
RIASEC. Reproducido en dev el 2026-08-11: el agente entro en bucle intentando
"registrar" el perfil y el turno murio por limite de recursion.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.memory.riasec_persist import CLAVE_DEL_PERFIL, guardar_riasec_medido
from src.models.profile import StudentProfile

SCORES = {"R": 9, "I": 8, "A": 7, "S": 1, "E": 3, "C": 8}
CODIGO = "RIC"


class ItemFalso:
    def __init__(self, key: str, value: Any) -> None:
        self.key = key
        self.value = value


class StoreFalso:
    """Lo minimo que usa `guardar_riasec_medido`: buscar y escribir."""

    def __init__(self, inicial: Any = None, clave: str = CLAVE_DEL_PERFIL) -> None:
        self.inicial = inicial
        self.clave = clave
        self.escrituras: list[tuple[tuple[str, ...], str, dict[str, Any]]] = []

    async def asearch(self, namespace, limit=1):
        if self.inicial is None:
            return []
        return [ItemFalso(self.clave, self.inicial)]

    async def aput(self, namespace, key, value):
        self.escrituras.append((namespace, key, value))


class StoreRoto(StoreFalso):
    async def aput(self, namespace, key, value):
        raise RuntimeError("store caido")


class TestGuardarElPerfilMedido:
    async def test_escribe_las_seis_puntuaciones_y_el_codigo(self):
        store = StoreFalso()

        assert await guardar_riasec_medido(store, "u-1", SCORES, CODIGO) is True

        _, _, escrito = store.escrituras[0]
        assert escrito["realistic"] == 9
        assert escrito["investigative"] == 8
        assert escrito["artistic"] == 7
        assert escrito["social"] == 1
        assert escrito["enterprising"] == 3
        assert escrito["conventional"] == 8
        assert escrito["riasec_code"] == CODIGO

    async def test_lo_escrito_abre_la_puerta_de_d8(self):
        """La prueba que importa: que el perfil resultante PASE.

        Afirmarlo contra `StudentProfile` y no contra el diccionario es lo que
        detecta que se renombre un campo del modelo: escribir seis claves que
        ya no existen dejaria `has_riasec_profile` en False sin que nada mas
        fallara.
        """
        store = StoreFalso()
        await guardar_riasec_medido(store, "u-1", SCORES, CODIGO)

        _, _, escrito = store.escrituras[0]
        perfil = StudentProfile.model_validate(escrito)

        assert perfil.has_riasec_profile is True
        assert perfil.riasec_code == CODIGO

    async def test_el_usuario_va_en_el_namespace(self):
        store = StoreFalso()
        await guardar_riasec_medido(store, "u-42", SCORES, CODIGO)

        namespace, _, _ = store.escrituras[0]
        assert "u-42" in namespace
        assert "{user_id}" not in namespace

    async def test_no_pisa_lo_que_conto_conversando(self):
        """Fusiona, no reemplaza.

        El nombre, los intereses y los cuatro filtros de busqueda los pone el
        extractor. Reemplazar el perfil entero aqui los borraria en silencio —
        y `max_annual_budget` de vuelta a None significa que el estudiante
        vuelve a ver carreras que no puede pagar.
        """
        store = StoreFalso(
            {
                "name": "Angel",
                "interests": ["diseño", "sistemas"],
                "max_annual_budget": 4500.0,
                "realistic": 2,
            }
        )

        await guardar_riasec_medido(store, "u-1", SCORES, CODIGO)

        _, _, escrito = store.escrituras[0]
        assert escrito["name"] == "Angel"
        assert escrito["interests"] == ["diseño", "sistemas"]
        assert escrito["max_annual_budget"] == 4500.0
        # Y lo que si es suyo, lo pisa: la medida nueva manda sobre la vieja.
        assert escrito["realistic"] == 9

    async def test_reutiliza_la_clave_del_perfil_que_ya_existe(self):
        """Escribir en otra clave crearia un SEGUNDO perfil.

        La puerta lee el primero que devuelve `asearch`, asi que un perfil
        duplicado se manifiesta como "el assessment no sirvio de nada" sin que
        nada falle por ninguna parte.
        """
        store = StoreFalso({"name": "Angel"}, clave="perfil-con-otra-clave")

        await guardar_riasec_medido(store, "u-1", SCORES, CODIGO)

        _, clave, _ = store.escrituras[0]
        assert clave == "perfil-con-otra-clave"

    async def test_sin_perfil_previo_usa_la_clave_por_defecto(self):
        store = StoreFalso()
        await guardar_riasec_medido(store, "u-1", SCORES, CODIGO)

        _, clave, _ = store.escrituras[0]
        assert clave == CLAVE_DEL_PERFIL


class TestNuncaTumbaElTurno:
    """Perder el guardado es malo; perder la respuesta del estudiante, peor."""

    async def test_sin_store_no_revienta(self):
        assert await guardar_riasec_medido(None, "u-1", SCORES, CODIGO) is False

    async def test_un_store_caido_no_revienta(self):
        assert await guardar_riasec_medido(StoreRoto(), "u-1", SCORES, CODIGO) is False

    async def test_un_perfil_guardado_con_forma_rara_no_revienta(self):
        """`asearch` puede devolver algo que no sea un dict."""
        store = StoreFalso("esto no es un perfil")

        assert await guardar_riasec_medido(store, "u-1", SCORES, CODIGO) is True
        _, _, escrito = store.escrituras[0]
        assert escrito["riasec_code"] == CODIGO

    @pytest.mark.parametrize(
        "incompletas",
        [
            {"R": 9, "I": 8},
            {"R": 9, "I": 8, "A": 7, "S": 1, "E": 3},
        ],
    )
    async def test_no_escribe_un_perfil_a_medias(self, incompletas):
        """Con menos de seis, `has_riasec_profile` seguiria en False.

        Escribirlo igual dejaria un perfil que nadie puede usar y que ademas
        tapa el diagnostico: el campo esta, pero la puerta sigue cerrada.
        """
        store = StoreFalso()

        assert await guardar_riasec_medido(store, "u-1", incompletas, CODIGO) is False
        assert store.escrituras == []
