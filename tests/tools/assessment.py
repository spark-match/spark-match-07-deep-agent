"""Tests for the RIASEC assessment tool."""

from src.tools.assessment import evaluate_riasec_profile


class AssessmentTool:
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
        we just need a valid 3-letter code.
        """
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
