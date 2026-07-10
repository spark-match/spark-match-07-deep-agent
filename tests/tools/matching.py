"""Tests for the matching handler."""

from src.tools.matching.handler import calculate_affinity_handler


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
        """A profile matching CS (IRC) should score CS highly."""
        result = calculate_affinity_handler(riasec_code="IRC")
        cs_result = next(m for m in result["data"]["matches"] if m["career_id"] == "cs")
        assert cs_result["affinity_score"] > 50

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
