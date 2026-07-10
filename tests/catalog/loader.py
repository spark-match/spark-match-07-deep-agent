"""Tests for the career catalog loader.

These tests operate against the real ``data/careers/`` directory so any
schema drift in the catalog itself surfaces as a test failure.
"""

from src.tools.catalog import (
    CATALOG_DIR,
    load_career_catalog,
    reload_career_catalog,
)
from src.tools.catalog.loader import _parse_career_file


class TestCareerCatalogLoader:
    """Loader behavior on the real catalog."""

    def test_catalog_dir_exists(self):
        assert CATALOG_DIR.exists(), f"catalog dir missing: {CATALOG_DIR}"

    def test_catalog_loads_at_least_10_careers(self):
        careers = load_career_catalog()
        assert len(careers) >= 10, f"expected >=10 careers, got {len(careers)}"

    def test_all_careers_have_required_fields(self):
        careers = load_career_catalog()
        required = ("id", "name", "riasec_profile", "field", "outlook")
        for career in careers:
            for field_name in required:
                assert career.get(field_name), f"career {career.get('id')} missing {field_name}"

    def test_career_ids_are_unique(self):
        careers = load_career_catalog()
        ids = [c["id"] for c in careers]
        assert len(ids) == len(set(ids)), f"duplicate career ids: {ids}"

    def test_riasec_profiles_are_three_letters(self):
        careers = load_career_catalog()
        for career in careers:
            code = career["riasec_profile"]
            assert len(code) == 3, f"career {career['id']} riasec={code!r} not 3 chars"
            valid = set("RIASEC")
            assert set(code).issubset(valid), (
                f"career {career['id']} riasec={code!r} has invalid letters"
            )

    def test_readme_is_not_loaded_as_career(self):
        """The README.md documents the schema; it must not appear as a career."""
        careers = load_career_catalog()
        ids = [c["id"] for c in careers]
        assert "readme" not in ids
        assert "README" not in ids


class TestParseCareerFile:
    """Direct tests for the file parser (uses tmp_path for isolation)."""

    def test_parses_valid_file(self, tmp_path):
        path = tmp_path / "test_career.md"
        path.write_text(
            "---\n"
            "id: test\n"
            "name: Test Career\n"
            "riasec_profile: irc\n"
            "field: Test Field\n"
            "outlook: positive\n"
            "---\n"
            "\n"
            "## Description\n"
            "Body content here.\n",
            encoding="utf-8",
        )
        career = _parse_career_file(path)
        assert career is not None
        assert career["id"] == "test"
        assert career["name"] == "Test Career"
        assert career["riasec_profile"] == "IRC"  # normalized to uppercase
        assert "Body content" in career["body"]

    def test_returns_none_without_frontmatter(self, tmp_path):
        path = tmp_path / "no_fm.md"
        path.write_text("just plain text\n", encoding="utf-8")
        assert _parse_career_file(path) is None

    def test_returns_none_with_malformed_frontmatter(self, tmp_path):
        path = tmp_path / "bad_fm.md"
        path.write_text("---\nid: only_one_dash\n", encoding="utf-8")
        assert _parse_career_file(path) is None

    def test_returns_none_missing_required_field(self, tmp_path):
        path = tmp_path / "missing.md"
        path.write_text(
            "---\nid: x\nname: X\nriasec_profile: IRC\n---\n",  # missing field + outlook
            encoding="utf-8",
        )
        assert _parse_career_file(path) is None


class TestReloadCatalog:
    """reload_career_catalog() must invalidate the cache."""

    def test_reload_returns_fresh_catalog(self, tmp_path, monkeypatch):
        # Redirect CATALOG_DIR for this test only.
        # CATALOG_DIR lives in src.tools.catalog.loader (after the
        # handler/tool refactor in Sprint 4 §4.5).
        monkeypatch.setattr("src.tools.catalog.loader.CATALOG_DIR", tmp_path)
        # Clear any prior cache from other tests
        reload_career_catalog()

        # Empty dir -> empty catalog
        assert load_career_catalog() == []

        # Add a career file
        (tmp_path / "new.md").write_text(
            "---\nid: new\nname: New\nriasec_profile: IRC\nfield: X\noutlook: y\n---\n",
            encoding="utf-8",
        )

        # Without reload, the module-level _CACHE would still serve the empty list.
        # reload_career_catalog() must invalidate it.
        careers = reload_career_catalog()
        assert len(careers) == 1
        assert careers[0]["id"] == "new"
