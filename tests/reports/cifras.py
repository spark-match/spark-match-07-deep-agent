"""Tests de como se escribe cada cifra del informe (ADR-019, fase 4')."""

from src.reports.cifras import afinidades, anios, gestion, porcentaje, soles, valor_marcado
from tests.reports.fixtures import carrera


class TestAfinidad:
    def test_sin_empates_va_sin_decimales(self):
        etiquetas = afinidades([carrera(match_score=87.4), carrera(match_score=71.2)])

        assert etiquetas == ["87%", "71%"]

    def test_dos_que_redondean_igual_se_desempatan(self):
        # 71.2 y 70.9 salian las dos como "71%". En una lista numerada eso deja
        # un puesto 1 y un puesto 2 con la misma cifra al lado, y la unica
        # lectura posible es que el orden se lo invento alguien.
        etiquetas = afinidades([carrera(match_score=71.2), carrera(match_score=70.9)])

        assert etiquetas == ["71.2%", "70.9%"]

    def test_el_decimal_se_pone_a_todas_o_a_ninguna(self):
        # Una lista con "71.2%" y "68%" mezclados parece un fallo de formato.
        # Que aparezca el decimal es justo la senal de que dos estaban cerca.
        etiquetas = afinidades(
            [carrera(match_score=71.2), carrera(match_score=70.9), carrera(match_score=68.0)]
        )

        assert etiquetas == ["71.2%", "70.9%", "68.0%"]

    def test_una_sola_carrera_no_empata_consigo_misma(self):
        assert afinidades([carrera(match_score=87.4)]) == ["87%"]

    def test_sin_carreras_no_revienta(self):
        assert afinidades([]) == []


class TestCantidades:
    def test_los_soles_llevan_separador_de_miles(self):
        assert soles(4261.0) == "S/ 4,261"

    def test_una_carrera_de_un_ano_va_en_singular(self):
        assert anios(1.0) == "1 año"

    def test_media_carrera_no_se_redondea_a_entero(self):
        assert anios(3.5) == "3.5 años"

    def test_la_admision_ya_viene_en_porcentaje(self):
        # El contrato decia 0-1 y el dato nunca lo fue: un 17% se enseñaba
        # como 1700%. Ver la descripcion del campo en `ReportCareer`.
        assert porcentaje(17.0) == "17%"


class TestLoEstimado:
    def test_un_dato_imputado_se_marca(self):
        marcado = valor_marcado(carrera(estimated=["monthly_income"]), "monthly_income")

        assert marcado == "S/ 4,261 (estimado)"

    def test_un_dato_publicado_no_lleva_marca(self):
        assert valor_marcado(carrera(estimated=[]), "monthly_income") == "S/ 4,261"


class TestGestion:
    def test_un_instituto_es_publico_y_no_publica(self):
        # El CSV guarda la gestion en femenino, que concuerda con "universidad"
        # y chirria con "instituto". El informe se imprime y se ensena.
        assert (
            gestion(carrera(institution_type="Instituto", management_type="Pública")) == "público"
        )

    def test_una_universidad_conserva_el_femenino(self):
        assert (
            gestion(carrera(institution_type="Universidad", management_type="Pública")) == "pública"
        )
