"""Delegated Textual app methods kept off the main app class module."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from textual.containers import Container, VerticalScroll
from textual.css.query import NoMatches

from invincat_cli.core.session_stats import SpinnerStatus
from invincat_cli.i18n import t

if TYPE_CHECKING:
    from typing import Any

    from textual.app import ComposeResult
    from textual.scrollbar import ScrollTo, ScrollUp
    from textual.widget import Widget

    from invincat_cli.remote.client import RemoteAgent
    from invincat_cli.skills.load import ExtendedSkillMetadata

logger = logging.getLogger(__name__)


class AppRuntimeDelegateMixin:
    """Lifecycle, status, and message-flow delegators for ``DeepAgentsApp``."""

    if TYPE_CHECKING:
        _agent: Any
        _auto_approve: bool
        _context_tokens: int
        _lc_thread_id: str | None
        _status_bar: Any | None
        _tokens_approximate: bool

        def notify(
            self,
            message: object,
            *,
            severity: str = "information",
            timeout: float | None = None,
            markup: bool = True,
        ) -> None: ...

        def query_one(self, *args: object, **kwargs: object) -> Any: ...

    def _remote_agent(self) -> RemoteAgent | None:
        from invincat_cli.remote.client import RemoteAgent

        return self._agent if isinstance(self._agent, RemoteAgent) else None

    def get_theme_variable_defaults(self) -> dict[str, str]:
        from invincat_cli.app_runtime.layout import get_theme_variable_defaults

        return get_theme_variable_defaults(self)

    def compose(self) -> ComposeResult:
        from invincat_cli.app_runtime.layout import compose_layout

        yield from compose_layout(self)

    async def on_mount(self) -> None:
        from invincat_cli.app_runtime.startup_handlers import handle_mount

        await handle_mount(self)

    async def _resolve_git_branch_and_continue(self) -> None:
        from invincat_cli.app_runtime.startup_handlers import (
            resolve_git_branch_and_continue,
        )

        await resolve_git_branch_and_continue(self)

    async def _post_paint_init(self) -> None:
        from invincat_cli.app_runtime.startup_handlers import post_paint_init

        await post_paint_init(self)

    async def _init_session_state(self) -> None:
        try:
            from invincat_cli import app as app_module

            self._session_state = await asyncio.to_thread(
                app_module.create_startup_session_state,
                auto_approve=self._auto_approve,
                thread_id=self._lc_thread_id,
            )
            from invincat_cli.app_runtime.goal_handlers import restore_goal_state

            await restore_goal_state(self)
        except Exception:
            logger.exception("Failed to create session state")
            self.notify(t("app.session_init_failed"), severity="error", timeout=10)

    async def _check_optional_tools_background(self) -> None:
        from invincat_cli.app_runtime.startup_handlers import (
            check_optional_tools_background,
        )

        await check_optional_tools_background(self)

    async def _discover_skills(self) -> None:
        from invincat_cli.app_runtime.startup_handlers import discover_skills

        await discover_skills(self)

    def _discover_skills_and_roots(
        self,
    ) -> tuple[list[ExtendedSkillMetadata], list[Path]]:
        from invincat_cli.app_runtime.startup_handlers import discover_skills_and_roots

        return discover_skills_and_roots(self)

    async def _resolve_resume_thread(self) -> None:
        from invincat_cli.app_runtime.server_handlers import resolve_resume_thread

        await resolve_resume_thread(self)

    async def _start_server_background(self) -> None:
        from invincat_cli.app_runtime.server_handlers import start_server_background

        await start_server_background(self)

    def on_deep_agents_app_server_ready(self, event) -> None:  # noqa: ANN001
        self.on_server_ready(event)

    def on_server_ready(self, event) -> None:  # noqa: ANN001
        from invincat_cli.app_runtime.server_handlers import handle_server_ready

        handle_server_ready(self, event)

    def on_deep_agents_app_server_start_failed(self, event) -> None:  # noqa: ANN001
        self.on_server_start_failed(event)

    def on_server_start_failed(self, event) -> None:  # noqa: ANN001
        from invincat_cli.app_runtime.server_handlers import handle_server_start_failed

        handle_server_start_failed(self, event)

    @staticmethod
    def _prewarm_deferred_imports() -> None:
        from invincat_cli.app_runtime.startup_handlers import prewarm_deferred_imports

        prewarm_deferred_imports()

    async def _prewarm_threads_cache(self) -> None:
        from invincat_cli.app_runtime.startup_handlers import prewarm_threads_cache

        await prewarm_threads_cache()

    async def _prewarm_model_caches(self) -> None:
        from invincat_cli.app_runtime.startup_handlers import prewarm_model_caches

        await prewarm_model_caches(self)

    async def _check_for_updates(self) -> None:
        from invincat_cli.app_runtime.update_handlers import check_for_updates

        await check_for_updates(self)

    async def _show_whats_new(self) -> None:
        from invincat_cli.app_runtime.update_handlers import show_whats_new

        await show_whats_new(self)

    async def _handle_update_command(self) -> None:
        from invincat_cli.app_runtime.update_handlers import handle_update_command

        await handle_update_command(self)

    async def _handle_auto_update_toggle(self) -> None:
        from invincat_cli.app_runtime.update_handlers import handle_auto_update_toggle

        await handle_auto_update_toggle(self)

    def on_scroll_up(self, _event: ScrollUp) -> None:
        self._check_hydration_needed()

    def on_scroll_to(self, _event: ScrollTo) -> None:
        self._check_hydration_needed()
        self._maybe_reanchor()

    def _update_status(self, message: str) -> None:
        if self._status_bar:
            self._status_bar.set_status_message(message)

    def _update_tokens(self, count: int, *, approximate: bool = False) -> None:
        if self._status_bar:
            self._status_bar.set_tokens(count, approximate=approximate)

    def _on_tokens_update(self, count: int, *, approximate: bool = False) -> None:
        self._context_tokens = count
        self._tokens_approximate = approximate
        self._update_tokens(count, approximate=approximate)

    def _show_tokens(self, *, approximate: bool = False) -> None:
        self._tokens_approximate = self._tokens_approximate or approximate
        self._update_tokens(
            self._context_tokens,
            approximate=self._tokens_approximate,
        )

    def _hide_tokens(self) -> None:
        if self._status_bar:
            self._status_bar.hide_tokens()

    def _maybe_reanchor(self) -> None:
        try:
            chat = self.query_one("#chat", VerticalScroll)
        except NoMatches:
            return
        if not chat.is_anchored and chat.max_scroll_y > 0:
            if chat.scroll_y >= chat.max_scroll_y - 2:
                chat.anchor()

    def _check_hydration_needed(self) -> None:
        from invincat_cli.app_runtime.message_flow import check_hydration_needed

        check_hydration_needed(self)

    async def _hydrate_messages_above(self) -> None:
        from invincat_cli.app_runtime.message_flow import hydrate_messages_above

        await hydrate_messages_above(self)

    async def _mount_before_queued(self, container: Container, widget: Widget) -> None:
        from invincat_cli.app_runtime.message_flow import mount_before_queued

        await mount_before_queued(self, container, widget)

    def _is_spinner_at_correct_position(self, container: Container) -> bool:
        from invincat_cli.app_runtime.message_flow import is_spinner_at_correct_position

        return is_spinner_at_correct_position(self, container)

    async def _set_spinner(self, status: SpinnerStatus) -> None:
        from invincat_cli.app_runtime.message_flow import set_spinner

        await set_spinner(self, status)
