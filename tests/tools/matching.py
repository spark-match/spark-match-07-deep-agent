"""Tests for the matching handler."""

import itertools

from src.tools.matching.handler import (
    MAX_TOP_N,
    _raw_riasec_score,
    _riasec_similarity,
    calculate_affinity_handler,
)

_RIASEC_LETTERS = "RIASEC"


class TestMatchingHandler:
    """Tests for the RIASEC affinity calculation handler."""

    def test_returns_top_n(self):
        result = calculate_affinity_handler(riasec_code="IAS", top_n=3)

        assert result["status"] == "success"
        matches = result["data"]["matches"]
        assert len(matches) == 3
        assert all("affinity_score" in m for m in matches)
        assert result["data"]["top_n"] == 3

    def test_sorted_descending(self):
        result = calculate_affinity_handler(riasec_code="IRC")
        scores = [m["affinity_score"] for m in result["data"]["matches"]]
        assert scores == sorted(scores, reverse=True)

    def test_high_affinity_for_matching_profile(self):
        """Una carrera con el codigo exacto del perfil puntua al maximo.

        Antes esto se comprobaba contra `career_id == "cs"`, una de las veinte
        fichas de `data/careers`. Ya no hay ids: el catalogo son las 554
        carreras del portal, identificadas por nombre. Se comprueba la
        propiedad -- codigo identico, afinidad 100 -- en vez de una fila
        concreta, que es lo que se queria decir desde el principio.
        """
        result = calculate_affinity_handler(riasec_code="IRC", top_n=1)
        mejor = result["data"]["matches"][0]

        assert mejor["riasec_profile"] == "IRC"
        assert mejor["affinity_score"] == 100.0

    def test_uppercase_normalization(self):
        result = calculate_affinity_handler(riasec_code="ias")
        assert result["data"]["riasec_code"] == "IAS"

    def test_invalid_code_returns_error(self):
        result = calculate_affinity_handler(riasec_code="")
        assert result["status"] == "error"
        assert result["data"] is None

    def test_default_top_n(self):
        result = calculate_affinity_handler(riasec_code="IRC")
        assert result["data"]["top_n"] == 5
        assert len(result["data"]["matches"]) == 5


class TestPuntuaElCatalogoReal:
    """El 2026-08-09 el handler paso de las 20 fichas de `data/careers/*.md`
    a las 554 carreras de `data/programs/programs.csv`.

    El cambio de tamano no es cosmetico: trae dos problemas que con 20
    entradas no existian, y estas pruebas los fijan.
    """

    def test_puntua_las_554_carreras(self):
        result = calculate_affinity_handler(riasec_code="IAS")
        assert result["data"]["total_scored"] == 554

    def test_ninguna_carrera_se_repite_en_el_top(self):
        """El problema numero uno de puntuar el CSV en crudo.

        `programs.csv` es carrera x institucion: "Ingenieria de Sistemas"
        aparece 53 veces. Puntuando las 6.208 filas, un top-5 saldria con la
        misma carrera cinco veces en cinco universidades distintas y el
        estudiante veria una sola opcion creyendo que ve cinco. Por eso se
        puntua la vista colapsada y no las filas.
        """
        result = calculate_affinity_handler(riasec_code="ISR", top_n=25)
        nombres = [m["career"] for m in result["data"]["matches"]]

        assert len(nombres) == len(set(nombres))

    def test_los_empates_se_ordenan_por_oferta_y_no_al_azar(self):
        """El problema numero dos: 554 carreras en solo 52 codigos RIASEC.

        Cualquier perfil empata a 100% con una decena de carreras. Sin criterio
        de desempate el top-5 seria un sorteo entre ellas; con el, delante van
        las que se pueden estudiar en mas sitios.
        """
        result = calculate_affinity_handler(riasec_code="SAE", top_n=25)
        matches = result["data"]["matches"]

        empatadas = [m for m in matches if m["affinity_score"] == matches[0]["affinity_score"]]
        assert len(empatadas) > 1, "sin empates este test no comprueba nada"

        cuentas = [m["program_count"] for m in empatadas]
        assert cuentas == sorted(cuentas, reverse=True)

    def test_dos_llamadas_iguales_dan_el_mismo_ranking(self):
        primera = calculate_affinity_handler(riasec_code="SAE", top_n=10)
        segunda = calculate_affinity_handler(riasec_code="SAE", top_n=10)

        assert [m["career"] for m in primera["data"]["matches"]] == [
            m["career"] for m in segunda["data"]["matches"]
        ]

    def test_top_n_tiene_tope_duro(self):
        # Sin tope, `top_n=554` mete el catalogo entero en el contexto.
        result = calculate_affinity_handler(riasec_code="IRC", top_n=1000)

        assert len(result["data"]["matches"]) <= MAX_TOP_N
        assert result["data"]["top_n"] == MAX_TOP_N

    def test_cada_resultado_trae_su_familia_y_su_oferta(self):
        result = calculate_affinity_handler(riasec_code="ECS", top_n=3)

        for match in result["data"]["matches"]:
            assert match["career_family"]
            assert match["program_count"] >= 1
            assert "career_id" not in match, "los ids eran del catalogo curado"

    def test_declara_la_fuente_con_su_fecha(self):
        result = calculate_affinity_handler(riasec_code="ECS")

        assert "Ponte en Carrera" in result["data"]["source"]
        assert "2026-06-13" in result["data"]["source"]


class TestRiasecSimilarityBounds:
    """B4 regression: affinity must never exceed 100%, even for malformed
    (repeated-letter) codes that should not occur from a real
    evaluate_riasec_profile call but were never validated against here.

    The previous fix (PR #19) patched a dead module
    (src/tools/matching.py, removed in the Sprint 4 refactor) and never
    reached this handler.
    """

    def test_property_score_bounded_for_all_216_combinations(self):
        """Exhaustive: every ordered 3-letter code (with repeats) against
        every other, both directions, must stay within [0, 100]."""
        codes = ["".join(c) for c in itertools.product(_RIASEC_LETTERS, repeat=3)]
        assert len(codes) == 216

        for profile in codes:
            for career in codes:
                score = _riasec_similarity(profile, career)
                assert 0 <= score <= 100, f"{profile} vs {career} -> {score}"

    def test_degenerate_repeated_letter_profile_used_to_exceed_100(self):
        """Concrete regression case: with the old fixed denominator (60),
        a profile of a single repeated letter against itself raw-scored
        120, i.e. 200% — this is the exact scenario B4 describes."""
        raw = _raw_riasec_score("III", "III")
        assert raw == 120.0  # confirms the degenerate case really is > the
        # old fixed denominator of 60; the bug was real, not hypothetical.
        assert _riasec_similarity("III", "III") == 100.0

    def test_self_match_is_always_100_percent(self):
        """A profile matched against itself is always the ceiling: 100%,
        regardless of whether the code has distinct or repeated letters."""
        codes = ["".join(c) for c in itertools.product(_RIASEC_LETTERS, repeat=3)]
        for code in codes:
            assert _riasec_similarity(code, code) == 100.0

    def test_distinct_letter_codes_are_unaffected_by_the_fix(self):
        """For real (3-distinct-letter) RIASEC codes, self-match raw score
        is exactly 60 — identical to the pre-fix fixed denominator — so
        legitimate scores are byte-for-byte unchanged by this fix."""
        assert _raw_riasec_score("IAS", "IAS") == 60.0
        assert _raw_riasec_score("RIC", "RIC") == 60.0
