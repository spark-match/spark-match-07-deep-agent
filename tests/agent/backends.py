"""Tests for the composite memory-files backend (Sprint 6, task 6.B;
Sprint 8, task 8.3 adds the /skills/ route)."""

from pathlib import Path

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend

from src.agent.backends import MEMORY_ROOT, SKILLS_ROOT, _memory_namespace, build_backend
from src.agent.user_context import DEFAULT_USER_ID


class _FakeRuntimeNoContext:
    context = None


class _FakeRuntimeWithUser:
    context = {"user_id": "user-42"}


def test_build_backend_returns_a_composite_backend():
    backend = build_backend()
    assert isinstance(backend, CompositeBackend)


def test_default_route_is_the_ephemeral_state_backend():
    backend = build_backend()
    assert isinstance(backend.default, StateBackend)


def test_memories_route_is_a_store_backend():
    backend = build_backend()
    assert MEMORY_ROOT in backend.routes
    assert isinstance(backend.routes[MEMORY_ROOT], StoreBackend)


def test_memory_namespace_uses_the_placeholder_user_id_when_no_context():
    ns = _memory_namespace(_FakeRuntimeNoContext())
    assert ns == ("spark-match", DEFAULT_USER_ID, "files")


def test_memory_namespace_uses_the_real_user_id_when_present():
    ns = _memory_namespace(_FakeRuntimeWithUser())
    assert ns == ("spark-match", "user-42", "files")


class TestSkillsRoute:
    """Sprint 8, task 8.3: /skills/ routed to a FilesystemBackend scoped to
    the repo's skills/ directory (not the repo root — see the security
    note in src/agent/backends.py's module docstring)."""

    def test_skills_route_is_a_filesystem_backend(self):
        backend = build_backend()
        assert SKILLS_ROOT in backend.routes
        assert isinstance(backend.routes[SKILLS_ROOT], FilesystemBackend)

    def test_skills_backend_is_scoped_to_the_skills_directory_not_repo_root(self):
        """Security: root_dir must be skills/ itself so a FilesystemBackend
        traversal bug can't reach .env, .git/, or application source."""
        backend = build_backend()
        skills_backend = backend.routes[SKILLS_ROOT]
        assert Path(skills_backend.cwd).name == "skills"

    def test_skills_backend_uses_virtual_mode_for_traversal_guardrails(self):
        backend = build_backend()
        skills_backend = backend.routes[SKILLS_ROOT]
        assert skills_backend.virtual_mode is True

    def test_skills_route_can_list_the_vocational_advisor_skill_directory(self):
        """End-to-end: the real on-disk skill is reachable through the exact
        backend + path SkillsMiddleware will use
        (deepagents.middleware.skills lists sources via backend.ls(...))."""
        backend = build_backend()
        skills_backend = backend.routes[SKILLS_ROOT]

        result = skills_backend.ls("/")

        assert result.error is None
        directory_names = {entry["path"].strip("/") for entry in result.entries}
        assert "vocational_advisor" in directory_names
