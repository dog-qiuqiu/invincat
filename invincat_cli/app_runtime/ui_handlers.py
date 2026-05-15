"""App-bound modal and UI action handlers."""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

from textual.containers import VerticalScroll
from textual.css.query import NoMatches

from invincat_cli.app_runtime.startup import build_startup_slash_commands
from invincat_cli.app_runtime.state import DeferredAction
from invincat_cli.app_runtime.theme_prefs import save_theme_preference
from invincat_cli.app_runtime.ui_actions import (
    capture_chat_scroll_state,
    restore_chat_scroll_state,
    should_defer_modal_action,
)
from invincat_cli.app_runtime.ui_actions import (
    resolve_memory_store_paths as resolve_memory_store_paths_runtime,
)
from invincat_cli.i18n import t
from invincat_cli.widgets.status import StatusBar
from invincat_cli.widgets.welcome import WelcomeBanner

logger = logging.getLogger(__name__)


async def show_theme_selector(app: Any) -> None:  # noqa: ANN401
    """Show interactive theme selector as a modal screen."""
    from invincat_cli.widgets.theme_selector import ThemeSelectorScreen

    chat = app.query_one("#chat", VerticalScroll)
    scroll_snapshot = capture_chat_scroll_state(chat)

    def handle_result(result: str | None) -> None:
        if result is not None:
            app.theme = result
            app.refresh_css(animate=False)

            async def _persist() -> None:
                try:
                    ok = await asyncio.to_thread(save_theme_preference, result)
                    if not ok:
                        app.notify(
                            t("app.theme_not_saved"),
                            severity="warning",
                            timeout=6,
                            markup=False,
                        )
                except Exception:
                    logger.warning(
                        "Failed to persist theme preference",
                        exc_info=True,
                    )
                    app.notify(
                        t("app.theme_not_saved"),
                        severity="warning",
                        timeout=6,
                        markup=False,
                    )

            app.call_later(_persist)
        restore_chat_scroll_state(chat, scroll_snapshot)
        if app._chat_input:
            app._chat_input.focus_input()

    screen = ThemeSelectorScreen(current_theme=app.theme)
    app.push_screen(screen, handle_result)


async def show_language_selector(app: Any) -> None:  # noqa: ANN401
    """Show interactive language selector as a modal screen."""
    from invincat_cli.i18n import Language, get_i18n
    from invincat_cli.widgets.language_selector import LanguageSelectorScreen

    chat = app.query_one("#chat", VerticalScroll)
    scroll_snapshot = capture_chat_scroll_state(chat)

    def handle_result(result: Language | None) -> None:
        if result is not None:
            i18n = get_i18n()
            lang_name = i18n.get_language_name(result)
            app.notify(
                t("app.language_changed_to", language=lang_name),
                severity="information",
                timeout=3,
            )
            app._refresh_all_ui_text()
        restore_chat_scroll_state(chat, scroll_snapshot)
        if app._chat_input:
            app._chat_input.focus_input()

    i18n = get_i18n()
    screen = LanguageSelectorScreen(current_language=i18n.language)
    app.push_screen(screen, handle_result)


def refresh_all_ui_text(app: Any) -> None:  # noqa: ANN401
    """Refresh all UI text to reflect language change."""
    from invincat_cli.commands.registry import COMMANDS, build_skill_commands

    try:
        banner = app.query_one("#welcome-banner", WelcomeBanner)
        banner.update(banner._build_banner(banner._project_url))
    except NoMatches:
        pass

    try:
        status_bar = app.query_one(StatusBar)
        status_bar.refresh()
    except NoMatches:
        pass

    try:
        if app._chat_input:
            app._chat_input.update_slash_commands(
                build_startup_slash_commands(
                    commands=COMMANDS,
                    discovered_skills=app._discovered_skills,
                    build_skill_commands=build_skill_commands,
                )
            )
    except Exception:
        pass


async def show_mcp_viewer(app: Any) -> None:  # noqa: ANN401
    """Show read-only MCP server/tool viewer as a modal screen."""
    from invincat_cli.widgets.mcp_viewer import MCPViewerScreen

    screen = MCPViewerScreen(server_info=app._mcp_server_info or [])

    def handle_result(result: None) -> None:  # noqa: ARG001
        if app._chat_input:
            app._chat_input.focus_input()

    app.push_screen(screen, handle_result)


def resolve_memory_store_paths(app: Any) -> dict[str, str]:  # noqa: ANN401
    """Resolve user/project memory store paths for the current session."""
    from invincat_cli.config import settings

    return resolve_memory_store_paths_runtime(
        cwd=app._cwd,
        assistant_id=app._assistant_id,
        get_agent_dir=settings.get_agent_dir,
    )


async def show_memory_viewer(app: Any) -> None:  # noqa: ANN401
    """Show memory manager modal with live store state."""
    from invincat_cli.widgets.memory_viewer import MemoryViewerScreen

    screen = MemoryViewerScreen(
        memory_store_paths=app._resolve_memory_store_paths(),
    )

    def handle_result(result: None) -> None:  # noqa: ARG001
        if app._chat_input:
            app._chat_input.focus_input()

    app.push_screen(screen, handle_result)


async def show_thread_selector(app: Any) -> None:  # noqa: ANN401
    """Show interactive thread selector as a modal screen."""
    from invincat_cli.sessions import get_cached_threads, get_thread_limit
    from invincat_cli.widgets.thread_selector import ThreadSelectorScreen

    current = app._session_state.thread_id if app._session_state else None
    thread_limit = get_thread_limit()

    initial_threads = get_cached_threads(
        limit=thread_limit,
        require_message_counts=True,
    )

    def handle_result(result: str | None) -> None:
        if result is not None:
            if should_defer_modal_action(
                agent_running=app._agent_running,
                shell_running=app._shell_running,
                connecting=app._connecting,
            ):
                app._defer_action(
                    DeferredAction(
                        kind="thread_switch",
                        execute=partial(app._resume_thread, result),
                    )
                )
                app.notify(t("app.thread_switch_pending"), timeout=3)
            else:
                app.call_later(app._resume_thread, result)
        if app._chat_input:
            app._chat_input.focus_input()

    screen = ThreadSelectorScreen(
        current_thread=current,
        thread_limit=thread_limit,
        initial_threads=initial_threads,
    )
    app.push_screen(screen, handle_result)


def update_welcome_banner(
    app: Any,  # noqa: ANN401
    thread_id: str,
    *,
    missing_message: str,
    warn_if_missing: bool,
) -> None:
    """Update the welcome banner thread ID when the banner is mounted."""
    try:
        banner = app.query_one("#welcome-banner", WelcomeBanner)
        banner.update_thread_id(thread_id)
    except NoMatches:
        if warn_if_missing:
            logger.warning(missing_message, thread_id)
        else:
            logger.debug(missing_message, thread_id)
