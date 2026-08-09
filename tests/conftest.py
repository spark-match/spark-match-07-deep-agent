"""Shared pytest fixtures for the test suite.

El catalogo se cachea a nivel de modulo (`_CACHE` para los 6.208 programas y
`_CAREERS_CACHE` para la vista de 554 carreras que se deriva de ellos). Los
tests que cargan un CSV de `tmp_path` ensucian esas caches, y sin limpiarlas la
suciedad se filtra al siguiente test. Esta fixture las reinicia antes y despues
de cada uno: antes por si algo anterior las dejo sucias, despues para no
ensuciar a nadie.

`reload_careers` limpia las dos, asi que basta con una llamada.
"""

import pytest

from src.tools.programs.loader import reload_careers


@pytest.fixture(autouse=True)
def _reset_catalog_cache():
    """Reinicia las caches del catalogo alrededor de cada test."""
    reload_careers()
    yield
    reload_careers()
