"""Tests for the role/capability model (Sprint 7, task 7.D)."""

from src.auth.roles import CAPABILITIES, DEFAULT_ROLE, Role, has_capability, resolve_role


class TestResolveRole:
    def test_known_role_resolves(self):
        assert resolve_role("admin") is Role.ADMIN
        assert resolve_role("docente") is Role.DOCENTE
        assert resolve_role("graduado") is Role.GRADUADO
        assert resolve_role("student") is Role.STUDENT

    def test_unknown_role_falls_back_to_default(self):
        assert resolve_role("superadmin") is DEFAULT_ROLE

    def test_none_role_falls_back_to_default(self):
        assert resolve_role(None) is DEFAULT_ROLE

    def test_default_is_least_privileged(self):
        # An unrecognized/missing role must never be silently upgraded.
        assert DEFAULT_ROLE == Role.STUDENT


class TestHasCapability:
    def test_admin_has_every_capability(self):
        assert has_capability(Role.ADMIN, "assessment")
        assert has_capability(Role.ADMIN, "anything_undefined")

    def test_student_has_only_its_granted_capabilities(self):
        assert has_capability(Role.STUDENT, "assessment")
        assert not has_capability(Role.STUDENT, "view_cohort")

    def test_graduado_cannot_take_assessment(self):
        assert not has_capability(Role.GRADUADO, "assessment")
        assert has_capability(Role.GRADUADO, "matching")

    def test_every_role_has_a_capability_set(self):
        for role in Role:
            assert role in CAPABILITIES
