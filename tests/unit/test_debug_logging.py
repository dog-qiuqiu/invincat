"""Tests for debug logging configuration."""

from __future__ import annotations

import logging

import pytest

import invincat_cli.core.debug as debug_mod
from invincat_cli.core.debug import configure_debug_logging
from invincat_cli.core.env_vars import DEBUG, DEBUG_FILE


def test_configure_debug_logging_noops_without_debug_env(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv(DEBUG, raising=False)
    monkeypatch.setenv(DEBUG_FILE, str(tmp_path / "debug.log"))
    logger = logging.getLogger("invincat-test-debug-disabled")
    logger.handlers.clear()

    configure_debug_logging(logger)

    assert logger.handlers == []


def test_configure_debug_logging_writes_to_configured_file(
    monkeypatch, tmp_path
) -> None:
    debug_file = tmp_path / "debug.log"
    monkeypatch.setenv(DEBUG, "1")
    monkeypatch.setenv(DEBUG_FILE, str(debug_file))
    logger = logging.getLogger("invincat-test-debug-enabled")
    logger.handlers.clear()

    try:
        configure_debug_logging(logger)
        logger.debug("hello debug")
    finally:
        for handler in logger.handlers:
            handler.close()
        logger.handlers.clear()

    assert logger.level == logging.DEBUG
    assert "hello debug" in debug_file.read_text()


def test_configure_debug_logging_reports_file_open_failure(
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(DEBUG, "1")
    monkeypatch.setenv(DEBUG_FILE, "/tmp/missing/debug.log")

    def fail_file_handler(*_args: object, **_kwargs: object) -> logging.FileHandler:
        raise OSError("cannot open")

    monkeypatch.setattr(debug_mod.logging, "FileHandler", fail_file_handler)
    logger = logging.getLogger("invincat-test-debug-failure")
    logger.handlers.clear()

    configure_debug_logging(logger)

    assert logger.handlers == []
    assert (
        "could not open debug log file /tmp/missing/debug.log"
        in capsys.readouterr().err
    )
