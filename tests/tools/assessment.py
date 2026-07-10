"""Tests for the assessment handler.

These tests exercise the pure handler directly (no @tool decorator, no
LLM dependency). See tests/tools/tool_wrappers.py for the @tool wrapper.
"""

from src.tools.assessment.handler import evaluate_riasec_profile_handler


class TestAssessmentHandler:
    """Tests for the RIASEC assessment handler."""

    def test_returns_ias_for_investigative_dominant(self):
        result = evaluate_riasec_profile_handler(
            realistic=3,
            investigative=9,
            artistic=7,
            social=5,
            enterprising=4,
            conventional=2,
        )

        assert result["status"] == "success"
        profile = result["data"]
        assert profile["riasec_code"] == "IAS"
        assert len(profile["dominant_types"]) == 3
        assert profile["dominant_types"][0]["code"] == "I"
        assert profile["dominant_types"][0]["score"] == 9
        assert result["errors"] is None

    def test_tie_breaking_returns_valid_code(self):
        """When all scores tie, we still get a valid 3-letter RIASEC code."""
        result = evaluate_riasec_profile_handler(
            realistic=5,
            investigative=5,
            artistic=5,
            social=5,
            enterprising=5,
            conventional=5,
        )

        assert result["status"] == "success"
        assert len(result["data"]["riasec_code"]) == 3
        assert "interpretation" in result["data"]

    def test_invalid_scores_returns_error_status(self):
        """Out-of-range scores produce a structured error, not a crash."""
        result = evaluate_riasec_profile_handler(
            realistic=99,
            investigative=9,
            artistic=7,
            social=5,
            enterprising=4,
            conventional=2,
        )

        assert result["status"] == "error"
        assert result["data"] is None
        assert result["errors"] is not None

    def test_interpretation_mentions_dominant_dimensions(self):
        result = evaluate_riasec_profile_handler(
            realistic=2,
            investigative=8,
            artistic=8,
            social=4,
            enterprising=3,
            conventional=5,
        )

        interpretation = result["data"]["interpretation"]
        assert "Investigativo" in interpretation
        assert "Art" in interpretation  # use partial match to avoid encoding issues
