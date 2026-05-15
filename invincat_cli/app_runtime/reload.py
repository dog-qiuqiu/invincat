"""Reload command presentation helpers for the Textual app."""

from __future__ import annotations


def build_reload_report(changes: list[str], *, theme_reload_ok: bool) -> str:
    """Build the `/reload` command result message."""
    if changes:
        report = "Configuration reloaded. Changes:\n" + "\n".join(
            f"  - {change}" for change in changes
        )
    else:
        report = "Configuration reloaded. No changes detected."

    report += "\nModel config caches cleared."
    if theme_reload_ok:
        report += "\nTheme registry reloaded."
    else:
        report += "\nTheme registry reload failed. Check config.toml for errors."
    return report
