"""Tests for `/version` response helpers."""

from __future__ import annotations

import builtins
import importlib.metadata
from importlib.metadata import PackageNotFoundError

from invincat_cli.app_runtime.version import (
    build_version_message,
    resolve_version_message,
)
from invincat_cli.i18n import Language, set_language


def test_build_version_message() -> None:
    set_language(Language.EN)

    assert build_version_message(cli_version="1.2.3", sdk_version="4.5.6") == (
        "deepagents-cli version: 1.2.3\ndeepagents (SDK) version: 4.5.6"
    )


def test_build_version_message_with_unknowns() -> None:
    set_language(Language.EN)

    assert build_version_message(cli_version=None, sdk_version=None) == (
        "deepagents-cli version: unknown\ndeepagents (SDK) version: unknown"
    )


def test_resolve_version_message_with_injected_package_lookup() -> None:
    set_language(Language.EN)

    assert (
        resolve_version_message(
            cli_version="1.2.3",
            package_version=lambda package: "4.5.6",
        )
        == "deepagents-cli version: 1.2.3\ndeepagents (SDK) version: 4.5.6"
    )


def test_resolve_version_message_handles_missing_sdk() -> None:
    set_language(Language.EN)

    def _missing(_package: str) -> str:
        raise PackageNotFoundError

    assert (
        resolve_version_message(
            cli_version="1.2.3",
            package_version=_missing,
        )
        == "deepagents-cli version: 1.2.3\ndeepagents (SDK) version: unknown"
    )


def test_resolve_version_message_handles_sdk_lookup_error() -> None:
    set_language(Language.EN)

    def _broken(_package: str) -> str:
        raise RuntimeError("metadata broken")

    assert (
        resolve_version_message(
            cli_version="1.2.3",
            package_version=_broken,
        )
        == "deepagents-cli version: 1.2.3\ndeepagents (SDK) version: unknown"
    )


def test_resolve_version_message_uses_default_cli_version() -> None:
    set_language(Language.EN)

    message = resolve_version_message(package_version=lambda _package: "4.5.6")

    assert "deepagents-cli version: " in message
    assert "deepagents (SDK) version: 4.5.6" in message


def test_resolve_version_message_uses_default_package_lookup(monkeypatch) -> None:
    set_language(Language.EN)
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda package: "4.5.6" if package == "deepagents" else "0",
    )

    assert (
        resolve_version_message(cli_version="1.2.3")
        == "deepagents-cli version: 1.2.3\ndeepagents (SDK) version: 4.5.6"
    )


def test_resolve_version_message_handles_cli_import_error(monkeypatch) -> None:
    set_language(Language.EN)
    real_import = builtins.__import__

    def import_with_missing_cli_version(
        name: str,
        globals_: dict | None = None,
        locals_: dict | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "invincat_cli.core.version" and "__version__" in fromlist:
            raise ImportError("missing version module")
        return real_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_with_missing_cli_version)

    assert (
        resolve_version_message(package_version=lambda _package: "4.5.6")
        == "deepagents-cli version: unknown\ndeepagents (SDK) version: 4.5.6"
    )


def test_resolve_version_message_handles_unexpected_cli_lookup_error(
    monkeypatch,
) -> None:
    set_language(Language.EN)
    real_import = builtins.__import__

    def import_with_broken_cli_version(
        name: str,
        globals_: dict | None = None,
        locals_: dict | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> object:
        if name == "invincat_cli.core.version" and "__version__" in fromlist:
            raise RuntimeError("broken version module")
        return real_import(name, globals_, locals_, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", import_with_broken_cli_version)

    assert (
        resolve_version_message(package_version=lambda _package: "4.5.6")
        == "deepagents-cli version: unknown\ndeepagents (SDK) version: 4.5.6"
    )
