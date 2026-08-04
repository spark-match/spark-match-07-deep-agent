"""Tests for the Sprint 6 user_id placeholder helper."""

from src.agent.user_context import DEFAULT_USER_ID, get_user_id


class _FakeRuntimeNoContext:
    context = None


class _FakeRuntimeDictContext:
    context = {"user_id": "user-42"}


class _FakeRuntimeAttrContext:
    class _Ctx:
        user_id = "user-99"

    context = _Ctx()


class _FakeRuntimeEmptyUserId:
    context = {"user_id": ""}


def test_falls_back_to_default_when_context_is_none():
    assert get_user_id(_FakeRuntimeNoContext()) == DEFAULT_USER_ID


def test_reads_user_id_from_dict_context():
    assert get_user_id(_FakeRuntimeDictContext()) == "user-42"


def test_reads_user_id_from_attribute_context():
    assert get_user_id(_FakeRuntimeAttrContext()) == "user-99"


def test_falls_back_to_default_when_user_id_is_empty():
    assert get_user_id(_FakeRuntimeEmptyUserId()) == DEFAULT_USER_ID
