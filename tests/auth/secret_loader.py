"""Tests for JWT secret resolution (Sprint 7, task 7.A).

Only exercises the local/dev override path (``settings.jwt_secret``) — the
SSM/Secrets Manager path requires real AWS credentials and is out of scope
for this offline test suite (hard rule #7 in AGENTS.md). Its own function
(``_fetch_from_aws``) is a thin, directly-attributable wrapper around two
boto3 calls, kept intentionally free of any branching logic to review.
"""

import pytest

from src.auth import secret_loader
from src.config import get_settings


@pytest.fixture(autouse=True)
def _reset_settings_and_cache():
    get_settings.cache_clear()
    secret_loader.clear_cache()
    yield
    get_settings.cache_clear()
    secret_loader.clear_cache()


class TestLoadJwtSecret:
    async def test_uses_local_override_verbatim_as_utf8_bytes(self, monkeypatch):
        monkeypatch.setenv("SPARK_JWT_SECRET", "my-signing-secret")
        get_settings.cache_clear()

        secret = await secret_loader.load_jwt_secret()

        assert secret == b"my-signing-secret"

    async def test_result_is_cached_across_calls(self, monkeypatch):
        monkeypatch.setenv("SPARK_JWT_SECRET", "first-value")
        get_settings.cache_clear()

        first = await secret_loader.load_jwt_secret()

        # Changing the setting after the first resolution must not affect
        # the cached value until clear_cache() is called.
        monkeypatch.setenv("SPARK_JWT_SECRET", "second-value")
        get_settings.cache_clear()

        second = await secret_loader.load_jwt_secret()

        assert first == second == b"first-value"

    async def test_clear_cache_forces_re_resolution(self, monkeypatch):
        monkeypatch.setenv("SPARK_JWT_SECRET", "first-value")
        get_settings.cache_clear()
        await secret_loader.load_jwt_secret()

        secret_loader.clear_cache()
        monkeypatch.setenv("SPARK_JWT_SECRET", "second-value")
        get_settings.cache_clear()

        second = await secret_loader.load_jwt_secret()

        assert second == b"second-value"
