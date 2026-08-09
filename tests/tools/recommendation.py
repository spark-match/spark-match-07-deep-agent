"""Tests del motor multicriterio (fase 2 del ADR-019).

Lo que este motor añade respecto a lo que ya había: los filtros de `/filters`
—región, gestión, tipo de institución y presupuesto— pasan de no influir en
nada a excluir antes de puntuar, y la afinidad RIASEC se combina con la
economía en una sola cifra explicable.
"""

import itertools

import pytest

from src.tools.programs.loader import load_programs, normalize
from src.tools.recommendation.handler import (
    MAX_TOP_N,
    recommend_programs_handler,
)
from src.tools.recommendation.scoring import (
    NEUTRO,
    REFERENCE_RANGES,
    SCORING_VERSION,
    WEIGHTS,
    score_program,
)


def _programa(**overrides):
    """Un programa sintético; por defecto todo medido y en la banda baja."""
    base = {
        "career": "Carrera",
        "career_family": "Familia",
        "riasec_profile": "IRC",
        "institution": "Institución",
        "institution_type": "Universidad",
        "management_type": "Pública",
        "location": "Lima",
        "duration_years": 5.0,
        "monthly_income": REFERENCE_RANGES["monthly_income"][0],
        "annual_cost": REFERENCE_RANGES["annual_cost"][1],
        "admission_rate": REFERENCE_RANGES["admission_rate"][0],
        "duration_measured": True,
        "income_measured": True,
        "cost_measured": True,
        "admission_measured": True,
        "search_text": "carrera familia institución",
    }
    base.update(overrides)
    return base


class TestPesos:
    def test_suman_uno(self):
        # Si no sumaran 1, `match_score` no llegaría a 100 ni con todo perfecto
        # y el número dejaría de ser interpretable como porcentaje.
        assert sum(WEIGHTS.values()) == pytest.approx(1.0)

    def test_la_afinidad_pesa_mas_que_cualquier_senal_economica(self):
        # El producto es orientación VOCACIONAL: su premisa es que encajar pesa
        # más que cobrar. Si algún día se decide lo contrario, este test es el
        # sitio donde se ve que fue una decisión y no un descuido.
        economicas = {k: v for k, v in WEIGHTS.items() if k != "riasec_affinity"}
        assert WEIGHTS["riasec_affinity"] >= max(economicas.values())


class TestPuntuacion:
    def test_todo_perfecto_da_cien(self):
        perfecto = _programa(
            monthly_income=REFERENCE_RANGES["monthly_income"][1],
            annual_cost=REFERENCE_RANGES["annual_cost"][0],
            admission_rate=REFERENCE_RANGES["admission_rate"][1],
        )
        assert score_program(perfecto, 100.0)["match_score"] == 100.0

    def test_todo_en_el_suelo_da_cero(self):
        assert score_program(_programa(), 0.0)["match_score"] == 0.0

    def test_el_costo_va_invertido(self):
        """Barato tiene que puntuar ALTO en asequibilidad, no bajo."""
        barato = score_program(_programa(annual_cost=REFERENCE_RANGES["annual_cost"][0]), 0.0)
        caro = score_program(_programa(annual_cost=REFERENCE_RANGES["annual_cost"][1]), 0.0)

        assert barato["breakdown"]["affordability"] == 1.0
        assert caro["breakdown"]["affordability"] == 0.0

    def test_los_valores_fuera_de_rango_se_recortan(self):
        # El máximo real de `annual_cost` son S/ 32.530, muy por encima del p95
        # que se usa como referencia: sin recorte saldría un componente negativo.
        extremo = score_program(_programa(annual_cost=32530.0, monthly_income=99999.0), 50.0)

        assert extremo["breakdown"]["affordability"] == 0.0
        assert extremo["breakdown"]["income"] == 1.0

    def test_la_afinidad_tambien_se_acota(self):
        assert score_program(_programa(), 150.0)["breakdown"]["riasec_affinity"] == 1.0


class TestCifrasImputadas:
    """Una cifra que el pipeline imputó no dice nada de ESE programa.

    El pipeline rellena lo que falta con la mediana de la familia de carrera
    —el 73% de los ingresos y el 65% de las tasas de admisión— y cada fila trae
    su bandera `*_measured`. Dejar que esas medianas puntúen sería construir el
    ranking sobre datos de familia presentados como datos del programa.
    """

    def test_una_cifra_imputada_vale_el_neutro(self):
        imputado = score_program(_programa(monthly_income=99999.0, income_measured=False), 0.0)
        # 99999 normalizaría a 1.0 si contase; al no estar medido vale NEUTRO.
        assert imputado["breakdown"]["income"] == NEUTRO

    def test_da_igual_lo_alto_que_sea_el_valor_imputado(self):
        bajo = score_program(_programa(monthly_income=0.0, income_measured=False), 50.0)
        alto = score_program(_programa(monthly_income=99999.0, income_measured=False), 50.0)

        assert bajo["match_score"] == alto["match_score"]

    def test_lo_desconocido_queda_por_delante_de_lo_medido_y_malo(self):
        """Propiedad deliberada, y conviene tenerla escrita.

        Un programa cuya tasa de admisión no se midió puntúa 0.5 en ese
        componente; otro con una tasa real pésima puntúa cerca de 0. O sea que
        no saber sale mejor que saber que es malo. Es la postura honesta —sin
        información, el punto medio— pero hay que conocerla al leer un ranking,
        y por eso el desempate de `_orden` pone delante al que sí se midió
        cuando la puntuación empata.
        """
        desconocido = score_program(_programa(admission_rate=0.0, admission_measured=False), 50.0)
        medido_malo = score_program(
            _programa(admission_rate=REFERENCE_RANGES["admission_rate"][0]), 50.0
        )

        assert desconocido["match_score"] > medido_malo["match_score"]


