"""Tests for the markdown prompt loader."""

from src.prompts import (
    ASSESSMENT_SYSTEM_PROMPT,
    MATCHING_SYSTEM_PROMPT,
    PLANNING_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    list_prompts,
    reload_prompts,
)
from src.prompts.loader import (
    PROMPTS_DIR,
    _parse_prompt_file,
    get_prompt_metadata,
    load_prompt,
)


class TestPromptLoader:
    """Loader returns the expected prompts on disk."""

    def test_prompts_dir_exists(self):
        assert PROMPTS_DIR.exists()

    def test_list_prompts_returns_four(self):
        names = list_prompts()
        assert "coordinator" in names
        assert "assessment" in names
        assert "matching" in names
        assert "planning" in names
        assert len(names) == 4

    def test_coordinator_loaded(self):
        body = load_prompt("coordinator")
        assert "Spark Match" in body
        assert "delegar" in body.lower()

    def test_assessment_loaded(self):
        body = load_prompt("assessment")
        assert "RIASEC" in body
        assert "evaluate_riasec_profile" in body

    def test_matching_loaded(self):
        body = load_prompt("matching")
        assert "afinidad" in body.lower()
        assert "calculate_affinity" in body

    def test_planning_loaded(self):
        body = load_prompt("planning")
        assert "plan" in body.lower()
        assert "Quick wins" in body

    def test_metadata_parsed(self):
        meta = get_prompt_metadata("coordinator")
        assert meta["versioned"] is True
        assert "coordinator" in meta["audience"].lower()

    def test_reload_is_idempotent(self):
        # Should not raise
        reload_prompts()
        reload_prompts()
        assert "Spark Match" in load_prompt("coordinator")


class TestPromptReExports:
    """The package re-exports match what the loader returns."""

    def test_system_prompt_matches_loader(self):
        assert load_prompt("coordinator") == SYSTEM_PROMPT

    def test_assessment_prompt_matches_loader(self):
        assert load_prompt("assessment") == ASSESSMENT_SYSTEM_PROMPT

    def test_matching_prompt_matches_loader(self):
        assert load_prompt("matching") == MATCHING_SYSTEM_PROMPT

    def test_planning_prompt_matches_loader(self):
        assert load_prompt("planning") == PLANNING_SYSTEM_PROMPT


class TestParsePromptFile:
    """Edge cases for the frontmatter parser."""

    def test_parses_with_frontmatter(self, tmp_path):
        path = tmp_path / "with_fm.md"
        path.write_text(
            "---\naudience: test\nversioned: true\n---\nBody content here.",
            encoding="utf-8",
        )
        meta, body = _parse_prompt_file(path)
        assert meta["audience"] == "test"
        assert meta["versioned"] is True
        assert body == "Body content here."

    def test_parses_without_frontmatter(self, tmp_path):
        path = tmp_path / "no_fm.md"
        path.write_text("Just body, no frontmatter.", encoding="utf-8")
        meta, body = _parse_prompt_file(path)
        assert meta["audience"] == ""
        assert body == "Just body, no frontmatter."

    def test_raises_on_malformed_yaml(self, tmp_path):
        path = tmp_path / "bad_yaml.md"
        path.write_text(
            "---\naudience: [unclosed bracket\n---\nbody",
            encoding="utf-8",
        )
        import pytest

        with pytest.raises(ValueError, match="Invalid YAML"):
            _parse_prompt_file(path)
