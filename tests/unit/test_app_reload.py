"""Tests for `/reload` result formatting."""

from __future__ import annotations

from invincat_cli.app_reload import build_reload_report


def test_build_reload_report_with_changes() -> None:
    assert build_reload_report(
        ["model changed", "profile changed"],
        theme_reload_ok=True,
    ) == (
        "Configuration reloaded. Changes:\n"
        "  - model changed\n"
        "  - profile changed\n"
        "Model config caches cleared.\n"
        "Theme registry reloaded."
    )


def test_build_reload_report_without_changes_and_theme_failure() -> None:
    assert build_reload_report([], theme_reload_ok=False) == (
        "Configuration reloaded. No changes detected.\n"
        "Model config caches cleared.\n"
        "Theme registry reload failed. Check config.toml for errors."
    )
