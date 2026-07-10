"""Shared pytest fixtures for the test suite.

The catalog loader uses a module-level cache for performance. When the
TestReloadCatalog test runs, it mutates that cache with a tmp_path,
which leaks into subsequent tests. We use an autouse fixture that
reloads the cache from the real catalog directory after each test,
so the catalog is always in a clean state.
"""

import pytest

from src.tools.catalog import reload_career_catalog


@pytest.fixture(autouse=True)
def _reset_catalog_cache(request):
    """Reset the career-catalog cache before each test."""
    # Reload BEFORE so we start from a clean state in case a previous
    # test polluted the cache.
    reload_career_catalog()
    yield
    # Reload AFTER too, so this test's mutations don't leak.
    reload_career_catalog()
