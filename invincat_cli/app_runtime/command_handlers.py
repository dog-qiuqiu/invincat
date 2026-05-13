"""Slash-command execution helpers for the Textual app."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

from textual.app import ScreenStackError
from textual.containers import VerticalScroll
from textual.css.query import NoMatches

from invincat_cli import theme
from invincat_cli.app_runtime.command import route_slash_command
from invincat_cli.app_runtime.model_command import (
    MODEL_DEFAULT_USAGE,
    parse_model_command,
)
from invincat_cli.app_runtime.reload import build_reload_report
from invincat_cli.app_runtime.tokens import build_tokens_message
from invincat_cli.app_runtime.version import resolve_version_message
from invincat_cli.widgets.messages import AppMessage, ErrorMessage, UserMessage
from invincat_cli.widgets.welcome import WelcomeBanner

logger = logging.getLogger(__name__)


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
        await app._open_url_command(command, cmd)
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
        await app._handle_trace_command(command)
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
