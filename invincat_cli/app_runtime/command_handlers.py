"""Slash-command execution helpers for the Textual app."""

from __future__ import annotations

import asyncio
import logging
import webbrowser
from contextlib import suppress
from typing import Any

from textual.app import ScreenStackError
from textual.containers import VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.style import Style as TStyle

from invincat_cli import theme
from invincat_cli.app_runtime.command import route_slash_command
from invincat_cli.app_runtime.model_command import (
    MODEL_DEFAULT_USAGE,
    parse_model_command,
)
from invincat_cli.app_runtime.reload import build_reload_report
from invincat_cli.app_runtime.state import DeferredAction
from invincat_cli.app_runtime.tokens import build_tokens_message
from invincat_cli.app_runtime.version import resolve_version_message
from invincat_cli.core.version import CHANGELOG_URL, DOCS_URL
from invincat_cli.widgets.messages import (
    AppMessage,
    ErrorMessage,
    QueuedUserMessage,
    UserMessage,
)
from invincat_cli.widgets.welcome import WelcomeBanner

logger = logging.getLogger(__name__)

COMMAND_URLS: dict[str, str] = {
    "/changelog": CHANGELOG_URL,
    "/docs": DOCS_URL,
    "/feedback": "https://github.com/langchain-ai/deepagents/issues/new/choose",
}


async def handle_app_command(app: Any, command: str) -> None:  # noqa: ANN401
    """Handle a slash command against a Textual app instance."""
    from invincat_cli.i18n import t

    route = route_slash_command(command)
    cmd = route.normalized

    if route.kind == "quit":
        app.exit()
    elif route.kind == "help":
        await app._mount_message(UserMessage(command))
        from invincat_cli.app_runtime.help import build_help_content

        await app._mount_message(AppMessage(build_help_content()))
    elif route.kind == "url":
        await handle_url_command(app, command, cmd)
    elif route.kind == "version":
        await app._mount_message(UserMessage(command))
        await app._mount_message(AppMessage(resolve_version_message()))
    elif route.kind == "clear":
        await handle_clear_command(app)
    elif route.kind == "editor":
        await app.action_open_editor()
    elif route.kind == "offload":
        await app._mount_message(UserMessage(command))
        await app._handle_offload()
    elif route.kind == "plan":
        await app._handle_plan_task()
    elif route.kind == "exit_plan":
        await app._exit_plan_mode()
    elif route.kind == "threads":
        await app._show_thread_selector()
    elif route.kind == "trace":
        await handle_trace_command(app, command)
    elif route.kind == "update":
        await app._handle_update_command()
    elif route.kind == "auto_update":
        await app._handle_auto_update_toggle()
    elif route.kind == "tokens":
        await handle_tokens_command(app, command)
    elif route.kind == "skill_creator" and route.rewritten_command:
        await app._handle_skill_command(route.rewritten_command)
    elif route.kind == "mcp":
        await app._show_mcp_viewer()
    elif route.kind == "memory":
        await app._show_memory_viewer()
    elif route.kind == "wecom" and route.wecom_action:
        await app._handle_wecombot_command(command, action=route.wecom_action)
    elif route.kind == "schedule":
        await app._handle_schedule_command(command)
    elif route.kind == "theme":
        await app._show_theme_selector()
    elif route.kind == "language":
        await app._show_language_selector()
    elif route.kind == "model":
        await handle_model_command(app, command)
    elif route.kind == "reload":
        await handle_reload_command(app, command)
    elif route.kind == "skill":
        await app._handle_skill_command(command)
    else:
        await app._mount_message(UserMessage(command))
        await app._mount_message(AppMessage(t("command.unknown").format(command=cmd)))

    with suppress(NoMatches, ScreenStackError):
        app.query_one("#chat", VerticalScroll).anchor()


async def handle_clear_command(app: Any) -> None:  # noqa: ANN401
    """Clear chat state and start a new thread."""
    from invincat_cli.i18n import t

    app._pending_messages.clear()
    app._queued_widgets.clear()
    await app._clear_messages()
    app._context_tokens = 0
    app._tokens_approximate = False
    app._update_tokens(0)
    app._update_status("")
    if app._session_state:
        new_thread_id = app._session_state.reset_thread()
        try:
            banner = app.query_one("#welcome-banner", WelcomeBanner)
            banner.update_thread_id(new_thread_id)
        except NoMatches:
            pass
        await app._mount_message(
            AppMessage(t("success.new_thread").format(thread_id=new_thread_id))
        )


async def handle_tokens_command(app: Any, command: str) -> None:  # noqa: ANN401
    """Show current context token usage."""
    from invincat_cli.config import settings

    await app._mount_message(UserMessage(command))
    conversation_tokens = (
        await app._get_conversation_token_count() if app._context_tokens > 0 else None
    )
    await app._mount_message(
        AppMessage(
            build_tokens_message(
                context_tokens=app._context_tokens,
                model_name=settings.model_name or "",
                context_limit=settings.model_context_limit,
                conversation_tokens=conversation_tokens,
            )
        )
    )


