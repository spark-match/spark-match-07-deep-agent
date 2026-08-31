"""Tests for Settings validation (Sprint 7, task 7.E.1 — CORS validator;
Sprint 8, task 8.6 — max_tokens)."""

import pytest

from src.config.settings import Settings


class TestCorsOriginsValidation:
    def test_valid_https_origin_is_accepted(self):
        settings = Settings(cors_origins=["https://app.spark-match.com"])
        assert settings.cors_origins == ["https://app.spark-match.com"]

    def test_default_origin_is_accepted(self):
        settings = Settings()
        assert settings.cors_origins == ["http://localhost:4200"]

    def test_wildcard_origin_is_rejected(self):
        with pytest.raises(ValueError, match="must not contain '\\*'"):
            Settings(cors_origins=["*"])

    def test_wildcard_among_other_origins_is_still_rejected(self):
        with pytest.raises(ValueError, match="must not contain '\\*'"):
            Settings(cors_origins=["https://app.spark-match.com", "*"])

    def test_origin_without_scheme_is_rejected(self):
        with pytest.raises(ValueError, match="must start with"):
            Settings(cors_origins=["app.spark-match.com"])


class TestMaxTokensSetting:
    """Sprint 8, task 8.6: max_tokens configurable via SPARK_MAX_TOKENS."""

    def test_default_is_4096(self):
        """Subido de 2048 el 2026-08-09.

        Desde la familia 4.6 el thinking adaptativo comparte `max_tokens`
        con el texto de la respuesta en vez de ir aparte, asi que 2048
        cortaba respuestas normales a media frase (stop_reason=max_tokens)
        en cuanto el modelo razonaba un poco antes de contestar.
        """
        assert Settings().max_tokens == 4096

    def test_explicit_override(self):
        assert Settings(max_tokens=8192).max_tokens == 8192

    def test_env_var_override(self, monkeypatch):
        monkeypatch.setenv("SPARK_MAX_TOKENS", "1024")
        assert Settings().max_tokens == 1024
