"""Tests del informe en HTML (fase 4' del ADR-019)."""

import re

from src.reports.html import HOJA_DE_ESTILOS, read_stylesheet, report_to_html
from tests.reports.fixtures import carrera, informe


class TestDocumento:
    def test_es_un_documento_completo_y_declara_su_idioma(self):
        # WeasyPrint usa `lang` para la particion de palabras; sin el, corta
        # en espanol con las reglas del ingles.
        html = report_to_html(informe())

        assert html.startswith("<!DOCTYPE html>")
        assert '<html lang="es">' in html
        assert "</html>" in html

    def test_declara_utf8(self):
        html = report_to_html(informe())

        assert '<meta charset="utf-8">' in html


class TestConversion:
    def test_la_ficha_de_cifras_sale_como_tabla(self):
        # El preset commonmark NO trae tablas; hay que habilitarlas a mano, y
        # si alguien quita ese `.enable("table")` la ficha se convierte en un
        # amasijo de barras verticales sin que falle nada.
        html = report_to_html(informe())

        assert "<table>" in html
        assert "<td>" in html

    def test_los_encabezados_conservan_su_jerarquia(self):
        html = report_to_html(informe())

        assert "<h1>" in html
        assert "<h2>" in html
        assert "<h3>" in html

    def test_los_acentos_sobreviven(self):
        html = report_to_html(informe(careers=[carrera(career="Ingeniería Civil")]))

        assert "Ingeniería Civil" in html


class TestHtmlCrudo:
    """El texto libre del informe lo escribe un modelo, y su contexto lleva
    dentro lo que tecleo el estudiante. No hace falta un ataque: basta una
    etiqueta mal cerrada para descuadrar el documento entero."""

    def test_una_etiqueta_en_el_resumen_sale_como_texto(self):
        html = report_to_html(informe(profile_summary="Antes <div style='x'> después"))

        assert "&lt;div" in html
        assert "<div style='x'>" not in html

    def test_una_etiqueta_en_una_explicacion_sale_como_texto(self):
        html = report_to_html(informe(careers=[carrera(insight="Ojo <script>alert(1)</script>")]))

        assert "<script>" not in html
        assert "&lt;script&gt;" in html


class TestHojaDeEstilos:
    def test_la_hoja_existe_y_se_lee(self):
        assert HOJA_DE_ESTILOS.exists()
        assert read_stylesheet().strip()

    def test_no_queda_ni_un_oklch_activo(self):
        # WeasyPrint no entiende `oklch()`: no falla, se salta la declaracion
        # y el elemento se queda con el color heredado. Un informe donde los
        # titulares pierden el color de marca sin un solo error en el log.
        #
        # Se quitan los comentarios antes de mirar: la hoja documenta el valor
        # oklch original al lado de cada hex a proposito, para poder rehacer
        # la conversion, y eso no es una declaracion.
        activo = re.sub(r"/\*.*?\*/", "", read_stylesheet(), flags=re.DOTALL)

        assert "oklch(" not in activo

    def test_pide_la_tipografia_del_producto(self):
        assert "Inter" in read_stylesheet()

    def test_numera_las_paginas_con_el_total(self):
        # Un informe impreso se desordena; "2 de 5" lo dice y "2" no.
        css = read_stylesheet()

        assert "counter(page)" in css
        assert "counter(pages)" in css