class TestFiltrosDuros:
    """Excluyen, no restan puntos.

    Quien pide Arequipa no debe ver Piura con menos puntos: debe no verla.
    """

    def test_la_region_excluye(self):
        d = recommend_programs_handler(riasec_code="IRC", region="Arequipa", top_n=MAX_TOP_N)[
            "data"
        ]

        assert d["recommendations"]
        assert {r["location"] for r in d["recommendations"]} == {"Arequipa"}

    def test_ignora_acentos_y_mayusculas_en_la_region(self):
        con = recommend_programs_handler(riasec_code="SAE", region="Áncash")
        sin = recommend_programs_handler(riasec_code="SAE", region="ancash")

        assert con["data"]["total_candidates"] == sin["data"]["total_candidates"]
        assert con["data"]["total_candidates"] > 0

    def test_management_type_es_publica_o_privada(self):
        """El vocabulario del frontend casa por normalización, sin tabla.

        Ojo con el cruce, que va invertido: el `institutionType` del frontend
        (pública/privada) es el `management_type` del CSV, y su `academicType`
        (universidad/instituto) es el `institution_type` del CSV.
        """
        d = recommend_programs_handler(
            riasec_code="IRC", management_type="publica", top_n=MAX_TOP_N
        )["data"]

        assert {r["management_type"] for r in d["recommendations"]} == {"Pública"}

    def test_institution_type_es_universidad_o_instituto(self):
        d = recommend_programs_handler(
            riasec_code="IRC", institution_type="instituto", top_n=MAX_TOP_N
        )["data"]

        assert {r["institution_type"] for r in d["recommendations"]} == {"Instituto"}

    def test_ambas_y_ambos_no_filtran(self):
        sin_filtro = recommend_programs_handler(riasec_code="IRC")
        con_ambos = recommend_programs_handler(
            riasec_code="IRC", management_type="ambas", institution_type="ambos"
        )

        assert con_ambos["data"]["total_candidates"] == sin_filtro["data"]["total_candidates"]
        assert con_ambos["data"]["filters_applied"] == []

    def test_el_presupuesto_excluye_lo_que_no_cabe(self):
        d = recommend_programs_handler(riasec_code="IRC", max_annual_cost=300, top_n=MAX_TOP_N)[
            "data"
        ]

        assert d["recommendations"]
        assert all(r["annual_cost"] <= 300 for r in d["recommendations"])

    def test_los_filtros_se_combinan_con_y(self):
        d = recommend_programs_handler(
            riasec_code="IRC",
            region="Arequipa",
            management_type="publica",
            institution_type="universidad",
            max_annual_cost=5000,
            top_n=MAX_TOP_N,
        )["data"]

        assert sorted(d["filters_applied"]) == [
            "institution_type",
            "management_type",
            "max_annual_cost",
            "region",
        ]
        for r in d["recommendations"]:
            assert r["location"] == "Arequipa"
            assert r["management_type"] == "Pública"
            assert r["institution_type"] == "Universidad"
            assert r["annual_cost"] <= 5000

    def test_el_total_de_candidatos_cuadra_con_el_dataset(self):
        d = recommend_programs_handler(riasec_code="IRC", region="Tumbes", top_n=1)["data"]
        esperados = sum(1 for p in load_programs() if normalize(p["location"]) == "tumbes")

        assert d["total_candidates"] == esperados


class TestUnaCarreraPorResultado:
    def test_no_repite_carrera_en_el_ranking(self):
        """Sin esto, un top-3 sería la misma carrera en tres universidades.

        `programs.csv` es carrera × institución: "Ingeniería de Sistemas"
        aparece 53 veces. El estudiante vería una opción creyendo que ve tres.
        """
        d = recommend_programs_handler(riasec_code="IRC", region="Lima", top_n=MAX_TOP_N)["data"]
        carreras = [r["career"] for r in d["recommendations"]]

        assert len(carreras) == len(set(carreras))

    def test_de_cada_carrera_se_queda_el_mejor_programa(self):
        d = recommend_programs_handler(riasec_code="IRC", region="Lima", top_n=1)["data"]
        elegido = d["recommendations"][0]

        mismos = [
            p
            for p in load_programs()
            if p["career"] == elegido["career"] and normalize(p["location"]) == "lima"
        ]
        assert len(mismos) >= 1
        assert elegido["institution"] in {p["institution"] for p in mismos}


