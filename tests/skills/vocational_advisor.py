"""Content assertions for skills/vocational_advisor/SKILL.md.

Unlike src/prompts/*.md (loaded via src.prompts.loader with frontmatter
parsing), skills/ files are read directly off disk by deepagents'
SkillsMiddleware through the FilesystemBackend built in
src/agent/backends.py. There is no Python loader to import here, so this
test reads the file the same way a human reviewer would.
"""

from src.agent.backends import _SKILLS_DIR

_SKILL_PATH = _SKILLS_DIR / "vocational_advisor" / "SKILL.md"


class TestVocationalAdvisorLanguageRule:
    """Sprint 9, task 9.A.5: defense-in-depth copy of the LANGUAGE RULE.

    Ported verbatim in spirit from the POC v2 Harness fix (all 3 of its
    skills got this preamble, not just the system prompt — see
    ``../orion/spark-match-poc-v2/skills/v1/riasec.md`` and
    ``../orion/spark-match-poc-v2/docs/AWS-HARNESS-POC-V5.md``), applied
    here to the one skill this repo actually exposes
    (``skills/vocational_advisor/``).
    """

    def test_skill_file_exists(self):
        assert _SKILL_PATH.exists()

    def test_skill_has_language_rule(self):
        body = _SKILL_PATH.read_text(encoding="utf-8")
        assert "LANGUAGE RULE" in body
        assert "Ignore the student's name" in body

    def test_language_rule_is_near_the_top_not_buried(self):
        body = _SKILL_PATH.read_text(encoding="utf-8")
        marker_pos = body.index("LANGUAGE RULE")
        assert marker_pos < len(body) / 3
