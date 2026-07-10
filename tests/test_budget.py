"""Tests for the per-session budget tracker."""

from src import budget
from src.budget import (
    DEFAULT_SESSION_ID,
    get_active_session,
    get_web_search_count,
    increment_web_search,
    reset_session_budget,
)


class TestWebSearchCounter:
    """Per-session web-search counter behavior."""

    def test_initial_count_is_zero(self):
        reset_session_budget("test_init_zero")
        assert get_web_search_count("test_init_zero") == 0

    def test_increment_returns_new_count(self):
        reset_session_budget("test_inc")
        assert increment_web_search("test_inc") == 1
        assert increment_web_search("test_inc") == 2
        assert increment_web_search("test_inc") == 3

    def test_sessions_are_isolated(self):
        reset_session_budget("session_a")
        reset_session_budget("session_b")

        increment_web_search("session_a")
        increment_web_search("session_a")
        increment_web_search("session_b")

        assert get_web_search_count("session_a") == 2
        assert get_web_search_count("session_b") == 1

    def test_reset_clears_counter(self):
        reset_session_budget("test_reset")
        increment_web_search("test_reset")
        increment_web_search("test_reset")
        assert get_web_search_count("test_reset") == 2

        reset_session_budget("test_reset")
        assert get_web_search_count("test_reset") == 0


class TestActiveSessionContext:
    """ContextVar-based active session handling.

    Note: pytest runs in a single thread, so the ContextVar carries across
    tests. We always reset to ``DEFAULT_SESSION_ID`` after exercising the
    setter so other tests see a clean state.
    """

    def setup_method(self):
        budget._active_session.set(DEFAULT_SESSION_ID)
        reset_session_budget()

    def teardown_method(self):
        budget._active_session.set(DEFAULT_SESSION_ID)
        reset_session_budget()

    def test_default_session_id(self):
        assert get_active_session() == DEFAULT_SESSION_ID

    def test_set_active_session_via_contextvar(self):
        budget._active_session.set("test_active_1")
        try:
            assert get_active_session() == "test_active_1"
            reset_session_budget()  # uses active session
            increment_web_search()
            assert get_web_search_count() == 1
        finally:
            budget._active_session.set(DEFAULT_SESSION_ID)
