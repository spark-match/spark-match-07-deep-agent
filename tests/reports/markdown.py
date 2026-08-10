"""Tests del informe en Markdown (fase 4' del ADR-019).

El renderizado es donde las promesas del ADR se vuelven visibles o se pierden
en silencio. Un `estimated` que no se imprime no da error: da un informe que
presenta una mediana de familia como si fuera el sueldo de esa carrera.
"""

from src.reports.markdown import report_to_markdown
from tests.reports.fixtures import carrera, informe


class TestLoQueNoPuedeFaltar:
    """Las tres advertencias que el ADR obliga a dar, ahora por escrito."""

    def test_dice_que_la_afinidad_no_es_del_minedu(self):
        # En el chat el agente lo matiza cuando hace falta. En un documento
        # que se ensena en casa no hay nadie para matizarlo.
        texto = report_to_markdown(informe())

        assert "MINEDU" in texto
        assert "Spark Match" in texto

    def test_dice_de_donde_salen_las_cifras_y_de_cuando(self):
        texto = report_to_markdown(informe())

        assert "Ponte en Carrera" in texto
        assert "2026-06-13" in texto

    def test_guarda_la_version_del_criterio_de_puntuacion(self):
        # Los pesos pueden cambiar; un informe de hoy tiene que poder
        # explicarse con las reglas de hoy.
        texto = report_to_markdown(informe(scoring_version="9.9.9"))

        assert "9.9.9" in texto

    def test_avisa_de_que_el_riasec_de_la_carrera_lo_puso_un_modelo(self):
        texto = report_to_markdown(informe())

        assert "modelo de lenguaje" in texto


class TestMedidoContraEstimado:
    """La distincion mas facil de perder y la que mas cambia una decision."""

    def test_una_cifra_estimada_se_marca(self):
        texto = report_to_markdown(
            informe(careers=[carrera(monthly_income=3590.0, estimated=["monthly_income"])])
        )

        assert "S/ 3,590 (estimado)" in texto

    def test_una_cifra_medida_no_se_marca(self):
        texto = report_to_markdown(informe(careers=[carrera(monthly_income=4261.0, estimated=[])]))

        assert "S/ 4,261 |" in texto
        assert "S/ 4,261 (estimado)" not in texto

    def test_se_explica_que_significa_estimado(self):
        # Marcarlo sin explicarlo deja al lector adivinando.
        texto = report_to_markdown(informe())

        assert "mediana de su familia de carrera" in texto


class TestFormatoDeCifras:
    """Las mismas convenciones que la web: si difieren, parece de otro sitio."""

    def test_los_soles_llevan_separador_de_miles(self):
        texto = report_to_markdown(informe(careers=[carrera(monthly_income=4261.0)]))

        assert "S/ 4,261" in texto

    def test_la_admision_se_lee_en_porcentaje(self):
        # Viaja como 0-1 y nadie lee "0.03" como "3 de cada 100".
        texto = report_to_markdown(informe(careers=[carrera(admission_rate=0.03)]))

        assert "3%" in texto

    def test_los_anios_se_dicen_en_singular_cuando_es_uno(self):
        texto = report_to_markdown(informe(careers=[carrera(duration_years=1.0)]))

        assert "1 año |" in texto
        assert "1 años" not in texto

    def test_media_carrera_no_se_redondea_a_entero(self):
        texto = report_to_markdown(informe(careers=[carrera(duration_years=3.5)]))

        assert "3.5 años" in texto

    def test_la_afinidad_sale_como_porcentaje_entero(self):
        texto = report_to_markdown(informe(careers=[carrera(match_score=87.4)]))

        assert "87% de afinidad" in texto


class TestLaProsaDelModelo:
    def test_el_retrato_del_perfil_sale_entero(self):
        resumen = "Un párrafo muy concreto sobre esta persona."
        texto = report_to_markdown(informe(profile_summary=resumen))

        assert resumen in texto

    def test_cada_carrera_lleva_su_explicacion(self):
        texto = report_to_markdown(
            informe(
                careers=[
                    carrera(career="Uno", insight="Explicación de la primera."),
                    carrera(career="Dos", insight="Explicación de la segunda."),
                ]
            )
        )

        assert "Explicación de la primera." in texto
        assert "Explicación de la segunda." in texto


class TestEstructura:
    def test_las_carreras_van_numeradas_en_el_orden_del_informe(self):
        texto = report_to_markdown(
            informe(careers=[carrera(career="Primera"), carrera(career="Segunda")])
        )

        assert texto.index("1. Primera") < texto.index("2. Segunda")

    def test_cada_carrera_dice_donde_se_estudia(self):
        texto = report_to_markdown(
            informe(
                careers=[
                    carrera(
                        institution="Instituto Pedro P. Díaz",
                        location="Arequipa",
                        institution_type="Instituto",
                        management_type="Pública",
                    )
                ]
            )
        )

        assert "Instituto Pedro P. Díaz · Arequipa · Instituto público" in texto

    def test_la_gestion_concuerda_con_el_tipo_de_institucion(self):
        # El CSV guarda "Publica" en femenino, que concuerda con "universidad"
        # y chirria con "instituto". Como esto se imprime y se ensena en casa,
        # la concordancia no es un detalle.
        universidad = report_to_markdown(
            informe(careers=[carrera(institution_type="Universidad", management_type="Pública")])
        )
        instituto = report_to_markdown(
            informe(careers=[carrera(institution_type="Instituto", management_type="Pública")])
        )

        assert "Universidad pública" in universidad
        assert "Instituto público" in instituto
        assert "Instituto pública" not in instituto


class TestLosFiltros:
    """Un filtro no da una respuesta mala: borra opciones en silencio."""

    def test_con_filtros_dice_cuanto_recortaron(self):
        texto = report_to_markdown(
            informe(
                filters_applied=["region"],
                total_candidates=411,
                candidates_without_each_filter={"region": 6208},
            )
        )

        assert "411" in texto
        assert "6,208" in texto
        assert "Cambiar un filtro cambia esta lista" in texto

    def test_los_filtros_se_nombran_en_castellano_y_no_por_su_clave(self):
        # `management_type` y `max_annual_cost` son identificadores del codigo.
        # En un documento que lee un chico de dieciseis anos no pintan nada.
        texto = report_to_markdown(
            informe(
                filters_applied=["management_type", "max_annual_cost", "region"],
                candidates_without_each_filter={
                    "management_type": 900,
                    "max_annual_cost": 1200,
                    "region": 6208,
                },
            )
        )

        assert "management_type" not in texto
        assert "max_annual_cost" not in texto
        assert "si es pública o privada" in texto
        assert "el presupuesto" in texto
        assert "la región" in texto

    def test_sin_filtros_lo_dice_igualmente(self):
        # El silencio se leeria como "esto es todo el catalogo", que es
        # verdad aqui pero no cuando hay filtros; decirlo siempre quita la
        # ambiguedad de las dos.
        texto = report_to_markdown(
            informe(filters_applied=[], candidates_without_each_filter={}, total_candidates=6208)
        )

        assert "No se aplicó ningún filtro" in texto
        assert "6,208" in texto


class TestSalida:
    def test_empieza_por_el_titulo_y_termina_en_salto(self):
        texto = report_to_markdown(informe())

        assert texto.startswith("# Informe de orientación vocacional")
        assert texto.endswith("\n")
