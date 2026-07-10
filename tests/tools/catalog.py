"""Tests for the career catalog search tool."""

from src.tools.catalog import search_careers


class CatalogTool:
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
