"""Tests for the LangSmith wiring."""

from src.config import get_settings
from src.observability.langsmith import (
    ENV_API_KEY,
    ENV_PROJECT,
    ENV_TRACING,
    configure_langsmith,
    is_langsmith_enabled,
)


class TestLangSmithWiring:
    """Verify env-var push + idempotency."""

    def setup_method(self):
        # Clean state for each test
        for v in (ENV_TRACING, ENV_API_KEY, ENV_PROJECT):
            if v in __import__("os").environ:
                del __import__("os").environ[v]
        get_settings.cache_clear()

    def teardown_method(self):
        for v in (ENV_TRACING, ENV_API_KEY, ENV_PROJECT):
            if v in __import__("os").environ:
                del __import__("os").environ[v]
        get_settings.cache_clear()

    def test_disabled_when_tracing_flag_false(self, monkeypatch):
        monkeypatch.setenv("SPARK_LANGSMITH_TRACING", "false")
        monkeypatch.setenv("SPARK_LANGSMITH_API_KEY", "lsv2_fake_key")
        result = configure_langsmith()
        assert result is False
        assert is_langsmith_enabled() is False

    def test_disabled_when_api_key_missing(self, monkeypatch):
        monkeypatch.setenv("SPARK_LANGSMITH_TRACING", "true")
        monkeypatch.delenv("SPARK_LANGSMITH_API_KEY", raising=False)
        result = configure_langsmith()
        assert result is False
        assert is_langsmith_enabled() is False

    def test_enabled_when_all_set(self, monkeypatch):
        monkeypatch.setenv("SPARK_LANGSMITH_TRACING", "true")
        monkeypatch.setenv("SPARK_LANGSMITH_API_KEY", "lsv2_fake_key")
        monkeypatch.setenv("SPARK_LANGSMITH_PROJECT", "test-project")
        result = configure_langsmith()
        assert result is True
        assert is_langsmith_enabled() is True
        # Verify env vars were pushed
        import os

        assert os.environ[ENV_TRACING] == "true"
        assert os.environ[ENV_API_KEY] == "lsv2_fake_key"
        assert os.environ[ENV_PROJECT] == "test-project"

    def test_is_idempotent(self, monkeypatch):
        monkeypatch.setenv("SPARK_LANGSMITH_TRACING", "true")
        monkeypatch.setenv("SPARK_LANGSMITH_API_KEY", "lsv2_fake_key")
        # Calling twice should not duplicate anything or raise
        configure_langsmith()
        configure_langsmith()
        assert is_langsmith_enabled() is True

    def test_secretstr_masks_api_key_in_settings(self, monkeypatch):
        """Sanity check that the API key is a SecretStr (regression for §4.3)."""
        monkeypatch.setenv("SPARK_LANGSMITH_TRACING", "true")
        monkeypatch.setenv("SPARK_LANGSMITH_API_KEY", "lsv2_supersecret_xyz")
        configure_langsmith()
        settings = get_settings()
        # repr must NOT leak the raw key
        assert "supersecret" not in repr(settings.langsmith_api_key)
        # get_secret_value() gives the raw key
        assert settings.langsmith_api_key is not None
        assert settings.langsmith_api_key.get_secret_value() == "lsv2_supersecret_xyz"
