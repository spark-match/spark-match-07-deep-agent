"""Tests del informe en HTML (fase 4' del ADR-019)."""

import re
from datetime import date

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

    def test_los_acentos_sobreviven(self):
        html = report_to_html(informe(careers=[carrera(career="Ingeniería Civil"), carrera()]))

        assert "Ingeniería Civil" in html


class TestPortada:
    def test_lleva_la_marca(self):
        html = report_to_html(informe())

        assert 'class="marca"' in html
        assert "Spark Match" in html

    def test_el_codigo_riasec_sale_desplegado_con_sus_nombres(self):
        # "IRA" es la etiqueta interna de un perfil y no significa nada para
        # quien abre el informe. Es lo primero que el documento tiene que
        # explicar, no lo primero que da por sabido.
        html = report_to_html(informe(riasec_code="IRA"))

        assert "Investigativo" in html
        assert "Realista" in html
        assert "Artístico" in html

    def test_una_letra_que_no_es_de_las_seis_no_tumba_el_informe(self):
        # Quedarse sin PDF por un caracter suelto en un codigo que ya paso
        # todas las validaciones de antes seria un mal cambio.
        html = report_to_html(informe(riasec_code="IRZ"))

        assert ">Z<" in html

    def test_lleva_la_fecha_de_emision(self):
        html = report_to_html(informe(issued_on=date(2026, 8, 11)))

        assert "11 de agosto de 2026" in html

    def test_un_informe_viejo_sin_fecha_se_ensena_sin_ella(self):
        # `issued_on` es opcional porque los informes emitidos antes de que
        # existiera el campo estan en S3 y se tienen que poder releer. Ponerles
        # la de hoy al abrirlos seria inventarse un dato.
        html = report_to_html(informe(issued_on=None))

        assert "fecha de emisión" not in html

    def test_dice_cuantas_carreras_y_contra_cuantos_programas(self):
        html = report_to_html(informe(total_candidates=411))

        assert "carreras recomendadas" in html
        assert "411" in html


class TestFichaDeCarrera:
    def test_cada_carrera_lleva_su_puesto(self):
        # El puesto en el ranking es la mitad de la informacion de una lista de
        # diez, y en la version de Markdown solo existia como el "3." de un
        # encabezado.
        html = report_to_html(informe(careers=[carrera(career="Uno"), carrera(career="Dos")]))

        assert '<span class="ficha__puesto">1</span>' in html
        assert '<span class="ficha__puesto">2</span>' in html

    def test_la_afinidad_va_tambien_como_barra(self):
        html = report_to_html(informe(careers=[carrera(match_score=87.4), carrera()]))

        assert 'style="width: 87.4%"' in html

    def test_una_puntuacion_fuera_de_rango_no_se_sale_del_papel(self):
        # El ancho acaba en un atributo `style`. La puntuacion sale del motor y
        # ya viene en 0-100, pero si algun dia cambia el rango preferimos una
        # barra llena a una barra al 340%, que nadie ve hasta que imprime.
        html = report_to_html(informe(careers=[carrera(match_score=340.0), carrera()]))

        assert 'style="width: 100.0%"' in html
        assert 'style="width: 340' not in html

    def test_las_cifras_salen_como_bloques_y_no_como_tabla(self):
        html = report_to_html(informe())

        assert 'class="cifras"' in html
        assert 'class="cifra__valor"' in html
        assert "<table>" not in html

    def test_un_dato_imputado_se_marca_como_estimado(self):
        # Sin la marca, un ingreso imputado es indistinguible de uno publicado.
        html = report_to_html(informe(careers=[carrera(estimated=["monthly_income"]), carrera()]))

        assert 'class="cifra__estimado"' in html

    def test_dos_carreras_que_empatan_al_redondear_se_desempatan(self):
        html = report_to_html(
            informe(careers=[carrera(match_score=71.2), carrera(match_score=70.9)])
        )

        assert "71.2% de afinidad" in html
        assert "70.9% de afinidad" in html


class TestProcedencia:
    def test_dice_que_la_afinidad_no_es_del_minedu(self):
        html = report_to_html(informe())

        assert "no una cifra oficial del MINEDU" in html

    def test_nombra_los_filtros_en_castellano(self):
        # Los filtros viajan con su nombre de codigo. "Se filtro por
        # management_type" en un documento que lee un chico de dieciseis anos.
        html = report_to_html(
            informe(
                filters_applied=["management_type"],
                candidates_without_each_filter={"management_type": 900},
            )
        )

        assert "si es pública o privada" in html
        assert "management_type" not in html


class TestHtmlCrudo:
    """El texto libre del informe lo escribe un modelo, y su contexto lleva
    dentro lo que tecleo el estudiante. No hace falta un ataque: basta una
    etiqueta mal cerrada para descuadrar el documento entero."""

    def test_una_etiqueta_en_el_resumen_sale_como_texto(self):
        html = report_to_html(informe(profile_summary="Antes <div style='x'> después"))

        assert "&lt;div" in html
        assert "<div style='x'>" not in html

    def test_una_etiqueta_en_una_explicacion_sale_como_texto(self):
        html = report_to_html(
            informe(careers=[carrera(insight="Ojo <script>alert(1)</script>"), carrera()])
        )

        assert "<script>" not in html
        assert "&lt;script&gt;" in html

    def test_lo_que_viene_del_catalogo_tambien_se_escapa(self):
        # No lo escribe el modelo, pero el documento ya no pasa por markdown-it
        # entero: si un nombre del CSV trae un `<`, ahora hay que escaparlo aqui.
        html = report_to_html(informe(careers=[carrera(career="Física <avanzada>"), carrera()]))

        assert "<avanzada>" not in html
        assert "&lt;avanzada&gt;" in html


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

    def test_pide_las_dos_tipografias_del_producto(self):
        css = read_stylesheet()

        assert "Inter" in css
        assert "Fraunces" in css

    def test_numera_las_paginas_con_el_total(self):
        # Un informe impreso se desordena; "2 de 5" lo dice y "2" no.
        css = read_stylesheet()

        assert "counter(page)" in css
        assert "counter(pages)" in css


class TestFraunces:
    """Fraunces no esta empaquetada en Debian y el contenedor no sale a
    internet, asi que viaja en el repositorio. Si el fichero desaparece, el PDF
    no falla: se cae a Georgia, que en la imagen tampoco existe, y los
    titulares salen con la serif por defecto sin una sola linea en el log."""

    def test_el_fichero_de_la_fuente_esta_donde_lo_pide_la_hoja(self):
        declarados = re.findall(r"url\('([^']+)'\)", read_stylesheet())

        assert declarados, "la hoja ya no declara ninguna fuente propia"
        for ruta in declarados:
            assert (HOJA_DE_ESTILOS.parent / ruta).exists(), ruta

    def test_no_se_declara_ningun_peso_que_no_viaje(self):
        # Pedir un peso sin fichero no falla: el motor sintetiza una negrita
        # falsa. Es de los fallos que solo se ven imprimiendo.
        pesos = set(re.findall(r"@font-face\s*{[^}]*?font-weight:\s*(\d+)", read_stylesheet()))
        ficheros = {p.name for p in (HOJA_DE_ESTILOS.parent / "fonts").glob("*.ttf")}

        assert pesos == {"600"}
        assert ficheros == {"Fraunces-SemiBold.ttf"}
