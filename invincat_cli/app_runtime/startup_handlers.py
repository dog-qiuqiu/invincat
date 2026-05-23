"""App-bound startup background handlers."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal, cast

from textual.containers import VerticalScroll

from invincat_cli.app_runtime.input_handlers import handle_user_message
from invincat_cli.app_runtime.model_args import split_model_spec
from invincat_cli.app_runtime.skill import discover_skills_and_roots as discover_roots
from invincat_cli.app_runtime.startup import (
    build_startup_slash_commands,
    resolve_memory_status_model,
    resolve_startup_followup,
    resolve_startup_model_overrides,
)
from invincat_cli.app_runtime.thread_handlers import load_thread_history
from invincat_cli.config import is_ascii_mode
from invincat_cli.i18n import t
from invincat_cli.skills.load import ExtendedSkillMetadata
from invincat_cli.widgets.chat_input import ChatInput
from invincat_cli.widgets.status import StatusBar

logger = logging.getLogger(__name__)


async def handle_mount(app: Any) -> None:  # noqa: ANN401
    """Initialize components after Textual mount."""
    import gc

    gc.freeze()

    chat = app.query_one("#chat", VerticalScroll)
    chat.anchor()
    if is_ascii_mode():
        chat.styles.scrollbar_size_vertical = 0

    from invincat_cli.config import _get_default_memory_model_spec, settings
    from invincat_cli.model_config import get_target_model_params

    def _get_target_model_params(
        target: str,
        model_spec: str,
    ) -> dict[str, Any]:
        return get_target_model_params(
            cast(Literal["primary", "memory"], target),
            model_spec,
        )

    startup_overrides = resolve_startup_model_overrides(
        memory_model_override=app._memory_model_override,
        memory_model_params_override=app._memory_model_params_override,
        model_params_override=app._model_params_override,
        model_provider=settings.model_provider,
        model_name=settings.model_name,
        get_default_memory_model_spec=_get_default_memory_model_spec,
        get_target_model_params=_get_target_model_params,
    )
    app._memory_model_override = startup_overrides.memory_model
    app._model_params_override = startup_overrides.primary_params
    app._memory_model_params_override = startup_overrides.memory_params

    app._status_bar = app.query_one("#status-bar", StatusBar)
    app._chat_input = app.query_one("#input-area", ChatInput)
    if app._status_bar:
        memory_status_model = resolve_memory_status_model(
            memory_model_override=app._memory_model_override,
            model_provider=settings.model_provider,
            model_name=settings.model_name,
            split_model_spec=split_model_spec,
        )
        app._status_bar.set_memory_model(
            provider=memory_status_model.provider,
            model=memory_status_model.model,
            follow_primary=memory_status_model.follow_primary,
        )

    from invincat_cli.commands.registry import COMMANDS, build_skill_commands

    app._chat_input.update_slash_commands(
        build_startup_slash_commands(
            commands=COMMANDS,
            discovered_skills=app._discovered_skills,
            build_skill_commands=build_skill_commands,
        )
    )

    if app._auto_approve:
        app._status_bar.set_auto_approve(enabled=True)

    app._chat_input.focus_input()

    app.run_worker(
        asyncio.to_thread(app._prewarm_deferred_imports),
        exclusive=True,
        group="startup-import-prewarm",
    )

    app._startup_task = asyncio.create_task(app._resolve_git_branch_and_continue())


async def resolve_git_branch_and_continue(app: Any) -> None:  # noqa: ANN401
    """Resolve git branch, then schedule remaining init workers."""
    try:
        import subprocess  # noqa: S404  # stdlib invocation for local git metadata

        def _get_branch() -> str:
            try:
                result = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],  # noqa: S607
                    capture_output=True,
                    text=True,
                    timeout=2,
                    check=False,
                )
                if result.returncode == 0:
                    return result.stdout.strip()
            except FileNotFoundError:
                pass
            except subprocess.TimeoutExpired:
                logger.debug("Git branch detection timed out")
            except OSError:
                logger.debug("Git branch detection failed", exc_info=True)
            return ""

        branch = await asyncio.to_thread(_get_branch)
        if app._status_bar:
            app._status_bar.branch = branch
    except Exception:
        logger.warning("Git branch resolution failed", exc_info=True)
    finally:
        app.call_after_refresh(app._post_paint_init)


async def post_paint_init(app: Any) -> None:  # noqa: ANN401
    """Fire background workers for remaining startup work."""
    from invincat_cli.textual_adapter import TextualUIAdapter

    app._ui_adapter = TextualUIAdapter(
        mount_message=app._mount_message,
        update_status=app._update_status,
        request_approval=app._request_approval,
        on_auto_approve_enabled=app._on_auto_approve_enabled,
        set_spinner=app._set_spinner,
        set_active_message=app._set_active_message,
        sync_message_content=app._sync_message_content,
        request_ask_user=app._request_ask_user,
        request_approve_plan=app._request_approve_plan,
        on_execute_watchdog_timeout=app._handle_execute_watchdog_timeout,
    )
    app._ui_adapter._on_tokens_update = app._on_tokens_update
    app._ui_adapter._on_tokens_hide = app._hide_tokens
    app._ui_adapter._on_tokens_show = app._show_tokens
    app._ui_adapter.set_message_store(app._message_store)

    app.run_worker(
        app._discover_skills(),
        exclusive=True,
        group="startup-skill-discovery",
    )

    app.run_worker(app._init_session_state, exclusive=True, group="session-init")

    if app._server_kwargs is not None and not app._defer_server_start:
        app.run_worker(
            app._start_server_background,
            exclusive=True,
            group="server-startup",
        )

    app.run_worker(
        app._prewarm_model_caches,
        exclusive=True,
        group="startup-model-prewarm",
    )

    app.run_worker(
        app._prewarm_threads_cache,
        exclusive=True,
        group="startup-thread-prewarm",
    )

    app.run_worker(
        app._check_optional_tools_background,
        exclusive=True,
        group="startup-tool-check",
    )

    app._start_scheduler()

    followup = resolve_startup_followup(
        connecting=app._connecting,
        initial_prompt=app._initial_prompt,
        thread_id=app._lc_thread_id,
        agent=app._agent,
    )
    if followup and followup.kind == "submit_prompt" and followup.prompt is not None:
        app.call_after_refresh(
            lambda: asyncio.create_task(handle_user_message(app, followup.prompt))
        )
    elif followup and followup.kind == "load_history":
        app.call_after_refresh(lambda: asyncio.create_task(load_thread_history(app)))


async def check_optional_tools_background(app: Any) -> None:  # noqa: ANN401
    """Check for optional tools in a thread and notify if missing."""
    try:
        from invincat_cli.main import (
            check_optional_tools,
            format_tool_warning_tui,
        )
    except ImportError:
        logger.warning(
            "Could not import optional tools checker",
            exc_info=True,
        )
        return

    try:
        missing = await asyncio.to_thread(check_optional_tools)
    except (OSError, FileNotFoundError):
        logger.debug("Failed to check for optional tools", exc_info=True)
        return
    except Exception:
        logger.warning("Unexpected error checking optional tools", exc_info=True)
        return

    for tool in missing:
        app.notify(
            format_tool_warning_tui(tool),
            severity="warning",
            timeout=15,
            markup=False,
        )


async def discover_skills(app: Any) -> None:  # noqa: ANN401
    """Discover skills, cache metadata, and update autocomplete."""
    from invincat_cli.commands.registry import SLASH_COMMANDS, build_skill_commands

    try:
        skills, roots = await asyncio.to_thread(app._discover_skills_and_roots)
        app._discovered_skills = skills
        app._skill_allowed_roots = roots
    except OSError:
        app._discovered_skills = []
        app._skill_allowed_roots = []
        logger.warning(
            "Filesystem error during skill discovery",
            exc_info=True,
        )
        app.notify(
            t("app.skill_scan_failed"),
            severity="warning",
            timeout=6,
            markup=False,
        )
    except Exception:
        app._discovered_skills = []
        app._skill_allowed_roots = []
        logger.exception("Unexpected error during skill discovery")
        app.notify(
            t("app.skill_discovery_failed"),
            severity="warning",
            timeout=8,
            markup=False,
        )
    if app._chat_input:
        skill_commands = build_skill_commands(app._discovered_skills)
        merged = list(SLASH_COMMANDS) + skill_commands
        app._chat_input.update_slash_commands(merged)
    else:
        logger.debug(
            "Skill discovery completed (%d skills) but chat input "
            "not yet mounted; autocomplete deferred",
            len(app._discovered_skills),
        )


def discover_skills_and_roots(
    app: Any,  # noqa: ANN401
) -> tuple[list[ExtendedSkillMetadata], list[Path]]:
    """Discover skills and build pre-resolved containment roots."""
    from invincat_cli.config import settings

    assistant_id = app._assistant_id or "agent"
    return discover_roots(settings=settings, assistant_id=assistant_id)


def prewarm_deferred_imports() -> None:
    """Background-load modules deferred from the startup path."""
    from invincat_cli.commands.registry import ALWAYS_IMMEDIATE  # noqa: F401
    from invincat_cli.config import settings  # noqa: F401
    from invincat_cli.hooks import dispatch_hook  # noqa: F401
    from invincat_cli.io.clipboard import copy_selection_to_clipboard  # noqa: F401
    from invincat_cli.model_config import ModelSpec  # noqa: F401
    from invincat_cli.textual_adapter import TextualUIAdapter  # noqa: F401
    from invincat_cli.update_check import is_update_check_enabled  # noqa: F401

    try:
        from deepagents.backends import DEFAULT_EXECUTE_TIMEOUT  # noqa: F401
        from langchain.agents.middleware.human_in_the_loop import (  # noqa: F401
            ApproveDecision,
        )
        from langchain_core.messages import AIMessage  # noqa: F401
        from langgraph.types import Command  # noqa: F401
    except Exception:
        logger.warning("Could not prewarm third-party imports", exc_info=True)

    import markdown_it  # noqa: F401
    from pygments.lexers import (  # type: ignore[import-untyped]
        get_lexer_by_name as _get_lexer,
    )
    from textual.widgets import Markdown  # noqa: F401

    _get_lexer("python")

    from invincat_cli.widgets.approval import ApprovalMenu  # noqa: F401
    from invincat_cli.widgets.ask_user import AskUserMenu  # noqa: F401
    from invincat_cli.widgets.memory_viewer import MemoryViewerScreen  # noqa: F401
    from invincat_cli.widgets.model_selector import ModelSelectorScreen  # noqa: F401
    from invincat_cli.widgets.thread_selector import (  # noqa: F401
        DeleteThreadConfirmScreen,
        ThreadSelectorScreen,
    )


async def prewarm_threads_cache() -> None:
    """Prewarm thread selector cache without blocking app startup."""
    from invincat_cli.sessions import (
        get_thread_limit,
        prewarm_thread_message_counts,
    )

    await prewarm_thread_message_counts(limit=get_thread_limit())


async def prewarm_model_caches(app: Any) -> None:  # noqa: ANN401
    """Prewarm model discovery and profile caches without blocking startup."""
    try:
        from invincat_cli.model_config import (
            get_available_models,
            get_model_profiles,
        )

        await asyncio.to_thread(get_available_models)
        await asyncio.to_thread(get_model_profiles, cli_override=app._profile_override)
    except Exception:
        logger.warning("Could not prewarm model caches", exc_info=True)
