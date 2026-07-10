"""Career catalog loader.

Careers live as Markdown files under ``data/careers/`` with YAML frontmatter.
This module:

1. Discovers all ``*.md`` files in the catalog directory at startup.
2. Parses YAML frontmatter into :class:`Career` TypedDicts.
3. Exposes :func:`load_career_catalog` for callers that want to refresh from disk,
   and :func:`reload_career_catalog` to clear the cache.

Adding a new career = adding one ``.md`` file. No Python changes needed.

Fallback behavior: if no ``.md`` files are found (e.g. during tests with a
stub catalog), returns an empty list. Tools handle the empty case gracefully.

This module was extracted from ``src/tools/catalog.py`` during the
handler/tool refactor (Sprint 4 §4.5). It contains pure data loading logic
with no LLM or @tool dependencies.
"""

import logging
from pathlib import Path
from typing import TypedDict

import yaml

logger = logging.getLogger(__name__)

# Default location for the career catalog. Resolved relative to the repo root
# so it works both in local dev and inside AgentCore containers.
CATALOG_DIR = Path(__file__).resolve().parents[3] / "data" / "careers"


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


_CACHE: list[Career] | None = None


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
    global _CACHE
    if _CACHE is not None:
        return list(_CACHE)

    catalog_dir = directory or CATALOG_DIR
    if not catalog_dir.exists():
        logger.warning("Career catalog directory %s does not exist", catalog_dir)
        _CACHE = []
        return []

    careers: list[Career] = []
    for md_file in sorted(catalog_dir.glob("*.md")):
        if md_file.name.lower() == "readme.md":
            continue
        parsed = _parse_career_file(md_file)
        if parsed is not None:
            careers.append(parsed)
    careers.sort(key=lambda c: c["id"])
    _CACHE = careers
    return list(_CACHE)


def reload_career_catalog(directory: Path | None = None) -> list[Career]:
    """Clear the cache and reload from disk. Useful for tests."""
    global _CACHE
    _CACHE = None
    return load_career_catalog(directory)
