"""Autocomplete popup widgets for chat input."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from textual.containers import VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.widgets import Static

from invincat_cli.widgets.chat_input_styles import (
    COMPLETION_OPTION_CSS,
    COMPLETION_POPUP_CSS,
)

if TYPE_CHECKING:
    from textual.events import Click

logger = logging.getLogger(__name__)


class CompletionOption(Static):
    """A clickable completion option in the autocomplete popup."""

    DEFAULT_CSS = COMPLETION_OPTION_CSS

    class Clicked(Message):
        """Message sent when a completion option is clicked."""

        def __init__(self, index: int) -> None:
            """Initialize with the clicked option index."""
            super().__init__()
            self.index = index

    def __init__(
        self,
        label: str,
        description: str,
        index: int,
        is_selected: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialize the completion option.

        Args:
            label: The main label text (e.g., command name or file path)
            description: Secondary description text
            index: Index of this option in the suggestions list
            is_selected: Whether this option is currently selected
            **kwargs: Additional arguments for parent
        """
        super().__init__(**kwargs)
        self._label = label
        self._description = description
        self._index = index
        self._is_selected = is_selected

    def on_mount(self) -> None:
        """Set up the option display on mount."""
        self._update_display()

    def _update_display(self) -> None:
        """Update the display text and styling."""
        display_label = self._label.removeprefix("/")
        if self._description:
            content = Content.from_markup(
                "[bold]$label[/bold]  [dim]$desc[/dim]",
                label=display_label,
                desc=self._description,
            )
        else:
            content = Content.from_markup("[bold]$label[/bold]", label=display_label)

        self.update(content)

        if self._is_selected:
            self.add_class("completion-option-selected")
        else:
            self.remove_class("completion-option-selected")

    def set_selected(self, *, selected: bool) -> None:
        """Update the selected state of this option."""
        if self._is_selected != selected:
            self._is_selected = selected
            self._update_display()

    def set_content(
        self, label: str, description: str, index: int, *, is_selected: bool
    ) -> None:
        """Replace label, description, index, and selection in-place."""
        self._label = label
        self._description = description
        self._index = index
        self._is_selected = is_selected
        self._update_display()

    def on_click(self, event: Click) -> None:
        """Handle click on this option."""
        event.stop()
        self.post_message(self.Clicked(self._index))


class CompletionPopup(VerticalScroll):
    """Popup widget that displays completion suggestions as clickable options."""

    DEFAULT_CSS = COMPLETION_POPUP_CSS

    class OptionClicked(Message):
        """Message sent when a completion option is clicked."""

        def __init__(self, index: int) -> None:
            """Initialize with the clicked option index."""
            super().__init__()
            self.index = index

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the completion popup."""
        super().__init__(**kwargs)
        self.can_focus = False
        self._options: list[CompletionOption] = []
        self._selected_index = 0
        self._pending_suggestions: list[tuple[str, str]] = []
        self._pending_selected: int = 0
        self._rebuild_generation: int = 0

    def update_suggestions(
        self, suggestions: list[tuple[str, str]], selected_index: int
    ) -> None:
        """Update the popup with new suggestions."""
        if not suggestions:
            self.hide()
            return

        self._selected_index = selected_index
        self._pending_suggestions = suggestions
        self._pending_selected = selected_index
        # Increment generation so stale callbacks from prior calls are skipped.
        self._rebuild_generation += 1
        gen = self._rebuild_generation
        # show() deferred to _rebuild_options to avoid a flash of stale content.
        self.call_after_refresh(lambda: self._rebuild_options(gen))

    async def _rebuild_options(self, generation: int) -> None:
        """Rebuild option widgets from pending suggestions.

        Reuses existing DOM nodes where possible to avoid flicker from
        a full teardown/mount cycle while the popup is visible.

        Args:
            generation: Caller's generation counter; skipped if superseded.
        """
        if generation != self._rebuild_generation:
            return

        suggestions = self._pending_suggestions
        selected_index = self._pending_selected

        if not suggestions:
            self.hide()
            return

        existing = len(self._options)
        needed = len(suggestions)

        # Update existing widgets in-place
        for i in range(min(existing, needed)):
            label, desc = suggestions[i]
            self._options[i].set_content(
                label, desc, i, is_selected=(i == selected_index)
            )

        # DOM mutations: trim extras / mount new widgets
        try:
            if existing > needed:
                for option in self._options[needed:]:
                    await option.remove()
                del self._options[needed:]

            if needed > existing:
                new_widgets: list[CompletionOption] = []
                for idx in range(existing, needed):
                    label, desc = suggestions[idx]
                    option = CompletionOption(
                        label=label,
                        description=desc,
                        index=idx,
                        is_selected=(idx == selected_index),
                    )
                    new_widgets.append(option)
                self._options.extend(new_widgets)
                await self.mount(*new_widgets)
        except Exception:
            logger.exception("Failed to rebuild completion popup; hiding to recover")
            self._options = []
            with contextlib.suppress(Exception):
                await self.remove_children()
            self.hide()
            return

        self.show()

        if 0 <= selected_index < len(self._options):
            self._options[selected_index].scroll_visible()

    def update_selection(self, selected_index: int) -> None:
        """Update which option is selected without rebuilding the list."""
        # Keep pending state in sync so an in-flight _rebuild_options uses
        # the latest selection.
        self._pending_selected = selected_index

        if self._selected_index == selected_index:
            return

        # Deselect previous
        if 0 <= self._selected_index < len(self._options):
            self._options[self._selected_index].set_selected(selected=False)

        # Select new
        self._selected_index = selected_index
        if 0 <= selected_index < len(self._options):
            self._options[selected_index].set_selected(selected=True)
            self._options[selected_index].scroll_visible()

    def on_completion_option_clicked(self, event: CompletionOption.Clicked) -> None:
        """Handle click on a completion option."""
        event.stop()
        self.post_message(self.OptionClicked(event.index))

    def hide(self) -> None:
        """Hide the popup."""
        self._pending_suggestions = []
        self._rebuild_generation += 1  # Cancel any in-flight rebuild
        self.styles.display = "none"  # type: ignore[assignment]  # Textual accepts string display values at runtime

    def show(self) -> None:
        """Show the popup."""
        self.styles.display = "block"
