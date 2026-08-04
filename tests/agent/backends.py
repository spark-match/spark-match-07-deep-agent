"""Tests for the composite memory-files backend (Sprint 6, task 6.B)."""

from deepagents.backends import CompositeBackend, StateBackend, StoreBackend

from src.agent.backends import MEMORY_ROOT, _memory_namespace, build_backend
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
