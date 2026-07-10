"""Career catalog loader.

Careers live as Markdown files under ``data/careers/`` with YAML frontmatter.
This module:

1. Discovers all ``*.md`` files in the catalog directory at startup.
2. Parses YAML frontmatter into :class:`Career` TypedDicts.
3. Exposes :data:`CAREER_CATALOG` for tools that need an in-memory list, plus
   :func:`load_career_catalog` for callers that want to refresh from disk.

Adding a new career = adding one ``.md`` file. No Python changes needed.

Fallback behavior: if no ``.md`` files are found (e.g. during tests with a
stub catalog), returns an empty list. Tools handle the empty case gracefully.
"""

import logging
from functools import lru_cache
from pathlib import Path
from typing import TypedDict

import yaml
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Default location for the career catalog. Resolved relative to the repo root
# so it works both in local dev and inside AgentCore containers.
CATALOG_DIR = Path(__file__).resolve().parents[2] / "data" / "careers"


class Career(TypedDict):
    """Shape of a career entry in the catalog.

    Loaded from YAML frontmatter; body markdown is preserved as ``body``
    for the planning subagent's resource recommendations.
    """

    id: str
    name: str
    riasec_profile: str
    field: str
    outlook: str
    body: str  # Markdown body (description + resources), may be empty


def _parse_career_file(path: Path) -> Career | None:
    """Parse a single ``.md`` career file. Returns None on malformed input."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Cannot read career file %s: %s", path, exc)
        return None

    if not text.startswith("---"):
        logger.warning("Career file %s missing YAML frontmatter; skipping", path.name)
        return None

    # Split frontmatter from body. The closing '---' must be on its own line.
    parts = text.split("---", 2)
    if len(parts) < 3:
        logger.warning("Career file %s has malformed frontmatter; skipping", path.name)
        return None

    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        logger.warning("Career file %s has invalid YAML: %s; skipping", path.name, exc)
        return None

    required = ("id", "name", "riasec_profile", "field", "outlook")
    missing = [k for k in required if k not in meta]
    if missing:
        logger.warning(
            "Career file %s missing required frontmatter fields %s; skipping",
            path.name,
            missing,
        )
        return None

    body = parts[2].strip()
    return Career(
        id=str(meta["id"]),
        name=str(meta["name"]),
        riasec_profile=str(meta["riasec_profile"]).upper(),
        field=str(meta["field"]),
        outlook=str(meta["outlook"]),
        body=body,
    )


def load_career_catalog(directory: Path | None = None) -> list[Career]:
    """Discover and parse all career files in the given directory.

    Returns an empty list if the directory does not exist or contains no
    valid career files. Sort by ``id`` so the catalog order is stable.
    """
    catalog_dir = directory or CATALOG_DIR
    if not catalog_dir.exists():
        logger.warning("Career catalog directory %s does not exist", catalog_dir)
        return []

    careers: list[Career] = []
    for path in sorted(catalog_dir.glob("*.md")):
        # Skip the README — it documents the schema, not a career.
        if path.name.lower() == "readme.md":
            continue
        career = _parse_career_file(path)
        if career is not None:
            careers.append(career)

    logger.info("Loaded %d careers from %s", len(careers), catalog_dir)
    return careers


@lru_cache
def _get_cached_catalog() -> tuple[Career, ...]:
    """Cached tuple version of the catalog for fast access.

    Tests can call :func:`reload_career_catalog` to invalidate this cache.
    """
    return tuple(load_career_catalog())


def reload_career_catalog() -> list[Career]:
    """Force a fresh load from disk and return the new catalog.

    Useful in tests and in admin endpoints that allow runtime catalog edits.
    """
    _get_cached_catalog.cache_clear()
    return list(_get_cached_catalog())


# Public, in-memory catalog exposed to the rest of the codebase.
# `list(...)` so callers get a mutable copy and can't mutate the cache tuple.
CAREER_CATALOG: list[Career] = list(_get_cached_catalog())


@tool
def search_careers(query: str, field: str | None = None) -> list[Career]:
    """Search the career catalog by keyword or field.

    Performs simple text matching on the local catalog. In production,
    this will use pgvector semantic search.

    Args:
        query: Search query (career name, skill, or description keywords)
        field: Optional filter by field (e.g., 'Tecnología', 'Salud')
    """
    query_lower = query.lower()
    results: list[Career] = []

    for career in CAREER_CATALOG:
        searchable = " ".join(
            [
                career["name"].lower(),
                career["outlook"].lower(),
                career["body"].lower(),
                career["field"].lower(),
            ]
        )

        if query_lower in searchable and (
            field is None or field.lower() in career["field"].lower()
        ):
            results.append(career)

    # If no exact match, return all careers in the field (if specified)
    if not results and field:
        results = [c for c in CAREER_CATALOG if field.lower() in c["field"].lower()]

    # If still no results, return first 5 as suggestions
    if not results:
        results = CAREER_CATALOG[:5]

    return results