class TestOrdenYEstabilidad:
    def test_ordena_por_puntuacion_descendente(self):
        d = recommend_programs_handler(riasec_code="ISR", top_n=MAX_TOP_N)["data"]
        notas = [r["match_score"] for r in d["recommendations"]]

        assert notas == sorted(notas, reverse=True)

    def test_a_igualdad_va_primero_el_que_tiene_mas_cifras_medidas(self):
        d = recommend_programs_handler(riasec_code="SAE", top_n=MAX_TOP_N)["data"]
        recomendaciones = d["recommendations"]

        for anterior, siguiente in itertools.pairwise(recomendaciones):
            if anterior["match_score"] == siguiente["match_score"]:
                assert len(anterior["estimated"]) <= len(siguiente["estimated"])

    def test_dos_llamadas_iguales_dan_el_mismo_ranking(self):
        primera = recommend_programs_handler(riasec_code="ECS", region="Cusco", top_n=5)
        segunda = recommend_programs_handler(riasec_code="ECS", region="Cusco", top_n=5)

        assert primera["data"]["recommendations"] == segunda["data"]["recommendations"]

    def test_respeta_el_tope_de_top_n(self):
        d = recommend_programs_handler(riasec_code="IRC", top_n=500)["data"]

        assert len(d["recommendations"]) <= MAX_TOP_N


class TestImpactoDeCadaFiltro:
    """Se reporta SIEMPRE, no solo cuando el resultado sale vacío.

    Un filtro duro no devuelve una respuesta equivocada: borra opciones en
    silencio. Si el presupuesto que el agente dedujo de la conversación es
    S/ 2.000 en vez de S/ 20.000, la búsqueda sigue devolviendo resultados
    plausibles y nadie se entera. Esto es lo que convierte un error invisible
    en uno que el estudiante puede corregir.
    """

    def test_sin_filtros_va_vacio(self):
        d = recommend_programs_handler(riasec_code="IRC")["data"]

        assert d["candidates_without_each_filter"] == {}

    def test_trae_una_entrada_por_filtro_aplicado(self):
        d = recommend_programs_handler(riasec_code="IRC", region="Arequipa", max_annual_cost=1000)[
            "data"
        ]

        assert set(d["candidates_without_each_filter"]) == {"region", "max_annual_cost"}

    def test_soltar_la_region_devuelve_el_catalogo_entero(self):
        d = recommend_programs_handler(riasec_code="IRC", region="Tumbes")["data"]

        # Único filtro: quitarlo deja las 6.208 filas.
        assert d["candidates_without_each_filter"]["region"] == len(load_programs())

    def test_cada_cifra_es_mayor_o_igual_que_lo_que_quedo(self):
        d = recommend_programs_handler(
            riasec_code="IRC",
            region="Arequipa",
            management_type="publica",
            max_annual_cost=2000,
        )["data"]

        for sin_ese in d["candidates_without_each_filter"].values():
            assert sin_ese >= d["total_candidates"]

    def test_permite_ver_cuanto_recorta_el_presupuesto(self):
        con_tope = recommend_programs_handler(
            riasec_code="IRC", region="Arequipa", max_annual_cost=500
        )["data"]

        sin_el_tope = con_tope["candidates_without_each_filter"]["max_annual_cost"]
        assert sin_el_tope > con_tope["total_candidates"]


class TestSinResultados:
    def test_dice_que_filtro_soltar_y_cuanto_abre(self):
        """«No encontré nada» a secas deja al estudiante tocando controles a ciegas."""
        result = recommend_programs_handler(
            riasec_code="IRC",
            region="Madre de Dios",
            management_type="privada",
            max_annual_cost=50,
        )

        assert result["status"] == "error"
        mensaje = result["errors"][0]
        assert "Soltando" in mensaje
        assert "aparecerian" in mensaje

    def test_codigo_vacio_es_error(self):
        result = recommend_programs_handler(riasec_code="")

        assert result["status"] == "error"
        assert result["data"] is None


class TestProcedencia:
    def test_cada_resultado_lleva_sus_campos_estimados(self):
        d = recommend_programs_handler(riasec_code="IRC", top_n=5)["data"]

        for r in d["recommendations"]:
            assert isinstance(r["estimated"], list)
            assert set(r["estimated"]) <= {
                "duration_years",
                "monthly_income",
                "annual_cost",
                "admission_rate",
            }

    def test_declara_la_version_del_criterio_y_la_fuente(self):
        """El informe guarda esto: uno emitido hoy tiene que poder explicarse
        con las reglas de hoy aunque mañana cambien los pesos."""
        d = recommend_programs_handler(riasec_code="IRC")["data"]

        assert d["scoring_version"] == SCORING_VERSION
        assert "Ponte en Carrera" in d["source"]
        assert "2026-06-13" in d["source"]

    def test_el_desglose_permite_explicar_la_nota(self):
        d = recommend_programs_handler(riasec_code="IRC", top_n=1)["data"]
        desglose = d["recommendations"][0]["score_breakdown"]

        assert set(desglose) == set(WEIGHTS)
        assert all(0.0 <= v <= 1.0 for v in desglose.values())