async def _mount_url_output(app: Any, command: str, url: str) -> None:  # noqa: ANN401
    await app._mount_message(UserMessage(command))
    link = Content.styled(url, TStyle(dim=True, italic=True, link=url))
    await app._mount_message(AppMessage(link))


def _open_browser(url: str) -> None:
    """Best-effort browser launch for URL commands."""
    try:
        webbrowser.open(url)
    except Exception:
        logger.debug("Could not open browser for URL: %s", url, exc_info=True)


async def _defer_url_output(app: Any, command: str, url: str) -> None:  # noqa: ANN401
    queued_widget = QueuedUserMessage(command)
    app._queued_widgets.append(queued_widget)
    await app._mount_message(queued_widget)

    async def _mount_output() -> None:
        if queued_widget in app._queued_widgets:
            app._queued_widgets.remove(queued_widget)
        with suppress(Exception):
            await queued_widget.remove()
        await _mount_url_output(app, command, url)

    app._deferred_actions.append(
        DeferredAction(kind="chat_output", execute=_mount_output)
    )


async def handle_url_command(app: Any, command: str, cmd: str) -> None:  # noqa: ANN401
    """Open a static URL command and render a clickable chat link."""
    url = COMMAND_URLS[cmd]
    _open_browser(url)

    if app._agent_running or app._shell_running:
        await _defer_url_output(app, command, url)
        return

    await _mount_url_output(app, command, url)


async def handle_trace_command(app: Any, command: str) -> None:  # noqa: ANN401
    """Open the current thread in LangSmith."""
    from invincat_cli.config import build_langsmith_thread_url
    from invincat_cli.i18n import t

    if not app._session_state:
        await app._mount_message(UserMessage(command))
        await app._mount_message(AppMessage(t("trace.no_active_session")))
        return
    thread_id = app._session_state.thread_id
    try:
        url = await asyncio.to_thread(build_langsmith_thread_url, thread_id)
    except Exception:
        logger.exception("Failed to build LangSmith thread URL for %s", thread_id)
        await app._mount_message(UserMessage(command))
        await app._mount_message(AppMessage(t("trace.resolve_failed")))
        return
    if not url:
        await app._mount_message(UserMessage(command))
        await app._mount_message(AppMessage(t("trace.not_configured")))
        return

    asyncio.get_running_loop().run_in_executor(None, _open_browser, url)

    if app._agent_running or app._shell_running:
        await _defer_url_output(app, command, url)
        return

    await _mount_url_output(app, command, url)


async def handle_model_command(app: Any, command: str) -> None:  # noqa: ANN401
    """Parse and execute a `/model` command."""
    action = parse_model_command(command)
    if action.kind == "error":
        await app._mount_message(UserMessage(command))
        await app._mount_message(ErrorMessage(action.error or ""))
        return
    if action.kind == "usage":
        await app._mount_message(UserMessage(command))
        await app._mount_message(AppMessage(MODEL_DEFAULT_USAGE))
    elif action.kind == "clear_default":
        await app._mount_message(UserMessage(command))
        await app._clear_default_model(target=action.target)
    elif action.kind == "set_default" and action.model_arg:
        await app._mount_message(UserMessage(command))
        await app._set_default_model(
            action.model_arg,
            target=action.target,
            apply_to_session=(action.target == "memory"),
        )
    elif action.kind == "switch" and action.model_arg:
        await app._mount_message(UserMessage(command))
        await app._switch_model(
            action.model_arg,
            target=action.target,
            extra_kwargs=action.extra_kwargs,
        )
    else:
        await app._show_model_selector(
            target=action.target,
            extra_kwargs=action.extra_kwargs,
        )


async def handle_reload_command(app: Any, command: str) -> None:  # noqa: ANN401
    """Reload config, model caches, themes, and skill discovery."""
    from invincat_cli.config import settings

    await app._mount_message(UserMessage(command))
    try:
        changes = settings.reload_from_environment()

        from invincat_cli.model_config import clear_caches

        clear_caches()
    except (OSError, ValueError):
        logger.exception("Failed to reload configuration")
        await app._mount_message(
            AppMessage(
                "Failed to reload configuration. Check your .env "
                "file and environment variables for syntax errors, "
                "then try again."
            )
        )
        return

    theme_reload_ok = True
    try:
        theme.reload_registry()
        app._register_custom_themes()
    except Exception:
        theme_reload_ok = False
        logger.warning("Failed to reload user themes", exc_info=True)

    await app._mount_message(
        AppMessage(
            build_reload_report(
                changes,
                theme_reload_ok=theme_reload_ok,
            )
        )
    )

    app.run_worker(
        app._discover_skills(),
        exclusive=True,
        group="startup-skill-discovery",
    )
