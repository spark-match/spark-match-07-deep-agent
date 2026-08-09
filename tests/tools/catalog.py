"""Tests del handler de busqueda por carrera.

Hasta el 2026-08-09 este handler leia `data/careers/*.md`: veinte fichas
curadas a mano, con su propio RIASEC y su propio `field`, en paralelo al
catalogo real de 6.208 filas que ya estaba en el repositorio y que solo usaba
`search_programs`. Se retiraron (ADR-019 de spark-match-03-backend) y ahora las
dos herramientas son dos vistas del mismo `data/programs/programs.csv`: esta a
nivel de carrera, la otra a nivel de programa.
"""

import src.tools.catalog as catalog_pkg
from src.tools.catalog.handler import MAX_LIMIT, search_careers_handler
from src.tools.programs.loader import load_careers


class TestSaleDelCatalogoReal:
    """La vista por carrera es una proyeccion del CSV, no otro fichero."""

    def test_son_las_554_carreras_unicas_del_portal(self):
        assert len(load_careers()) == 554

    def test_ninguna_carrera_aparece_dos_veces(self):
        nombres = [entry["career"] for entry in load_careers()]
        assert len(nombres) == len(set(nombres))

    def test_todas_traen_codigo_riasec(self):
        # Precondicion de `calculate_affinity`: sin codigo no hay nada que
        # puntuar. El pipeline dice que etiqueta las 554; esto lo comprueba.
        assert all(len(entry["riasec_profile"]) == 3 for entry in load_careers())

    def test_el_paquete_ya_no_expone_el_catalogo_curado(self):
        """Guarda contra reintroducir una segunda fuente de carreras.

        Que `data/careers/` desaparezca no basta: mientras `load_career_catalog`
        siga exportado, volver a poner un fichero paralelo es una linea de
        codigo. El dia que alguien lo intente, esto falla primero.
        """
        assert not hasattr(catalog_pkg, "load_career_catalog")
        assert not hasattr(catalog_pkg, "Career")


class TestCareerCatalogSize:
    """DoD de la tarea 8.7 del Sprint 8: >= 20 carreras en el catalogo.

    El criterio se escribio contra `data/careers/`, que ya no existe. No se
    baja ni se borra: se comprueba contra el catalogo que lo sustituye, que
    tiene 554. Sigue siendo una guarda util --un CSV que no se copie a la
    imagen del contenedor deja el catalogo en cero-- y el umbral se deja en 20
    a proposito, que es el numero que fijo la DoD.
    """

    def test_catalog_has_at_least_twenty_careers(self):
        assert len(load_careers()) >= 20

    def test_all_career_names_are_unique(self):
        nombres = [entry["career"] for entry in load_careers()]
        assert len(nombres) == len(set(nombres))


class TestBusqueda:
    def test_encuentra_por_nombre_de_carrera(self):
        result = search_careers_handler(query="psicolog")

        assert result["status"] == "success"
        assert result["data"]["fallback_used"] is False
        assert any("Psicolog" in c["career"] for c in result["data"]["careers"])

    def test_ignora_acentos_y_mayusculas(self):
        con = search_careers_handler(query="Ingeniería")
        sin = search_careers_handler(query="ingenieria")

        assert con["data"]["total_matches"] == sin["data"]["total_matches"]
        assert con["data"]["total_matches"] > 0

    def test_field_filtra_por_familia(self):
        result = search_careers_handler(query="", field="Teatro")

        assert result["status"] == "success"
        assert {c["career_family"] for c in result["data"]["careers"]} == {"Teatro"}
        assert result["data"]["fallback_used"] is False

    def test_devuelve_la_fuente_con_su_fecha(self):
        result = search_careers_handler(query="derecho")

        assert "Ponte en Carrera" in result["data"]["source"]
        assert "2026-06-13" in result["data"]["source"]

    def test_no_filtra_el_indice_interno_al_modelo(self):
        result = search_careers_handler(query="medicina")

        assert all("search_text" not in c for c in result["data"]["careers"])


class TestFallbacks:
    def test_sin_coincidencias_sugiere_y_lo_avisa(self):
        result = search_careers_handler(query="xyznoexiste")

        assert result["status"] == "success"
        assert result["data"]["fallback_used"] is True
        assert len(result["data"]["careers"]) > 0

    def test_cae_a_la_familia_cuando_el_texto_no_casa(self):
        result = search_careers_handler(query="xyznoexiste", field="Teatro")

        assert result["data"]["fallback_used"] is True
        assert {c["career_family"] for c in result["data"]["careers"]} == {"Teatro"}


class TestTope:
    """Con 20 carreras no hacia falta tope; con 554, si.

    Una consulta vaga como "ingenieria" casa con mas de cien, y devolverlas
    todas mete ~100 registros en el contexto del modelo, desplazando la
    conversacion del estudiante.
    """

    def test_respeta_el_limit_pedido(self):
        result = search_careers_handler(query="ingenieria", limit=3)

        assert len(result["data"]["careers"]) == 3
        # total_matches cuenta lo encontrado, no lo devuelto.
        assert result["data"]["total_matches"] > 3

    def test_no_deja_pedir_mas_del_tope_duro(self):
        result = search_careers_handler(query="ingenieria", limit=500)

        assert len(result["data"]["careers"]) <= MAX_LIMIT

    def test_un_limit_absurdo_no_revienta(self):
        result = search_careers_handler(query="ingenieria", limit=0)

        assert result["status"] == "success"
        assert len(result["data"]["careers"]) == 1


class TestOrden:
    def test_primero_las_que_se_ofertan_en_mas_sitios(self):
        result = search_careers_handler(query="ingenieria", limit=MAX_LIMIT)

        cuentas = [c["program_count"] for c in result["data"]["careers"]]
        assert cuentas == sorted(cuentas, reverse=True)

    def test_dos_llamadas_iguales_dan_el_mismo_orden(self):
        primera = search_careers_handler(query="ingenieria")
        segunda = search_careers_handler(query="ingenieria")

        assert [c["career"] for c in primera["data"]["careers"]] == [
            c["career"] for c in segunda["data"]["careers"]
        ]
