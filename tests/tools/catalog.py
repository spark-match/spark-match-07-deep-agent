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
