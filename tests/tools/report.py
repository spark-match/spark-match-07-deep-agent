"""Tests del ensamblado del informe (fase 4 del ADR-019).

Lo que se protege aqui no es un formato, es una frontera: **el modelo escribe
prosa y el motor pone las cifras**, y esa separacion tiene que seguir siendo
imposible de cruzar aunque alguien anada un parametro «por comodidad» dentro de
seis meses. De ahi que haya un test sobre la FIRMA de la herramienta y no solo
sobre su salida.
"""

import inspect

from src.models.report import OrientationReport
from src.tools.recommendation.handler import MAX_TOP_N, recommend_programs_handler
from src.tools.report.handler import MIN_CAREERS, build_orientation_report_handler

CODIGO = "IRC"
RESUMEN = "Le interesa entender como funcionan las cosas y explicarselo a otros."


def _ranking(**kwargs):
    """El ranking del motor tal cual, para comparar contra el informe."""
    resultado = recommend_programs_handler(riasec_code=CODIGO, top_n=MAX_TOP_N, **kwargs)
    assert resultado["status"] == "success", resultado["errors"]
    return resultado["data"]["recommendations"]


def _insights(recomendaciones, cuantas=3):
    return [
        {"career": r["career"], "insight": f"Encaja con su perfil por {r['career_family']}."}
        for r in recomendaciones[:cuantas]
    ]


def _informe(insights=None, resumen=RESUMEN, **kwargs):
    return build_orientation_report_handler(
        riasec_code=CODIGO,
        profile_summary=resumen,
        insights=insights if insights is not None else _insights(_ranking()),
        **kwargs,
    )


class TestLaFronteraEntreProsaYCifras:
    """La razon de ser de esta herramienta."""

    def test_la_herramienta_no_admite_una_sola_cifra_del_informe(self):
        """El guardrail estructural: no hay parametro por donde meterlas.

        Un prompt que pide «no inventes numeros» se puede desobedecer. Una
        firma que no tiene donde recibirlos, no. Si alguien anade aqui un
        `monthly_income` o un `match_score` para ahorrarse la llamada al
        motor, este test se rompe y hay que leer el porque en
        `src/tools/report/handler.py`.
        """
        parametros = set(inspect.signature(build_orientation_report_handler).parameters)

        prohibidos = {
            "duration_years",
            "monthly_income",
            "annual_cost",
            "admission_rate",
            "match_score",
            "score_breakdown",
            "estimated",
            "careers",
            "recommendations",
        }
        assert not (parametros & prohibidos), (
            f"la herramienta acepta cifras del informe por parametro: {parametros & prohibidos}"
        )

    def test_las_cifras_del_informe_son_exactamente_las_del_motor(self):
        recomendaciones = _ranking()
        resultado = _informe(_insights(recomendaciones))
        assert resultado["status"] == "success", resultado["errors"]

        del_motor = {r["career"]: r for r in recomendaciones}
        for carrera in resultado["data"]["careers"]:
            origen = del_motor[carrera["career"]]
            for campo in (
                "duration_years",
                "monthly_income",
                "annual_cost",
                "admission_rate",
                "match_score",
                "score_breakdown",
                "estimated",
                "institution",
                "location",
            ):
                assert carrera[campo] == origen[campo], f"{carrera['career']}: {campo} no coincide"

    def test_la_prosa_del_modelo_llega_intacta(self):
        recomendaciones = _ranking()
        insights = _insights(recomendaciones)
        resultado = _informe(insights)

        escritas = {i["career"]: i["insight"] for i in insights}
        for carrera in resultado["data"]["careers"]:
            assert carrera["insight"] == escritas[carrera["career"]]
        assert resultado["data"]["profile_summary"] == RESUMEN


class TestDesajustes:
    """Un informe a medias es peor que ninguno: nadie ve el hueco."""

    def test_una_carrera_que_el_motor_no_recomendo_es_un_error(self):
        insights = _insights(_ranking(), cuantas=2)
        insights.append({"career": "Alquimia Aplicada", "insight": "Inventada."})

        resultado = _informe(insights)

        assert resultado["status"] == "error"
        assert "Alquimia Aplicada" in resultado["errors"][0]

    def test_el_error_dice_que_carreras_si_hay(self):
        # Sin la lista, el modelo solo sabe que fallo, no como corregirlo.
        recomendaciones = _ranking()
        insights = _insights(recomendaciones, cuantas=2)
        insights.append({"career": "No Existe", "insight": "x"})

        resultado = _informe(insights)

        assert recomendaciones[0]["career"] in resultado["errors"][0]

    def test_una_carrera_sin_explicar_no_entra(self):
        # No es un error: el modelo eligio 3 de 10 y las otras 7 simplemente
        # no salen. Lo que no puede pasar es que salgan con el insight vacio.
        recomendaciones = _ranking()
        resultado = _informe(_insights(recomendaciones, cuantas=3))

        assert len(resultado["data"]["careers"]) == 3
        assert all(c["insight"] for c in resultado["data"]["careers"])

    def test_un_insight_vacio_es_un_error(self):
        insights = _insights(_ranking(), cuantas=2)
        insights[0]["insight"] = "   "

        resultado = _informe(insights)

        assert resultado["status"] == "error"
        assert insights[0]["career"] in resultado["errors"][0]

    def test_la_misma_carrera_dos_veces_es_un_error(self):
        insights = _insights(_ranking(), cuantas=2)
        insights.append(dict(insights[0]))

        resultado = _informe(insights)

        assert resultado["status"] == "error"
        assert "dos veces" in resultado["errors"][0]


