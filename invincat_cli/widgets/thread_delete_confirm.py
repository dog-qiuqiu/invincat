"""Delete confirmation modal for the thread selector."""

from __future__ import annotations

from typing import TYPE_CHECKING

from textual.screen import ModalScreen

from invincat_cli.widgets.thread_selector_style import (
    DELETE_THREAD_CONFIRM_BINDINGS,
    DELETE_THREAD_CONFIRM_CSS,
)

if TYPE_CHECKING:
    from textual.app import ComposeResult


class DeleteThreadConfirmScreen(ModalScreen[bool]):
    """Confirmation modal shown before deleting a thread."""

    BINDINGS = DELETE_THREAD_CONFIRM_BINDINGS
    CSS = DELETE_THREAD_CONFIRM_CSS

    def __init__(self, thread_id: str) -> None:
        """Initialize the confirmation modal.

        Args:
            thread_id: Thread ID the user is being asked to delete.
        """
        super().__init__()
        self._delete_thread_id = thread_id

    def compose(self) -> ComposeResult:
        """Compose the confirmation dialog.

        Yields:
            Widgets for the delete confirmation prompt.
        """
        from invincat_cli.widgets import thread_selector as _thread_selector

        with _thread_selector.Vertical(id="delete-confirm"):
            yield _thread_selector.Static(
                _thread_selector.Content.from_markup(
                    _thread_selector.t(
                        "thread.delete_confirm", thread_id=self._delete_thread_id
                    ),
                    tid=self._delete_thread_id,
                ),
                classes="thread-confirm-text",
            )
            yield _thread_selector.Static(
                _thread_selector.t("thread.delete_help"),
                classes="thread-confirm-help",
            )

    def action_confirm(self) -> None:
        """Confirm deletion."""
        self.dismiss(True)

    def action_cancel(self) -> None:
        """Cancel deletion."""
        self.dismiss(False)
