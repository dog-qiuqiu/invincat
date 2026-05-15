"""Action handlers for the thread selector screen."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from invincat_cli.widgets import thread_selector as _thread_selector

if TYPE_CHECKING:
    from textual.events import Click
    from textual.widgets import Checkbox, Input

    from invincat_cli.widgets.thread_selector_option import ThreadOption

logger = logging.getLogger(__name__)


class ThreadSelectorActionMixin:
    """Handle selection movement, sorting, deletion, and click actions."""

    def _apply_sort(self) -> None:
        """Sort filtered threads by the active sort key."""
        key = _thread_selector._active_sort_key(self._sort_by_updated)
        self._filtered_threads.sort(
            key=lambda thread: thread.get(key) or "", reverse=True
        )

    def _move_selection(self, delta: int) -> None:
        """Move selection by delta, updating only the affected rows.

        Args:
            delta: Positions to move (negative for up, positive for down).
        """
        if not self._filtered_threads or not self._option_widgets:
            return

        count = len(self._filtered_threads)
        old_index = self._selected_index
        new_index = (old_index + delta) % count
        self._selected_index = new_index

        self._option_widgets[old_index].set_selected(False)
        self._option_widgets[new_index].set_selected(True)

        if new_index == 0:
            scroll = self.query_one(".thread-list", _thread_selector.VerticalScroll)
            scroll.scroll_home(animate=False)
        else:
            self._option_widgets[new_index].scroll_visible()

    def action_move_up(self) -> None:
        """Move selection up."""
        if self._confirming_delete:
            return
        self._move_selection(-1)

    def action_move_down(self) -> None:
        """Move selection down."""
        if self._confirming_delete:
            return
        self._move_selection(1)

    def _visible_page_size(self) -> int:
        """Return the number of thread options that fit in one visual page.

        Returns:
            Number of thread options per page, at least 1.
        """
        default_page_size = 10
        try:
            scroll = self.query_one(".thread-list", _thread_selector.VerticalScroll)
            height = scroll.size.height
        except _thread_selector.NoMatches:
            logger.debug(
                "Thread list widget not found in _visible_page_size; "
                "using default page size %d",
                default_page_size,
            )
            return default_page_size
        if height <= 0:
            return default_page_size
        return max(1, height)

    def action_page_up(self) -> None:
        """Move selection up by one visible page."""
        if self._confirming_delete or not self._filtered_threads:
            return
        page = self._visible_page_size()
        target = max(0, self._selected_index - page)
        delta = target - self._selected_index
        if delta != 0:
            self._move_selection(delta)

    def action_page_down(self) -> None:
        """Move selection down by one visible page."""
        if self._confirming_delete or not self._filtered_threads:
            return
        count = len(self._filtered_threads)
        page = self._visible_page_size()
        target = min(count - 1, self._selected_index + page)
        delta = target - self._selected_index
        if delta != 0:
            self._move_selection(delta)

    def action_select(self) -> None:
        """Confirm the highlighted thread and dismiss the selector."""
        if self._confirming_delete:
            return
        if self._filtered_threads:
            thread_id = self._filtered_threads[self._selected_index]["thread_id"]
            self.dismiss(thread_id)

    def action_focus_next_filter(self) -> None:
        """Move focus through the filter and column-toggle controls."""
        if self._confirming_delete:
            return
        controls = self._filter_focus_order()
        focused = self.focused
        if focused not in controls:
            controls[0].focus()
            return

        index = controls.index(cast("Input | Checkbox", focused))
        controls[(index + 1) % len(controls)].focus()

    def action_focus_previous_filter(self) -> None:
        """Move focus backward through the filter and column-toggle controls."""
        if self._confirming_delete:
            return
        controls = self._filter_focus_order()
        focused = self.focused
        if focused not in controls:
            controls[-1].focus()
            return

        index = controls.index(cast("Input | Checkbox", focused))
        controls[(index - 1) % len(controls)].focus()

    def action_toggle_sort(self) -> None:
        """Toggle sort between updated_at and created_at."""
        if self._confirming_delete:
            return
        self._sort_by_updated = not self._sort_by_updated
        self._apply_sort()
        self._sync_selected_index()
        self._update_help_widgets()
        self._schedule_list_rebuild()

        self._persist_sort_order(
            "updated_at" if self._sort_by_updated else "created_at"
        )

    def _persist_sort_order(self, order: str) -> None:
        """Save sort-order preference to config, notifying on failure."""

        async def _save() -> None:
            from invincat_cli.model_config import save_thread_sort_order

            ok = await _thread_selector.asyncio.to_thread(save_thread_sort_order, order)
            if not ok:
                self.app.notify(_thread_selector.t("thread.sort_save_failed"), severity="warning")

        self.run_worker(_save(), group="thread-selector-save")

    def action_delete_thread(self) -> None:
        """Show delete confirmation for the highlighted thread."""
        if self._confirming_delete:
            return
        if not self._filtered_threads:
            # Nothing to delete — fall through to quit. Using exit() instead of
            # dismiss() is intentional: dismiss() would just close the modal
            # silently, re-swallowing ctrl+d.
            self.app.exit()
            return
        self._confirming_delete = True
        thread = self._filtered_threads[self._selected_index]
        tid = thread["thread_id"]
        self.app.push_screen(
            _thread_selector.DeleteThreadConfirmScreen(tid),
            lambda confirmed: self._on_delete_confirmed(tid, confirmed),
        )

    @property
    def is_delete_confirmation_open(self) -> bool:
        """Return whether the delete confirmation overlay is visible."""
        return self._confirming_delete

    def _on_delete_confirmed(self, thread_id: str, confirmed: bool | None) -> None:
        """Handle the result from the delete confirmation modal.

        Args:
            thread_id: Thread ID that was targeted.
            confirmed: Whether deletion was confirmed.
        """
        self._confirming_delete = False
        if confirmed:
            self.run_worker(
                self._handle_delete_confirm(thread_id),
                group="thread-delete-execute",
            )
            return
        with _thread_selector.contextlib.suppress(_thread_selector.NoMatches):
            self._get_filter_input().focus()

    async def _handle_delete_confirm(self, thread_id: str) -> None:
        """Execute thread deletion after confirmation.

        Args:
            thread_id: Thread ID to delete.
        """
        from invincat_cli.sessions import delete_thread

        preferred_thread_id: str | None = None
        if self._selected_index + 1 < len(self._filtered_threads):
            preferred_thread_id = self._filtered_threads[self._selected_index + 1][
                "thread_id"
            ]
        elif self._selected_index > 0:
            preferred_thread_id = self._filtered_threads[self._selected_index - 1][
                "thread_id"
            ]

        try:
            await delete_thread(thread_id)
        except (OSError, _thread_selector.sqlite3.Error):
            logger.warning("Failed to delete thread %s", thread_id, exc_info=True)
            self.app.notify(
                _thread_selector.t("thread.delete_failed", thread_id=thread_id[:8]),
                severity="error",
                timeout=3,
                markup=False,
            )
            with _thread_selector.contextlib.suppress(_thread_selector.NoMatches):
                self.query_one("#thread-filter", _thread_selector.Input).focus()
            return

        self._threads = [
            thread for thread in self._threads if thread["thread_id"] != thread_id
        ]
        self._update_filtered_list()
        if preferred_thread_id is not None:
            for index, thread in enumerate(self._filtered_threads):
                if thread["thread_id"] == preferred_thread_id:
                    self._selected_index = index
                    break
        if self._selected_index >= len(self._filtered_threads):
            self._selected_index = max(0, len(self._filtered_threads) - 1)
        await self._build_list()
        with _thread_selector.contextlib.suppress(_thread_selector.NoMatches):
            self.query_one("#thread-filter", _thread_selector.Input).focus()

    def on_click(self, event: Click) -> None:  # noqa: PLR6301  # Textual event handler
        """Open Rich-style hyperlinks on single click."""
        _thread_selector.open_style_link(event)

    def on_thread_option_clicked(self, event: ThreadOption.Clicked) -> None:
        """Handle click on a thread option.

        Args:
            event: The clicked message with thread ID and index.
        """
        if self._confirming_delete:
            return
        if 0 <= event.index < len(self._filtered_threads):
            self._selected_index = event.index
            self.dismiss(event.thread_id)

    def action_cancel(self) -> None:
        """Cancel the selection."""
        self.dismiss(None)
