"""Tests for the agent tools."""

from src.tools.assessment import evaluate_riasec_profile
from src.tools.catalog import search_careers
from src.tools.matching import calculate_affinity


class TestAssessment:
    """Tests for the RIASEC assessment tool."""

    def test_evaluate_riasec_profile_returns_code(self):
        result = evaluate_riasec_profile.invoke(
            {
                "realistic": 3,
                "investigative": 9,
                "artistic": 7,
                "social": 5,
                "enterprising": 4,
                "conventional": 2,
            }
        )

        assert result["riasec_code"] == "IAS"
        assert len(result["dominant_types"]) == 3
        assert result["dominant_types"][0]["code"] == "I"
        assert result["dominant_types"][0]["score"] == 9

    def test_evaluate_riasec_profile_tie_breaking(self):
        """When scores tie, alphabetical RIASEC order is not guaranteed —
        we just need a valid 3-letter code."""
        result = evaluate_riasec_profile.invoke(
            {
                "realistic": 5,
                "investigative": 5,
                "artistic": 5,
                "social": 5,
                "enterprising": 5,
                "conventional": 5,
            }
        )

        assert len(result["riasec_code"]) == 3
        assert "interpretation" in result


class TestCatalog:
    """Tests for the career catalog search tool."""

    def test_search_by_keyword(self):
        results = search_careers.invoke({"query": "programación"})
        assert len(results) > 0
        assert any(c["id"] == "cs" for c in results)

    def test_search_by_field(self):
        results = search_careers.invoke({"query": "", "field": "Tecnología"})
        assert len(results) > 0
        assert all("Tecnología" in c["field"] for c in results)

    def test_search_no_results_returns_suggestions(self):
        results = search_careers.invoke({"query": "xyznonexistent"})
        assert len(results) > 0  # Returns suggestions


class TestMatching:
    """Tests for the affinity calculation tool."""

    def test_calculate_affinity_returns_top_n(self):
        results = calculate_affinity.invoke({"riasec_code": "IAS", "top_n": 3})
        assert len(results) == 3
        assert all("affinity_score" in r for r in results)

    def test_calculate_affinity_sorted_descending(self):
        results = calculate_affinity.invoke({"riasec_code": "IRC"})
        scores = [r["affinity_score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_high_affinity_for_matching_profile(self):
        """A profile matching CS (IRC) should score CS highly."""
        results = calculate_affinity.invoke({"riasec_code": "IRC"})
        cs_result = next(r for r in results if r["career_id"] == "cs")
        assert cs_result["affinity_score"] > 50
