"""Unified logging configuration for the Spark Match Agent.

Idempotent: calling :func:`setup_logging` more than once with the same level
will not duplicate handlers. The standard format mirrors the workshop
reference so log lines can be grep'd uniformly across services.

Usage:
    from src.config import get_settings
    from src.utils import setup_logging

    setup_logging(level=get_settings().log_level)
"""

import logging
import sys

# Format aligned with langchain / langgraph default logger shape so log lines
# can be parsed by downstream collectors (CloudWatch, Opik, LangSmith).
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S"

# Track handler ids we've installed so setup_logging stays idempotent.
_INSTALLED_HANDLERS: set[int] = set()


def _resolve_level(level: str | int | logging.Logger) -> int:
    """Resolve a level name/number/logger to a logging level integer."""
    if isinstance(level, logging.Logger):
        return level.level if level.level != logging.NOTSET else logging.INFO
    if isinstance(level, int):
        return level
    if isinstance(level, str):
        return logging.getLevelNamesMapping().get(level.upper(), logging.INFO)
    return logging.INFO


def setup_logging(
    level: str | int | logging.Logger = logging.INFO,
    *,
    stream: object | None = None,
) -> None:
    """Configure the root logger with the standard format.

    Idempotent: re-running with the same level does not stack handlers.
    First call wins; subsequent calls only adjust the level of existing
    handlers that this module owns.

    Args:
        level: Logging level as int, str name ("DEBUG"/"INFO"/...), or a
            ``logging.Logger`` instance (its ``.level`` will be used).
        stream: Optional stream for the StreamHandler. Defaults to ``sys.stderr``
            so agent output on stdout (e.g., traces) stays uncluttered.
    """
    resolved = _resolve_level(level)

    root = logging.getLogger()
    root.setLevel(resolved)

    # Reuse an existing handler we own if possible (idempotency).
    existing = next(
        (h for h in root.handlers if id(h) in _INSTALLED_HANDLERS),
        None,
    )
    if existing is None:
        handler = logging.StreamHandler(stream if stream is not None else sys.stderr)  # type: ignore[arg-type]
        handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        root.addHandler(handler)
        _INSTALLED_HANDLERS.add(id(handler))
    else:
        existing.setLevel(resolved)
        if existing.formatter is None:
            existing.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))

    # Quiet down noisy third-party loggers unless we explicitly want DEBUG.
    # We always reset their level, so flipping between INFO and DEBUG in
    # the same process (e.g. tests) behaves predictably.
    if resolved > logging.DEBUG:
        for noisy in ("httpx", "httpcore", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    else:
        for noisy in ("httpx", "httpcore", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.NOTSET)
