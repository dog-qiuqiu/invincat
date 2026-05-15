"""App-bound update and auto-update handlers."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from invincat_cli.core.version import CHANGELOG_URL
from invincat_cli.i18n import t
from invincat_cli.widgets.messages import AppMessage, ErrorMessage, UserMessage

logger = logging.getLogger(__name__)


async def check_for_updates(app: Any) -> None:  # noqa: ANN401
    """Check PyPI for a newer version and optionally auto-update."""
    try:
        from invincat_cli.update_check import (
            is_auto_update_enabled,
            is_update_available,
            upgrade_command,
        )

        available, latest = await asyncio.to_thread(is_update_available)
        if not available:
            return

        app._update_available = (True, latest)
    except Exception:
        logger.debug("Background update check failed", exc_info=True)
        return

    try:
        from invincat_cli.core.version import __version__ as cli_version

        if is_auto_update_enabled():
            from invincat_cli.update_check import perform_upgrade

            app.notify(
                t("app.updating_to", version=latest),
                severity="information",
                timeout=5,
            )
            success, _output = await perform_upgrade()
            if success:
                app.notify(
                    t("app.updated_to", version=latest),
                    severity="information",
                    timeout=10,
                )
            else:
                cmd = upgrade_command()
                app.notify(
                    t("app.auto_update_failed", command=cmd),
                    severity="warning",
                    timeout=15,
                    markup=False,
                )
        else:
            cmd = upgrade_command()
            app.notify(
                t(
                    "app.update_available",
                    latest=latest,
                    current=cli_version,
                    command=cmd,
                ),
                severity="information",
                timeout=15,
                markup=False,
            )
    except Exception:
        logger.warning("Auto-update failed unexpectedly", exc_info=True)
        app.notify(
            t("app.update_failed"),
            severity="warning",
            timeout=10,
        )


async def show_whats_new(app: Any) -> None:  # noqa: ANN401
    """Show a what's-new banner on the first launch after an upgrade."""
    try:
        from invincat_cli.update_check import should_show_whats_new

        if not await asyncio.to_thread(should_show_whats_new):
            return
    except Exception:
        logger.debug("What's new check failed", exc_info=True)
        return

    try:
        from invincat_cli.core.version import __version__ as cli_version

        await app._mount_message(
            AppMessage(f"Updated to v{cli_version}\nSee what's new: {CHANGELOG_URL}")
        )
    except Exception:
        logger.debug("What's new banner display failed", exc_info=True)
        return

    try:
        from invincat_cli.core.version import __version__ as cli_version
        from invincat_cli.update_check import mark_version_seen

        await asyncio.to_thread(mark_version_seen, cli_version)
    except Exception:
        logger.warning("Failed to persist seen-version marker", exc_info=True)


async def handle_update_command(app: Any) -> None:  # noqa: ANN401
    """Handle the `/update` slash command."""
    await app._mount_message(UserMessage("/update"))
    try:
        from invincat_cli.update_check import (
            is_update_available,
            perform_upgrade,
            upgrade_command,
        )

        await app._mount_message(AppMessage(t("update.checking")))
        available, latest = await asyncio.to_thread(
            is_update_available, bypass_cache=True
        )
        if not available:
            await app._mount_message(AppMessage(t("success.up_to_date")))
            return

        from invincat_cli.core.version import __version__ as cli_version

        await app._mount_message(
            AppMessage(
                t("app.update_available_upgrading").format(
                    latest=latest,
                    current=cli_version,
                )
            )
        )
        success, output = await perform_upgrade()
        if success:
            app._update_available = (False, None)
            await app._mount_message(
                AppMessage(t("app.updated_to").format(version=latest))
            )
        else:
            cmd = upgrade_command()
            detail = f": {output[:200]}" if output else ""
            await app._mount_message(
                AppMessage(
                    t("app.auto_update_failed_with_detail").format(
                        detail=detail,
                        command=cmd,
                    )
                )
            )
    except Exception as exc:
        logger.warning("/update command failed", exc_info=True)
        await app._mount_message(
            ErrorMessage(
                t("app.update_failed_with_error").format(
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        )


async def handle_auto_update_toggle(app: Any) -> None:  # noqa: ANN401
    """Handle the `/auto-update` slash command."""
    try:
        from invincat_cli.config import _is_editable_install
        from invincat_cli.update_check import (
            is_auto_update_enabled,
            set_auto_update,
        )

        if await asyncio.to_thread(_is_editable_install):
            app.notify(
                t("app.auto_update_not_available"),
                severity="warning",
                timeout=5,
            )
            return

        currently_enabled = await asyncio.to_thread(is_auto_update_enabled)
        new_state = not currently_enabled
        await asyncio.to_thread(set_auto_update, new_state)
        label = (
            t("app.auto_updates_enabled")
            if new_state
            else t("app.auto_updates_disabled")
        )
        app.notify(
            label,
            severity="information",
            timeout=5,
            markup=False,
        )
    except Exception as exc:
        logger.warning("/auto-update command failed", exc_info=True)
        app.notify(
            t(
                "app.auto_update_toggle_failed",
                error=f"{type(exc).__name__}: {exc}",
            ),
            severity="warning",
            timeout=5,
            markup=False,
        )
