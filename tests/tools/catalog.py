"""Tests for the catalog handler."""

from src.tools.catalog.handler import search_careers_handler


class TestCatalogHandler:
    """Tests for the career catalog search handler."""

    def test_search_by_keyword(self):
        result = search_careers_handler(query="computaci")

        assert result["status"] == "success"
        careers = result["data"]["careers"]
        assert len(careers) > 0
        assert any(c["id"] == "cs" for c in careers)
        assert result["data"]["fallback_used"] is False

    def test_search_by_field(self):
        result = search_careers_handler(query="", field="Tecnolog")

        assert result["status"] == "success"
        careers = result["data"]["careers"]
        assert len(careers) > 0
        assert all("Tecnolog" in c["field"] for c in careers)

    def test_search_no_results_returns_suggestions(self):
        result = search_careers_handler(query="xyznonexistent")

        assert result["status"] == "success"
        assert result["data"]["fallback_used"] is True
        assert len(result["data"]["careers"]) > 0

    def test_total_field_present(self):
        result = search_careers_handler(query="psicolog")
        assert "total" in result["data"]
        assert result["data"]["total"] == len(result["data"]["careers"])

    def test_empty_query_with_field(self):
        """Empty query with a field returns all careers in that field."""
        result = search_careers_handler(query="", field="Salud")
        assert result["status"] == "success"
        assert len(result["data"]["careers"]) > 0
        # Not a fallback - it's a real filter match
        assert result["data"]["fallback_used"] is False


class TestCareerCatalogSize:
    """Sprint 8, task 8.7 DoD: >=20 careers in data/careers/.

    Regression guard: catches an accidental deletion or a malformed new
    entry (frontmatter parse failure silently dropping a file) that would
    take the real catalog back under the DoD threshold, without needing
    to hardcode an exact count that would break every time content is
    added (a content-only, no-code-review-needed change per
    data/careers/README.md).
    """

    def test_catalog_has_at_least_twenty_careers(self):
        from src.tools.catalog.loader import load_career_catalog

        careers = load_career_catalog()
        assert len(careers) >= 20

    def test_all_career_ids_are_unique(self):
        from src.tools.catalog.loader import load_career_catalog

        careers = load_career_catalog()
        ids = [c["id"] for c in careers]
        assert len(ids) == len(set(ids))
