"""Tests for `/version` response helpers."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError

from invincat_cli.app_runtime.version import build_version_message, resolve_version_message
from invincat_cli.i18n import Language, set_language


def test_build_version_message() -> None:
    set_language(Language.EN)

    assert build_version_message(cli_version="1.2.3", sdk_version="4.5.6") == (
        "deepagents-cli version: 1.2.3\n"
        "deepagents (SDK) version: 4.5.6"
    )


def test_build_version_message_with_unknowns() -> None:
    set_language(Language.EN)

    assert build_version_message(cli_version=None, sdk_version=None) == (
        "deepagents-cli version: unknown\n"
        "deepagents (SDK) version: unknown"
    )


def test_resolve_version_message_with_injected_package_lookup() -> None:
    set_language(Language.EN)

    assert resolve_version_message(
        cli_version="1.2.3",
        package_version=lambda package: "4.5.6",
    ) == "deepagents-cli version: 1.2.3\ndeepagents (SDK) version: 4.5.6"


def test_resolve_version_message_handles_missing_sdk() -> None:
    set_language(Language.EN)

    def _missing(_package: str) -> str:
        raise PackageNotFoundError

    assert resolve_version_message(
        cli_version="1.2.3",
        package_version=_missing,
    ) == "deepagents-cli version: 1.2.3\ndeepagents (SDK) version: unknown"