class TestEntradasQueNoDanInforme:
    def test_sin_resumen_no_hay_informe(self):
        resultado = _informe(resumen="   ")

        assert resultado["status"] == "error"
        assert "profile_summary" in resultado["errors"][0]

    def test_sin_carreras_no_hay_informe(self):
        resultado = _informe([])

        assert resultado["status"] == "error"

    def test_una_sola_carrera_no_es_una_orientacion(self):
        # Un informe con una opcion no deja comparar, que es para lo que se
        # pide un informe.
        resultado = _informe(_insights(_ranking(), cuantas=1))

        assert resultado["status"] == "error"
        assert str(MIN_CAREERS) in resultado["errors"][0]

    def test_mas_del_maximo_no_se_acepta(self):
        insights = [{"career": f"Carrera {i}", "insight": "x"} for i in range(MAX_TOP_N + 1)]

        resultado = _informe(insights)

        assert resultado["status"] == "error"
        assert str(MAX_TOP_N) in resultado["errors"][0]

    def test_un_codigo_riasec_vacio_propaga_el_error_del_motor(self):
        resultado = build_orientation_report_handler(
            riasec_code="",
            profile_summary=RESUMEN,
            insights=[{"career": "x", "insight": "y"}, {"career": "z", "insight": "w"}],
        )

        assert resultado["status"] == "error"


class TestNombresDeCarrera:
    def test_los_acentos_y_las_mayusculas_no_rompen_el_emparejado(self):
        """El modelo escribe el nombre a mano; fallar por un acento seria absurdo."""
        recomendaciones = _ranking()
        insights = _insights(recomendaciones, cuantas=2)
        insights[0]["career"] = insights[0]["career"].upper()

        resultado = _informe(insights)

        assert resultado["status"] == "success", resultado["errors"]
        assert len(resultado["data"]["careers"]) == 2


class TestOrden:
    def test_manda_el_orden_del_motor_y_no_el_del_modelo(self):
        """El ranking es trabajo del motor; el modelo solo elige quien entra."""
        recomendaciones = _ranking()
        insights = _insights(recomendaciones, cuantas=3)
        insights.reverse()

        resultado = _informe(insights)

        salida = [c["career"] for c in resultado["data"]["careers"]]
        esperado = [r["career"] for r in recomendaciones[:3]]
        assert salida == esperado

    def test_elegir_salteado_respeta_las_elegidas(self):
        # El subagente puede mirar un top-10 y quedarse con la 1, la 4 y la 8:
        # tienen que salir esas tres, no las tres primeras.
        recomendaciones = _ranking()
        if len(recomendaciones) < 9:
            return  # el catalogo filtrado no da para este caso

        elegidas = [recomendaciones[0], recomendaciones[3], recomendaciones[7]]
        insights = [{"career": r["career"], "insight": "porque si."} for r in elegidas]

        resultado = _informe(insights)

        salida = [c["career"] for c in resultado["data"]["careers"]]
        assert salida == [r["career"] for r in elegidas]


class TestProcedencia:
    def test_el_informe_guarda_con_que_reglas_se_emitio(self):
        """Un informe de hoy tiene que poder explicarse con las reglas de hoy."""
        datos = _informe()["data"]

        assert datos["scoring_version"]
        assert datos["source"]

    def test_guarda_cuanto_recortaba_cada_filtro(self):
        # Quien lo lea meses despues tiene que ver que el resultado dependia de
        # unos filtros, no del catalogo entero.
        datos = _informe(
            _insights(_ranking(region="Lima"), cuantas=2),
            region="Lima",
        )["data"]

        assert "region" in datos["filters_applied"]
        assert datos["candidates_without_each_filter"]["region"] > 0

    def test_lo_estimado_sigue_marcado_en_el_informe(self):
        # Sin esta lista, un ingreso imputado es indistinguible de uno
        # publicado, y el informe se imprime.
        datos = _informe()["data"]

        assert all(isinstance(c["estimated"], list) for c in datos["careers"])


class TestEsquema:
    def test_la_salida_valida_contra_el_modelo(self):
        datos = _informe()["data"]

        informe = OrientationReport(**datos)

        assert informe.riasec_code == CODIGO
        assert len(informe.careers) >= MIN_CAREERS
