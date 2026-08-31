"""Tests del sobre `{kind, content}` en que langmem guarda el perfil.

Lo que se fija aqui es que abrir el sobre y escribir dentro de el sean **una
sola** definicion. Los cuatro lectores del perfil habian llegado cada uno por
su cuenta a la suposicion de que el valor del store era el perfil, y con
Pydantic esa suposicion no revienta: valida y devuelve un perfil vacio.
"""

from __future__ import annotations

from src.memory.profile_envelope import con_campos, perfil_de

#: Un item tal y como lo deja `create_memory_store_manager`.
SOBRE = {
    "kind": "StudentProfile",
    "content": {"age": 22, "realistic": 8, "riasec_code": "IRA"},
}


class TestAbrirElSobre:
    def test_saca_el_perfil_de_dentro(self):
        assert perfil_de(SOBRE) == {"age": 22, "realistic": 8, "riasec_code": "IRA"}

    def test_un_perfil_suelto_pasa_tal_cual(self):
        """Lo que queda si algo escribio antes de la primera extraccion."""
        suelto = {"age": 22, "realistic": 8}

        assert perfil_de(suelto) == suelto

    def test_lo_que_no_es_diccionario_no_dice_nada_de_nadie(self):
        # Los lectores de esto deciden permisos: la respuesta segura ante algo
        # ilegible es "no se sabe nada", no una excepcion a mitad de turno.
        assert perfil_de(None) == {}
        assert perfil_de("esto no es un perfil") == {}
        assert perfil_de([{"age": 22}]) == {}

    def test_un_sobre_vacio_por_dentro_sale_vacio(self):
        assert perfil_de({"kind": "StudentProfile", "content": {}}) == {}

    def test_el_content_que_no_es_diccionario_no_se_trata_como_sobre(self):
        # Un `StudentProfile` no tiene campo `content`, asi que esto no puede
        # ser un perfil de verdad; lo que no puede es explotar.
        assert perfil_de({"content": "texto"}) == {"content": "texto"}

    def test_devuelve_una_copia(self):
        """Quien lo lea no puede tocar sin querer lo que hay en el store."""
        sobre = {"kind": "StudentProfile", "content": {"age": 22}}

        perfil_de(sobre)["age"] = 99

        assert sobre["content"]["age"] == 22


class TestEscribirDentroDelSobre:
    def test_los_campos_entran_en_content_y_el_sobre_se_conserva(self):
        resultado = con_campos(SOBRE, {"social": 5})

        assert resultado["kind"] == "StudentProfile"
        assert resultado["content"]["social"] == 5
        assert resultado["content"]["age"] == 22

    def test_no_deja_nada_en_la_raiz(self):
        """El fallo original: las seis puntuaciones acababan al lado del sobre.

        Desde ahi no las veia ni la completitud de D8 --que se calcula sobre el
        contenido-- ni el extractor de langmem al actualizar el perfil.
        """
        resultado = con_campos(SOBRE, {"social": 5})

        assert "social" not in resultado

    def test_sobre_un_perfil_suelto_fusiona_en_la_raiz(self):
        resultado = con_campos({"age": 22}, {"social": 5})

        assert resultado == {"age": 22, "social": 5}

    def test_sin_nada_previo_crea_el_perfil(self):
        assert con_campos(None, {"social": 5}) == {"social": 5}

    def test_los_campos_nuevos_pisan_a_los_viejos(self):
        resultado = con_campos(SOBRE, {"riasec_code": "RIC"})

        assert resultado["content"]["riasec_code"] == "RIC"

    def test_no_muta_lo_que_recibe(self):
        sobre = {"kind": "StudentProfile", "content": {"age": 22}}

        con_campos(sobre, {"social": 5})

        assert sobre == {"kind": "StudentProfile", "content": {"age": 22}}
