"""Tests for the @tool wrappers (thin layer around the handlers).

These tests verify the tool wrappers correctly delegate to the handlers
and surface the structured response in the format the LLM expects.
"""

from src.tools.assessment.tool import evaluate_riasec_profile
from src.tools.catalog.tool import search_careers
from src.tools.matching.tool import calculate_affinity
from src.tools.web_search.tool import web_search


class TestToolWrappers:
    """Tests verifying the @tool wrappers are correctly wired."""

    def test_assessment_tool_unwraps_data(self):
        """The @tool wrapper exposes handler's data dict directly to the LLM."""
        result = evaluate_riasec_profile.invoke(
            {
                "realistic": 2,
                "investigative": 8,
                "artistic": 7,
                "social": 4,
                "enterprising": 3,
                "conventional": 5,
            }
        )

        # Tool wrapper unwraps: handler's "data" becomes the tool's return
        assert "riasec_code" in result
        assert "dominant_types" in result

    def test_catalog_tool_returns_careers_list(self):
        result = search_careers.invoke({"query": "psicolog"})
        assert isinstance(result, list)
        if result:
            assert "id" in result[0]
            assert "name" in result[0]

    def test_matching_tool_returns_matches_list(self):
        result = calculate_affinity.invoke({"riasec_code": "IRC"})
        assert isinstance(result, list)
        if result:
            assert "career_id" in result[0]
            assert "affinity_score" in result[0]

    def test_web_search_tool_is_callable(self):
        # Web search requires API keys; we just verify the tool exists.
        assert callable(web_search.invoke)
        assert web_search.name == "web_search"
