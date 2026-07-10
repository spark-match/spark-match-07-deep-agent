"""Tests for the affinity calculation tool."""

from src.tools.matching import calculate_affinity


class MatchingTool:
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
