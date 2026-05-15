"""Tests for package-level lazy exports."""

from __future__ import annotations

import invincat_cli
from invincat_cli.core.version import __version__


def test_package_exports_version() -> None:
    assert invincat_cli.__version__ == __version__


def test_getattr_lazy_loads_cli_main() -> None:
    from invincat_cli.main import cli_main

    assert invincat_cli.__getattr__("cli_main") is cli_main


def test_getattr_rejects_unknown_attribute() -> None:
    missing = "missing_attribute"

    try:
        invincat_cli.__getattr__(missing)
    except AttributeError as exc:
        assert missing in str(exc)
    else:
        raise AssertionError("expected AttributeError")
