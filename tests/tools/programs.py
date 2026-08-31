"""Tests del catalogo real de programas (Ponte en Carrera).

Dos bloques. Los primeros trabajan sobre un CSV de juguete y fijan el
comportamiento del filtro. El ultimo va contra el **fichero que se despliega
de verdad**: sin eso, todo lo demas podria estar verde con un dataset vacio
dentro de la imagen, que es justo la forma en que este fallo volveria.
"""

from pathlib import Path

import pytest

from src.tools.programs import search_programs
from src.tools.programs.handler import MAX_LIMIT, search_programs_handler
from src.tools.programs.loader import (
    DATASET_PATH,
    SNAPSHOT_DATE,
    load_programs,
    normalize,
    reload_programs,
)

HEADER = (
    "source_id,career,career_family,riasec_profile,institution,institution_type,"
    "management_type,location,duration_years,monthly_income,annual_cost,admission_rate,"
    "duration_measured,income_measured,cost_measured,admission_measured\n"
)

# Tres filas pensadas para que cada una sea distinguible por un filtro.
ROWS = (
    "1,Ingeniería de Sistemas,Ingeniería,IRC,Universidad Nacional de Ingeniería,"
    "Universidad,Pública,Lima,5,4900,110,13,true,true,true,true\n"
    "2,Enfermería,Salud,SIA,Instituto Superior del Norte,"
    "Instituto,Privada,Áncash,3,1100,3200,60,true,false,true,false\n"
    "3,Contabilidad,Contabilidad y Finanzas,CES,Universidad Privada del Sur,"
    "Universidad,Privada,Arequipa,5,2100,8000,45,false,false,false,false\n"
)


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    dataset = tmp_path / "programs.csv"
    dataset.write_text(HEADER + ROWS, encoding="utf-8")
    reload_programs(dataset)
    yield dataset
    # La cache es global: dejarla con el CSV de juguete contaminaria al
    # siguiente test que espere el catalogo real.
    reload_programs()


@pytest.fixture
def empty_catalog(tmp_path: Path):
    reload_programs(tmp_path / "no-existe.csv")
    yield
    reload_programs()


class TestNormalize:
    def test_ignores_accents_and_case(self):
        # Un estudiante escribe "ancash" y el dataset guarda "Áncash".
        # Comparar en crudo convierte una tilde en un cero resultados.
        assert normalize("Áncash") == normalize("ancash")
        assert normalize("Ingeniería") == normalize("INGENIERIA")


class TestLoader:
    def test_reads_every_row(self, catalog):
        assert len(load_programs()) == 3

    def test_turns_the_measurement_flags_into_booleans(self, catalog):
        programs = {p["career"]: p for p in load_programs()}

        assert programs["Ingeniería de Sistemas"]["income_measured"] is True
        assert programs["Enfermería"]["income_measured"] is False

    def test_a_missing_file_is_not_an_empty_catalog_by_accident(self, empty_catalog):
        # Devuelve lista vacia, y el handler la convierte en error explicito.
        assert load_programs() == []
        assert search_programs_handler()["status"] == "error"


class TestFilters:
    def test_finds_a_career_written_without_accents(self, catalog):
        result = search_programs_handler(career="ingenieria")

        assert [p["career"] for p in result["data"]["programs"]] == ["Ingeniería de Sistemas"]

    def test_matches_the_institution_name_too(self, catalog):
        result = search_programs_handler(career="Nacional de Ingeniería")

        assert result["data"]["total_matches"] == 1

    def test_filters_by_department(self, catalog):
        result = search_programs_handler(location="ancash")

        assert [p["career"] for p in result["data"]["programs"]] == ["Enfermería"]

    def test_filters_by_institution_and_management_type(self, catalog):
        result = search_programs_handler(institution_type="Universidad", management_type="Pública")

        assert [p["institution"] for p in result["data"]["programs"]] == [
            "Universidad Nacional de Ingeniería"
        ]

    def test_filters_by_budget(self, catalog):
        result = search_programs_handler(max_annual_cost=3500)

        assert {p["career"] for p in result["data"]["programs"]} == {
            "Ingeniería de Sistemas",
            "Enfermería",
        }

    def test_riasec_keeps_only_careers_that_share_a_letter(self, catalog):
        # "IRC" comparte I,R,C con IRC y C con CES; con SIA comparte la I.
        result = search_programs_handler(riasec_profile="RC")

        assert {p["career"] for p in result["data"]["programs"]} == {
            "Ingeniería de Sistemas",
            "Contabilidad",
        }

    def test_combines_filters_with_and(self, catalog):
        result = search_programs_handler(career="a", location="Lima", management_type="Privada")

        assert result["data"]["total_matches"] == 0

    def test_no_filters_returns_the_catalog(self, catalog):
        assert search_programs_handler()["data"]["total_matches"] == 3


