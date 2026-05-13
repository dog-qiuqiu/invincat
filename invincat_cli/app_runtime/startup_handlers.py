"""App-bound startup background handlers."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from invincat_cli.app_runtime.skill import discover_skills_and_roots as discover_roots
from invincat_cli.i18n import t
from invincat_cli.skills.load import ExtendedSkillMetadata

logger = logging.getLogger(__name__)


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
    from invincat_cli.command_registry import SLASH_COMMANDS, build_skill_commands

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
    from invincat_cli.io.clipboard import copy_selection_to_clipboard  # noqa: F401
    from invincat_cli.command_registry import ALWAYS_IMMEDIATE  # noqa: F401
    from invincat_cli.config import settings  # noqa: F401
    from invincat_cli.hooks import dispatch_hook  # noqa: F401
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
    from pygments.lexers import get_lexer_by_name as _get_lexer  # type: ignore[import-untyped]
    from textual.widgets import Markdown  # noqa: F401

    _get_lexer("python")

    from invincat_cli.widgets.approval import ApprovalMenu  # noqa: F401
    from invincat_cli.widgets.ask_user import AskUserMenu  # noqa: F401
    from invincat_cli.widgets.model_selector import ModelSelectorScreen  # noqa: F401
    from invincat_cli.widgets.memory_viewer import MemoryViewerScreen  # noqa: F401
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
        await asyncio.to_thread(
            get_model_profiles, cli_override=app._profile_override
        )
    except Exception:
        logger.warning("Could not prewarm model caches", exc_info=True)
