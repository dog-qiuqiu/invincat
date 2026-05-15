"""Interaction-level methods mixed into the main Textual app."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.screen import ModalScreen

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from textual.events import Click, MouseUp, Paste

    from invincat_cli.app_runtime.model_runtime import ResolvedModelSpec
    from invincat_cli.app_runtime.thread_runtime import ThreadSwitchSnapshot
    from invincat_cli.model_config import ModelTarget
    from invincat_cli.remote.client import RemoteAgent


class AppInputEventMixin:
    """Prompt input, focus, paste, and mouse event hooks for the app."""

    async def action_open_editor(self) -> None:
        """Open the current prompt text in an external editor ($VISUAL/$EDITOR)."""
        from invincat_cli.app_runtime.action_handlers import open_editor

        await open_editor(self)

    def on_paste(self, event: Paste) -> None:
        """Route unfocused paste events to chat input for drag/drop reliability."""
        if not self._chat_input:
            return
        if (
            self._pending_approval_widget
            or self._pending_ask_user_widget
            or self._is_input_focused()
        ):
            return
        if self._chat_input.handle_external_paste(event.text):
            event.prevent_default()
            event.stop()

    def on_app_focus(self) -> None:
        """Restore chat input focus when the terminal regains OS focus."""
        if not self._chat_input:
            return
        from invincat_cli import app as app_module

        modal_screen_type = getattr(app_module, "ModalScreen", ModalScreen)
        if isinstance(self.screen, modal_screen_type):
            return
        if self._pending_approval_widget or self._pending_ask_user_widget:
            return
        self._chat_input.focus_input()

    def on_click(self, _event: Click) -> None:
        """Handle clicks anywhere in the terminal to focus on the command line."""
        if not self._chat_input:
            return
        if self._pending_approval_widget or self._pending_ask_user_widget:
            return
        self.call_after_refresh(self._chat_input.focus_input)

    def on_mouse_up(self, event: MouseUp) -> None:  # noqa: ARG002
        """Copy selection to clipboard on mouse release."""
        from invincat_cli.io.clipboard import copy_selection_to_clipboard

        copy_selection_to_clipboard(self)


class AppSelectionMixin:
    """Selector and switch delegates for models, UI settings, and threads."""

    async def _show_model_selector(
        self,
        *,
        target: ModelTarget = "primary",
        extra_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Show interactive model selector as a modal screen."""
        from invincat_cli.app_runtime.model_handlers import show_model_selector

        await show_model_selector(
            self,
            target=target,
            extra_kwargs=extra_kwargs,
        )

    def _register_custom_themes(self) -> None:
        """Register all custom themes (built-in LC + user-defined) with Textual."""
        from invincat_cli.app_runtime.layout import register_custom_themes

        register_custom_themes(self)

    async def _show_theme_selector(self) -> None:
        """Show interactive theme selector as a modal screen."""
        from invincat_cli.app_runtime.ui_handlers import show_theme_selector

        await show_theme_selector(self)

    async def _show_language_selector(self) -> None:
        """Show interactive language selector as a modal screen."""
        from invincat_cli.app_runtime.ui_handlers import show_language_selector

        await show_language_selector(self)

    def _refresh_all_ui_text(self) -> None:
        """Refresh all UI text to reflect language change."""
        from invincat_cli.app_runtime.ui_handlers import refresh_all_ui_text

        refresh_all_ui_text(self)

    async def _show_mcp_viewer(self) -> None:
        """Show read-only MCP server/tool viewer as a modal screen."""
        from invincat_cli.app_runtime.ui_handlers import show_mcp_viewer

        await show_mcp_viewer(self)

    def _resolve_memory_store_paths(self) -> dict[str, str]:
        """Resolve user/project memory store paths for the current session."""
        from invincat_cli.app_runtime.ui_handlers import resolve_memory_store_paths

        return resolve_memory_store_paths(self)

    async def _show_memory_viewer(self) -> None:
        """Show memory manager modal with live store state."""
        from invincat_cli.app_runtime.ui_handlers import show_memory_viewer

        await show_memory_viewer(self)

    async def _show_thread_selector(self) -> None:
        """Show interactive thread selector as a modal screen."""
        from invincat_cli.app_runtime.ui_handlers import show_thread_selector

        await show_thread_selector(self)

    async def _reset_thread_conversation_view(self) -> None:
        """Clear visible conversation state before loading another thread."""
        from invincat_cli.app_runtime.thread_handlers import (
            reset_thread_conversation_view,
        )

        await reset_thread_conversation_view(self)

    def _apply_thread_switch_ids(self, thread_id: str) -> None:
        """Apply active thread IDs and update the welcome banner."""
        from invincat_cli.app_runtime.thread_handlers import apply_thread_switch_ids

        apply_thread_switch_ids(self, thread_id)

    def _rollback_thread_switch_ids(self, snapshot: ThreadSwitchSnapshot) -> None:
        """Restore active thread IDs from a pre-switch snapshot."""
        from invincat_cli.app_runtime.thread_handlers import rollback_thread_switch_ids

        rollback_thread_switch_ids(self, snapshot)

    async def _restore_previous_thread_after_failed_switch(
        self,
        *,
        snapshot: ThreadSwitchSnapshot,
        failed_thread_id: str,
    ) -> bool:
        """Try to restore the previous thread view after a failed switch."""
        from invincat_cli.app_runtime.thread_handlers import (
            restore_previous_thread_after_failed_switch,
        )

        return await restore_previous_thread_after_failed_switch(
            self,
            snapshot=snapshot,
            failed_thread_id=failed_thread_id,
        )

    def _start_server_after_primary_model_switch(
        self,
        *,
        resolved: ResolvedModelSpec,
        target_kwargs: dict[str, Any] | None,
    ) -> None:
        """Update deferred server kwargs and start the background server."""
        from invincat_cli.app_runtime.model_handlers import (
            start_server_after_primary_model_switch,
        )

        start_server_after_primary_model_switch(
            self,
            resolved=resolved,
            target_kwargs=target_kwargs,
        )

    def _apply_primary_model_status(self, *, model_result: Any) -> None:
        """Update status-bar labels after switching the primary model."""
        from invincat_cli.app_runtime.model_handlers import apply_primary_model_status

        apply_primary_model_status(self, model_result=model_result)

    async def _apply_primary_model_switch(
        self,
        *,
        resolved: ResolvedModelSpec,
        model_result: Any,
        target_kwargs: dict[str, Any] | None,
        remote_agent: RemoteAgent | None,
        save_recent_model: Callable[[str], bool],
    ) -> None:
        """Apply primary model switch side effects."""
        from invincat_cli.app_runtime.model_handlers import apply_primary_model_switch

        await apply_primary_model_switch(
            self,
            resolved=resolved,
            model_result=model_result,
            target_kwargs=target_kwargs,
            remote_agent=remote_agent,
            save_recent_model=save_recent_model,
        )

    async def _apply_memory_model_switch(
        self,
        *,
        resolved: ResolvedModelSpec,
        model_result: Any,
        target_kwargs: dict[str, Any] | None,
    ) -> None:
        """Apply memory model switch side effects."""
        from invincat_cli.app_runtime.model_handlers import apply_memory_model_switch

        await apply_memory_model_switch(
            self,
            resolved=resolved,
            model_result=model_result,
            target_kwargs=target_kwargs,
        )

    async def _resume_thread(self, thread_id: str) -> None:
        """Resume a previously saved thread."""
        from invincat_cli.app_runtime.thread_handlers import resume_thread

        await resume_thread(self, thread_id)

    async def _switch_model(
        self,
        model_spec: str,
        *,
        target: ModelTarget = "primary",
        extra_kwargs: dict[str, Any] | None = None,
        persist_as_default: bool = False,
    ) -> None:
        """Switch to a new model, preserving conversation history."""
        from invincat_cli.app_runtime.model_handlers import switch_model

        await switch_model(
            self,
            model_spec,
            target=target,
            extra_kwargs=extra_kwargs,
            persist_as_default=persist_as_default,
        )

    async def _set_default_model(
        self,
        model_spec: str,
        *,
        target: ModelTarget = "primary",
        announce: bool = True,
        apply_to_session: bool = False,
    ) -> bool:
        """Set the default model target in config without switching session."""
        from invincat_cli.app_runtime.model_handlers import set_default_model

        return await set_default_model(
            self,
            model_spec,
            target=target,
            announce=announce,
            apply_to_session=apply_to_session,
        )

    async def _clear_default_model(self, *, target: ModelTarget = "primary") -> None:
        """Remove default model target from config."""
        from invincat_cli.app_runtime.model_handlers import clear_default_model

        await clear_default_model(self, target=target)
