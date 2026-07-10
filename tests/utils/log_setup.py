"""Tests for the logging utility."""

import logging

from src.utils import setup_logging


def test_setup_logging_idempotent():
    """Re-running setup_logging does not stack handlers."""
    setup_logging(level="INFO")
    handler_count = len(logging.getLogger().handlers)

    setup_logging(level="DEBUG")  # second call

    assert len(logging.getLogger().handlers) == handler_count, "setup_logging must be idempotent"


def test_setup_logging_changes_root_level():
    """Second call with a different level updates the root level."""
    setup_logging(level="WARNING")
    assert logging.getLogger().level == logging.WARNING

    setup_logging(level="DEBUG")
    assert logging.getLogger().level == logging.DEBUG


def test_setup_logging_accepts_str_level():
    """String level names are resolved to integers."""
    setup_logging(level="ERROR")
    assert logging.getLogger().level == logging.ERROR


def test_setup_logging_accepts_logger_instance():
    """A logging.Logger can be passed in place of a level."""
    sub = logging.getLogger("src.utils.sub_logger_instance_test")
    sub.setLevel(logging.CRITICAL)
    setup_logging(level=sub)
    assert logging.getLogger().level == logging.CRITICAL


def test_setup_logging_quietens_noisy_loggers():
    """At INFO, third-party noisy loggers are quieted to WARNING."""
    setup_logging(level="DEBUG")
    setup_logging(level="INFO")  # noisy -> WARNING
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("urllib3").level == logging.WARNING


def test_setup_logging_debug_unquiets_noisy_loggers():
    """Switching back to DEBUG resets third-party loggers to NOTSET."""
    setup_logging(level="INFO")
    setup_logging(level="DEBUG")
    # NOTSET means "let root decide" — root is DEBUG, so they will pass through.
    assert logging.getLogger("httpx").level == logging.NOTSET


def test_setup_logging_emits_message(caplog):
    """Messages emitted after setup_logging reach the root handler."""
    setup_logging(level="DEBUG")

    logger = logging.getLogger("src.utils.test_emits")
    with caplog.at_level(logging.INFO):
        logger.info("hello-caplog")

    assert "hello-caplog" in caplog.text
