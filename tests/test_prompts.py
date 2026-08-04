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

    def test_list_prompts_returns_five(self):
        names = list_prompts()
        assert "coordinator" in names
        assert "assessment" in names
        assert "matching" in names
        assert "planning" in names
        assert "user_memory_seed" in names  # Sprint 6, task 6.C
        assert len(names) == 5

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


class TestLanguageRule:
    """Sprint 9, task 9.A.5: an explicit, high-priority LANGUAGE RULE in
    every prompt the model can be driven by — not just the coordinator's.

    Root cause (measured in the POC v2 Harness, see
    ``../orion/AWS-HARNESS-POC-V5.md``): subagents get their own
    independent ``system_prompt`` (confirmed in
    ``src/agent/subagents/*.py`` — the coordinator's prompt is never
    concatenated onto a subagent's), so a language rule that only lives
    in ``coordinator.md`` never reaches a turn handled by ``assessment``,
    ``matching``, or ``planning``. Each of the 4 prompts must carry its
    own copy.
    """

    _MARKER = "LANGUAGE RULE"
    _NAME_BIAS_GUARD = "Ignora el nombre"

    def test_coordinator_has_language_rule(self):
        assert self._MARKER in SYSTEM_PROMPT
        assert self._NAME_BIAS_GUARD in SYSTEM_PROMPT

    def test_assessment_has_language_rule(self):
        assert self._MARKER in ASSESSMENT_SYSTEM_PROMPT
        assert self._NAME_BIAS_GUARD in ASSESSMENT_SYSTEM_PROMPT

    def test_matching_has_language_rule(self):
        assert self._MARKER in MATCHING_SYSTEM_PROMPT
        assert self._NAME_BIAS_GUARD in MATCHING_SYSTEM_PROMPT

    def test_planning_has_language_rule(self):
        assert self._MARKER in PLANNING_SYSTEM_PROMPT
        assert self._NAME_BIAS_GUARD in PLANNING_SYSTEM_PROMPT

    def test_language_rule_is_near_the_top_not_buried(self):
        """Placement matters: the POC v2 fix moved the rule to the front
        of each prompt/skill (not left as a low-priority trailing bullet)
        and that's what measured +46% language match. Assert it appears
        within roughly the first third of the prompt body, not just
        "somewhere"."""
        for name, body in (
            ("coordinator", SYSTEM_PROMPT),
            ("assessment", ASSESSMENT_SYSTEM_PROMPT),
            ("matching", MATCHING_SYSTEM_PROMPT),
            ("planning", PLANNING_SYSTEM_PROMPT),
        ):
            marker_pos = body.index(self._MARKER)
            assert marker_pos < len(body) / 3, (
                f"{name}: LANGUAGE RULE is not near the top of the prompt"
            )


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
