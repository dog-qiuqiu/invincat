"""Tool-call message widget."""

from __future__ import annotations

import logging
from time import time
from typing import TYPE_CHECKING, Any

from textual.containers import Vertical
from textual.content import Content
from textual.events import Click
from textual.widgets import Static

from invincat_cli import theme
from invincat_cli.config import get_glyphs
from invincat_cli.presentation.formatting import format_duration
from invincat_cli.presentation.tool_display import format_tool_display
from invincat_cli.widgets import messages as _messages
from invincat_cli.widgets.message_styles import TOOL_CALL_MESSAGE_CSS
from invincat_cli.widgets.tool_call_output import ToolCallOutputMixin

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.timer import Timer

logger = logging.getLogger(__name__)


class ToolCallMessage(ToolCallOutputMixin, Vertical):
    """Widget displaying a tool call with collapsible output.

    Tool outputs are shown as a 3-line preview by default.
    Press Ctrl+O to expand/collapse the full output.
    Shows an animated "Running..." indicator while the tool is executing.
    """

    DEFAULT_CSS = TOOL_CALL_MESSAGE_CSS
    """Left border tracks tool lifecycle; hover brightens for interactivity."""

    # Max lines/chars to show in preview mode
    _PREVIEW_LINES = 6
    _PREVIEW_CHARS = 400

    def __init__(
        self,
        tool_name: str,
        args: dict[str, Any] | None = None,
        *,
        tool_call_id: str | int | None = None,
        args_finalized: bool | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize a tool call message.

        Args:
            tool_name: Name of the tool being called
            args: Tool arguments (optional)
            tool_call_id: ID of the tool call (optional)
            args_finalized: Override for whether args are complete.  Pass
                ``False`` when early-mounting before args have streamed in,
                ``True`` when the widget is created with complete args (e.g.
                hydration from store or final tool call).  ``None`` (default)
                falls back to ``bool(args)``, which is True only when a
                non-empty dict is provided.
            **kwargs: Additional arguments passed to parent
        """
        super().__init__(**kwargs)
        self._tool_name = tool_name
        self._args = args or {}
        self._tool_call_id = tool_call_id
        self._status = "pending"  # Waiting for approval or auto-approve
        # True once real args have been streamed in via update_args().
        # False means the widget was early-mounted and args are still streaming.
        if args_finalized is not None:
            self._args_finalized: bool = args_finalized
        else:
            # Heuristic: if a non-empty args dict was supplied the caller had
            # the final args at construction time; empty dict is ambiguous
            # (could be a no-arg tool OR args not yet received), so default
            # to False and let update_args() flip it when real args arrive.
            self._args_finalized = bool(args)
        self._output: str = ""
        self._expanded: bool = False
        # Widget references (set in on_mount)
        self._status_widget: Static | None = None
        self._preview_widget: Static | None = None
        self._hint_widget: Static | None = None
        self._full_widget: Static | None = None
        # Animation state
        self._spinner_position = 0
        self._start_time: float | None = None
        self._animation_timer: Timer | None = None
        # Cache frequently-read config so _update_animation doesn't
        # call get_glyphs() / get_theme_colors() on every 100 ms tick.
        # These values are stable for the lifetime of the widget.
        self._spinner_frames: list[str] | None = None
        self._progress_detail: str | None = None
        # Deferred state for hydration (set by MessageData.to_widget)
        self._deferred_status: str | None = None
        self._deferred_output: str | None = None
        self._deferred_expanded: bool = False

    def compose(self) -> ComposeResult:
        """Compose the tool call message layout.

        Yields:
            Widgets for header, arguments, status, and output display.
        """
        tool_label = format_tool_display(self._tool_name, self._args)
        yield Static(tool_label, markup=False, classes="tool-header")
        # Task: dedicated description line (dim, truncated)
        if self._tool_name == "task":
            desc = self._args.get("description", "")
            if desc:
                max_len = 120
                suffix = "..." if len(desc) > max_len else ""
                truncated = desc[:max_len].rstrip() + suffix
                yield Static(
                    Content.styled(truncated, "dim"),
                    classes="tool-task-desc",
                )
        # Only show args for tools where header doesn't capture the key info
        elif self._tool_name not in _messages._TOOLS_WITH_HEADER_INFO:
            args = self._filtered_args()
            if args:
                args_str = ", ".join(
                    f"{k}={v!r}" for k, v in list(args.items())[:_messages._MAX_INLINE_ARGS]
                )
                if len(args) > _messages._MAX_INLINE_ARGS:
                    args_str += ", ..."
                yield Static(
                    Content.from_markup("[dim]($args)[/dim]", args=args_str),
                    classes="tool-args",
                )
        # Status - shows running animation while pending, then final status
        yield Static("", classes="tool-status", id="status")
        # Output area - hidden initially, shown when output is set
        yield Static("", classes="tool-output-preview", id="output-preview")
        yield Static("", classes="tool-output", id="output-full")
        yield Static("", classes="tool-output-hint", id="output-hint")

    def on_mount(self) -> None:
        """Cache widget references and hide all status/output areas initially."""
        if _messages.is_ascii_mode():
            self.add_class("-ascii")

        self._status_widget = self.query_one("#status", Static)
        self._preview_widget = self.query_one("#output-preview", Static)
        self._hint_widget = self.query_one("#output-hint", Static)
        self._full_widget = self.query_one("#output-full", Static)
        # Cache spinner frames once — get_glyphs() reads config on every call
        # and calling it 10 times/sec per widget wastes CPU unnecessarily.
        self._spinner_frames = list(get_glyphs().spinner_frames)
        # Hide output initially - status shown for pending/running/error/reject
        self._preview_widget.display = False
        self._hint_widget.display = False
        self._full_widget.display = False

        # If this widget was hydrated from the store it already has a terminal
        # (or running) deferred status — skip the animation startup entirely and
        # go straight to _restore_deferred_state.  Starting the timer and then
        # immediately stopping it causes a visible spinner flash for re-mounted
        # widgets that are already in a final state.
        if self._deferred_status is not None:
            self._restore_deferred_state()
            return

        # Show pending status by default (waiting for approval)
        if self._status == "pending":
            self._start_time = time()
            if not self._args_finalized:
                # Args still streaming — model hasn't finished generating yet
                self._status_widget.add_class("generating")
            else:
                # Args complete — waiting for user approval (HITL)
                self._status_widget.add_class("pending")
            self._status_widget.display = True
            self._update_animation()
            self._animation_timer = self.set_interval(0.1, self._update_animation)
        elif self._status == "running":
            self._status_widget.add_class("pending")
            self._status_widget.display = True
            self._update_animation()
            self._animation_timer = self.set_interval(0.1, self._update_animation)
        elif self._status in ("success", "error"):
            self._update_output_display()
            if self._status == "error" and self._status_widget:
                self._status_widget.add_class("error")
                error_icon = get_glyphs().error
                colors = theme.get_theme_colors(self)
                self._status_widget.update(
                    Content.styled(f"{error_icon} Error", colors.error)
                )
                self._status_widget.display = True

    def _restore_deferred_state(self) -> None:
        """Restore state from deferred values (used when hydrating from data)."""
        if self._deferred_status is None:
            return

        # Stop the pending/generating timer started unconditionally in on_mount —
        # the deferred state will set the real status, which may not be "pending".
        self._stop_pending_animation()

        status = self._deferred_status
        output = self._deferred_output or ""
        self._expanded = self._deferred_expanded

        # Clear deferred values
        self._deferred_status = None
        self._deferred_output = None
        self._deferred_expanded = False

        # Restore based on status (don't restart animations for running tools)
        colors = theme.get_theme_colors(self)
        match status:
            case "success":
                self._status = "success"
                self._output = output
                self._update_output_display()
            case "error":
                self._status = "error"
                self._output = output
                if self._status_widget:
                    self._status_widget.add_class("error")
                    error_icon = get_glyphs().error
                    self._status_widget.update(
                        Content.styled(f"{error_icon} Error", colors.error)
                    )
                    self._status_widget.display = True
                self._update_output_display()
            case "rejected":
                self._status = "rejected"
                if self._status_widget:
                    self._status_widget.add_class("rejected")
                    error_icon = get_glyphs().error
                    self._status_widget.update(
                        Content.styled(f"{error_icon} Rejected", colors.warning)
                    )
                    self._status_widget.display = True
            case "skipped":
                self._status = "skipped"
                if self._status_widget:
                    self._status_widget.add_class("rejected")
                    self._status_widget.update(Content.styled("- Skipped", "dim"))
                    self._status_widget.display = True
            case "running":
                # For running tools, show static "Running..." without animation
                # (animations shouldn't be restored for archived tools)
                self._status = "running"
                if self._status_widget:
                    self._status_widget.add_class("pending")
                    frame = get_glyphs().spinner_frames[0]
                    self._status_widget.update(
                        Content.styled(f"{frame} Running...", colors.warning)
                    )
                    self._status_widget.display = True
            case _:
                # pending or unknown - leave as default
                pass

    def _update_animation(self) -> None:
        """Update spinner animation for generating, pending, and running states."""
        if self._status_widget is None:
            return
        # Use cached frames (set in on_mount); fall back to live lookup if the
        # widget was somehow called before on_mount (shouldn't happen in practice).
        spinner_frames = self._spinner_frames or list(get_glyphs().spinner_frames)
        frame = spinner_frames[self._spinner_position]
        self._spinner_position = (self._spinner_position + 1) % len(spinner_frames)
        elapsed = ""
        if self._start_time is not None:
            elapsed_secs = int(time() - self._start_time)
            elapsed = f" ({format_duration(elapsed_secs)})"
        if self._status == "pending":
            if not self._args_finalized:
                self._status_widget.update(
                    Content.styled(
                        self._format_progress_line(
                            frame,
                            f"Generating...{elapsed}",
                        ),
                        "dim",
                    )
                )
            else:
                colors = theme.get_theme_colors(self)
                self._status_widget.update(
                    Content.styled(
                        self._format_progress_line(
                            frame,
                            f"Pending...{elapsed}",
                        ),
                        colors.warning,
                    )
                )
        elif self._status == "running":
            self._status_widget.update(
                Content.styled(
                    self._format_progress_line(frame, f"Running...{elapsed}"),
                    theme.get_theme_colors(self).warning,
                )
            )

    def _format_progress_line(self, frame: str, base: str) -> str:
        """Return the animated status line, optionally with progress detail."""
        if self._progress_detail:
            return f"{frame} {base} - {self._progress_detail}"
        return f"{frame} {base}"

    def _stop_pending_animation(self) -> None:
        """Stop the animation timer (covers generating/pending/running states)."""
        if self._animation_timer is not None:
            self._animation_timer.stop()
            self._animation_timer = None

    def set_running(self) -> None:
        """Mark the tool as running (approved and executing).

        Call this when approval is granted to start the running animation.
        """
        if self._status == "running":
            return  # Already running

        self._status = "running"
        self._start_time = time()
        if self._status_widget:
            self._status_widget.remove_class("generating")
            self._status_widget.add_class("pending")
            self._status_widget.display = True
        # Reuse the existing timer from generating/pending; start one if missing
        if self._animation_timer is None:
            self._update_animation()
            self._animation_timer = self.set_interval(0.1, self._update_animation)

    def set_progress_detail(self, detail: str) -> None:
        """Update the visible in-progress detail for long-running tools."""
        normalized = " ".join(str(detail).split())
        self._progress_detail = normalized or None
        if self._status in {"pending", "running"}:
            self._status = "running"
        if self._status_widget:
            self._status_widget.remove_class("generating")
            self._status_widget.add_class("pending")
            self._status_widget.display = True
        if self.is_mounted and self._animation_timer is None:
            self._start_time = self._start_time or time()
            self._animation_timer = self.set_interval(0.1, self._update_animation)
        if self._status in {"pending", "running"}:
            self._update_animation()

    def _stop_animation(self) -> None:
        """Stop the animation timer (covers all animated states)."""
        self._stop_pending_animation()

    def set_success(self, result: str = "") -> None:
        """Mark the tool call as successful.

        Args:
            result: Tool output/result to display
        """
        self._stop_animation()
        self._status = "success"
        self._progress_detail = None
        # Strip redundant success trailer — the UI already conveys success
        self._output = _messages._strip_success_exit_line(result)
        if self._status_widget:
            self._status_widget.remove_class("generating", "pending")
            # Hide status on success - output speaks for itself
            self._status_widget.display = False
        self._update_output_display()
        self.refresh()

    def set_error(self, error: str) -> None:
        """Mark the tool call as failed.

        Args:
            error: Error message
        """
        self._stop_animation()
        self._status = "error"
        self._progress_detail = None
        # For shell commands, prepend the full command so users can see what failed
        command = (
            self._args.get("command")
            if self._tool_name in {"shell", "bash", "execute"}
            else None
        )
        if command and isinstance(command, str) and command.strip():
            self._output = f"$ {command}\n\n{error}"
        else:
            self._output = error
        if self._status_widget:
            self._status_widget.remove_class("generating", "pending")
            self._status_widget.add_class("error")
            error_icon = get_glyphs().error
            colors = theme.get_theme_colors(self)
            self._status_widget.update(
                Content.styled(f"{error_icon} Error", colors.error)
            )
            self._status_widget.display = True
        # Always show full error - errors should be visible
        self._expanded = True
        self._update_output_display()
        self.refresh()

    def set_rejected(self) -> None:
        """Mark the tool call as rejected by user."""
        self._stop_animation()
        self._status = "rejected"
        self._progress_detail = None
        if self._status_widget:
            self._status_widget.remove_class("generating", "pending")
            self._status_widget.add_class("rejected")
            error_icon = get_glyphs().error
            text = f"{error_icon} Rejected"
            colors = theme.get_theme_colors(self)
            self._status_widget.update(Content.styled(text, colors.warning))
            self._status_widget.display = True

    def set_skipped(self) -> None:
        """Mark the tool call as skipped (due to another rejection)."""
        self._stop_animation()
        self._status = "skipped"
        self._progress_detail = None
        if self._status_widget:
            self._status_widget.remove_class("generating", "pending")
            self._status_widget.add_class("rejected")  # Use same styling as rejected
            self._status_widget.update(Content.styled("- Skipped", "dim"))
            self._status_widget.display = True

    def toggle_output(self) -> None:
        """Toggle between preview and full output display."""
        if not self._output:
            return
        self._expanded = not self._expanded
        self._update_output_display()

    def on_click(self, event: Click) -> None:
        """Toggle output expansion, or show timestamp if no output."""
        event.stop()  # Prevent click from bubbling up and scrolling
        if self._output:
            self.toggle_output()
        else:
            _messages._show_timestamp_toast(self)

    def _filtered_args(self) -> dict[str, Any]:
        """Filter large tool args for display.

        Returns:
            Filtered args dict with only display-relevant keys for write/edit tools.
        """
        if self._tool_name not in {"write_file", "edit_file"}:
            return self._args

        filtered: dict[str, Any] = {}
        for key in ("file_path", "path", "replace_all"):
            if key in self._args:
                filtered[key] = self._args[key]
        return filtered

    def update_args(self, args: dict[str, Any]) -> None:
        """Update the displayed tool arguments after early mount.

        Called once args have finished streaming in, to fill in the widget
        that was initially mounted with empty args for instant feedback.
        Safe to call before or after ``on_mount``; when called before mount
        the updated ``self._args`` will be picked up by ``compose()``
        automatically.

        Args:
            args: Fully parsed argument dict for this tool call.
        """
        self._args = args
        self._args_finalized = True

        # Not yet mounted — compose() will use the updated self._args when it
        # runs, so there is nothing more to do here.
        if not self.is_mounted:
            return

        # If still in pending state, transition from "Generating..." to "⏳ Pending..."
        # to signal that args are complete and we're now waiting for approval.
        if self._status == "pending" and self._status_widget is not None:
            self._status_widget.remove_class("generating")
            self._status_widget.add_class("pending")
            # Immediately refresh display; timer keeps spinning from here
            self._update_animation()

        # --- Header: format_tool_display derives the primary label from
        #     self._tool_name + self._args (e.g. file path, command).
        try:
            header = self.query_one(".tool-header", Static)
            header.update(format_tool_display(self._tool_name, self._args))
        except Exception:
            logger.debug("update_args: could not update .tool-header", exc_info=True)

        # --- Task description line (task tool only, no .tool-args sibling)
        if self._tool_name == "task":
            desc = args.get("description", "")
            try:
                task_desc = self.query_one(".tool-task-desc", Static)
                if desc:
                    max_len = 120
                    suffix = "..." if len(desc) > max_len else ""
                    truncated = desc[:max_len].rstrip() + suffix
                    task_desc.update(Content.styled(truncated, "dim"))
                    task_desc.display = True
                else:
                    task_desc.display = False
            except Exception:
                logger.debug(
                    "update_args: could not update .tool-task-desc", exc_info=True
                )
            return  # task tool never has a .tool-args widget

        # --- Inline args line for non-header tools
        if self._tool_name not in _messages._TOOLS_WITH_HEADER_INFO:
            filtered = self._filtered_args()
            try:
                args_widget = self.query_one(".tool-args", Static)
                if filtered:
                    args_str = ", ".join(
                        f"{k}={v!r}"
                        for k, v in list(filtered.items())[:_messages._MAX_INLINE_ARGS]
                    )
                    if len(filtered) > _messages._MAX_INLINE_ARGS:
                        args_str += ", ..."
                    args_widget.update(
                        Content.from_markup("[dim]($args)[/dim]", args=args_str)
                    )
                    args_widget.display = True
                else:
                    args_widget.display = False
            except Exception:
                logger.debug("update_args: could not update .tool-args", exc_info=True)
