"""Catalog package - busqueda a nivel de carrera.

El loader vive en ``src.tools.programs.loader`` (``load_careers``), no aqui:
esta vista es una proyeccion de ``programs.csv``, no una fuente propia. El
``loader.py`` que habia en este paquete leia ``data/careers/*.md`` y se retiro
el 2026-08-09 junto con esas fichas -- ver ADR-019 de spark-match-03-backend.
"""

from src.tools.catalog.handler import search_careers_handler
from src.tools.catalog.tool import search_careers

__all__ = [
    "search_careers",
    "search_careers_handler",
]