class TestHonestyAboutTheNumbers:
    """Lo que impide presentar una mediana como si fuera un dato medido."""

    def test_lists_exactly_the_fields_that_were_not_measured(self, catalog):
        result = search_programs_handler(career="Enfermería")

        assert result["data"]["programs"][0]["estimated"] == ["monthly_income", "admission_rate"]

    def test_a_fully_measured_program_estimates_nothing(self, catalog):
        result = search_programs_handler(career="Ingeniería de Sistemas")

        assert result["data"]["programs"][0]["estimated"] == []

    def test_puts_the_best_known_programs_first(self, catalog):
        # Lo primero que ve el estudiante debe ser lo que mejor sabemos, no
        # una mediana bien presentada. Contabilidad no tiene ni una cifra
        # medida, asi que va la ultima.
        result = search_programs_handler()

        assert [p["career"] for p in result["data"]["programs"]][-1] == "Contabilidad"

    def test_every_answer_carries_the_source_and_its_date(self, catalog):
        # "Datos oficiales" a secas omite que el snapshot tiene fecha y que
        # el portal lleva caido desde julio.
        source = search_programs_handler()["data"]["source"]

        assert "Ponte en Carrera" in source
        assert SNAPSHOT_DATE in source


class TestLimit:
    def test_caps_the_number_of_results(self, catalog):
        result = search_programs_handler(limit=2)

        assert len(result["data"]["programs"]) == 2
        # El total sigue siendo el real: el modelo tiene que poder decir
        # "hay 3, te muestro 2".
        assert result["data"]["total_matches"] == 3

    def test_refuses_to_flood_the_context(self, catalog):
        assert search_programs_handler(limit=10_000)["data"]["total_matches"] == 3
        assert len(search_programs_handler(limit=10_000)["data"]["programs"]) <= MAX_LIMIT

    def test_a_zero_or_negative_limit_still_returns_something(self, catalog):
        assert len(search_programs_handler(limit=0)["data"]["programs"]) == 1


class TestToolWrapper:
    def test_returns_the_data_block(self, catalog):
        result = search_programs.invoke({"career": "Enfermería"})

        assert result["total_matches"] == 1
        assert "source" in result

    def test_surfaces_the_error_instead_of_pretending_there_are_no_results(self, empty_catalog):
        # "Sin resultados" invita al modelo a rellenar el hueco de memoria.
        # Un error le dice que no tiene el dato.
        assert "error" in search_programs.invoke({"career": "x"})


class TestTheDatasetThatShips:
    """Contra el CSV real del repositorio, no contra uno de juguete."""

    def test_the_dataset_exists(self):
        assert DATASET_PATH.exists(), f"falta {DATASET_PATH} - regeneralo con scripts/"

    def test_carries_the_whole_portal(self):
        programs = reload_programs()

        assert len(programs) == 6208
        assert len({p["career"] for p in programs}) == 554
        assert len({p["institution"] for p in programs}) == 1071
        assert len({p["location"] for p in programs}) == 25

    def test_every_program_has_a_riasec_code(self):
        programs = reload_programs()

        assert all(p["riasec_profile"] for p in programs)

    def test_finds_a_real_program_in_a_real_department(self):
        reload_programs()
        result = search_programs_handler(career="enfermeria", location="Loreto", limit=3)

        assert result["data"]["total_matches"] > 0
        assert all(p["location"] == "Loreto" for p in result["data"]["programs"])

    def test_the_numbers_are_not_all_estimated(self):
        # Si el generador perdiera las banderas, todo quedaria marcado como
        # estimado (o como medido) y la distincion dejaria de valer nada.
        reload_programs()
        programs = load_programs()

        measured = sum(1 for p in programs if p["income_measured"])
        assert 0 < measured < len(programs)
