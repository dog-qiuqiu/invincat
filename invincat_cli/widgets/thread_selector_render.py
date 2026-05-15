"""DOM rendering helpers for the thread selector."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from invincat_cli.widgets import thread_selector as _thread_selector

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ThreadSelectorRenderMixin:
    """Handle table-pane, list, header, and error rendering."""

    async def _show_mount_error(self, detail: str) -> None:
        """Display an error message inside the thread list and refocus.

        Args:
            detail: Human-readable error detail to show.
        """
        try:
            async with self._render_lock:
                try:
                    scroll = self.query_one(".thread-list", _thread_selector.VerticalScroll)
                    await scroll.remove_children()
                    await scroll.mount(
                        _thread_selector.Static(
                            _thread_selector.Content.from_markup(
                                "[red]Failed to load threads: $detail. "
                                "Press Esc to close.[/red]",
                                detail=detail,
                            ),
                            classes="thread-empty",
                        )
                    )
                except _thread_selector.NoMatches:
                    try:
                        overlay = self.query_one(".thread-loading-overlay", _thread_selector.Vertical)
                        await overlay.remove_children()
                        await overlay.mount(
                            _thread_selector.Static(
                                _thread_selector.Content.from_markup(
                                    "[red]Failed to load threads: $detail. "
                                    "Press Esc to close.[/red]",
                                    detail=detail,
                                )
                            )
                        )
                    except _thread_selector.NoMatches:
                        pass
        except Exception:
            logger.warning(
                "Could not display error message in thread selector UI",
                exc_info=True,
            )
        self.focus()

    async def _build_table_pane(self) -> None:
        """Build the table pane after loading completes.

        Replaces the loading overlay with the actual table header and list.
        """
        async with self._render_lock:
            try:
                body = self.query_one(".thread-selector-body", _thread_selector.Horizontal)
            except _thread_selector.NoMatches:
                return

            try:
                loading_container = self.query_one(
                    "#thread-loading-container", _thread_selector.Vertical
                )
                await loading_container.remove()
            except _thread_selector.NoMatches:
                pass

            self._column_widths = self._compute_column_widths()

            table_pane = _thread_selector.Vertical(classes="thread-table-pane")
            await body.mount(table_pane, before=0)

            header = _thread_selector.Horizontal(classes="thread-list-header", id="thread-header")
            await table_pane.mount(header)

            header.mount(_thread_selector.Static("", classes="thread-cell thread-cell-cursor"))
            sort_key = _thread_selector._active_sort_key(self._sort_by_updated)
            for key in _thread_selector._visible_column_keys(self._columns):
                cell = _thread_selector.Static(
                    _thread_selector._format_header_label(key),
                    classes=_thread_selector._header_cell_classes(key, sort_key=sort_key),
                    expand=key == "initial_prompt",
                    markup=False,
                )
                _thread_selector._apply_column_width(cell, key, self._column_widths)
                await header.mount(cell)

            scroll = _thread_selector.VerticalScroll(classes="thread-list")
            await table_pane.mount(scroll)

            if not self._filtered_threads:
                self._option_widgets = []
                await scroll.mount(
                    _thread_selector.Static(
                        _thread_selector.Content.styled(_thread_selector.t("thread.no_threads"), "dim"),
                        classes="thread-empty",
                    )
                )
                return

            self._option_widgets, selected_widget = self._create_option_widgets()
            await scroll.mount(*self._option_widgets)

            if selected_widget:
                self.call_after_refresh(self._scroll_selected_into_view)

    async def _build_list(self, *, recompute_widths: bool = True) -> None:
        """Build the thread option widgets.

        Args:
            recompute_widths: Whether to recalculate shared column widths first.
        """
        async with self._render_lock:
            try:
                scroll = self.query_one(".thread-list", _thread_selector.VerticalScroll)
            except _thread_selector.NoMatches:
                return

            if recompute_widths:
                self._column_widths = self._compute_column_widths()
            with self.app.batch_update():
                await scroll.remove_children()
                self._update_help_widgets()

                if not self._filtered_threads:
                    self._option_widgets = []
                    await scroll.mount(
                        _thread_selector.Static(
                            _thread_selector.Content.styled(_thread_selector.t("thread.no_threads"), "dim"),
                            classes="thread-empty",
                        )
                    )
                    return

                self._option_widgets, selected_widget = self._create_option_widgets()
                await scroll.mount(*self._option_widgets)

            if selected_widget:
                self.call_after_refresh(self._scroll_selected_into_view)

    def _create_option_widgets(self) -> tuple[list[_thread_selector.ThreadOption], _thread_selector.ThreadOption | None]:
        """Build option widgets from filtered threads without mounting.

        Returns:
            Tuple of all option widgets and the currently selected widget.
        """
        widgets: list[_thread_selector.ThreadOption] = []
        selected_widget: _thread_selector.ThreadOption | None = None

        for i, thread in enumerate(self._filtered_threads):
            is_current = thread["thread_id"] == self._current_thread
            is_selected = i == self._selected_index

            classes = "thread-option"
            if is_selected:
                classes += " thread-option-selected"
            if is_current:
                classes += " thread-option-current"

            widget = _thread_selector.ThreadOption(
                thread=thread,
                index=i,
                columns=self._columns,
                column_widths=self._column_widths,
                selected=is_selected,
                current=is_current,
                relative_time=self._relative_time,
                cell_text=self._cell_text or None,
                classes=classes,
            )
            widgets.append(widget)
            if is_selected:
                selected_widget = widget

        return widgets, selected_widget

    def _scroll_selected_into_view(self) -> None:
        """Scroll selected option into view without animation."""
        if not self._option_widgets:
            return
        if self._selected_index >= len(self._option_widgets):
            return
        try:
            scroll = self.query_one(".thread-list", _thread_selector.VerticalScroll)
        except _thread_selector.NoMatches:
            return

        if self._selected_index == 0:
            scroll.scroll_home(animate=False)
        else:
            self._option_widgets[self._selected_index].scroll_visible(animate=False)

    def _update_help_widgets(self) -> None:
        """Update visible header and help text after state changes."""
        self._schedule_header_rebuild()

        try:
            help_widget = self.query_one("#thread-help", _thread_selector.Static)
            help_widget.update(self._build_help_text())
        except _thread_selector.NoMatches:
            logger.debug("Help widget #thread-help not found during update")

        with _thread_selector.contextlib.suppress(_thread_selector.NoMatches):
            sort_checkbox = self.query_one(f"#{_thread_selector._SORT_SWITCH_ID}", _thread_selector.Checkbox)
            sort_checkbox.label = self._format_sort_toggle_label()
            if sort_checkbox.value != self._sort_by_updated:
                sort_checkbox.value = self._sort_by_updated

    def _schedule_header_rebuild(self) -> None:
        """Queue a header rebuild to reflect column/sort changes."""
        self.run_worker(
            self._rebuild_header,
            exclusive=True,
            group="thread-selector-header",
        )

    async def _rebuild_header(self) -> None:
        """Replace header cells to match current visible columns."""
        try:
            header = self.query_one("#thread-header", _thread_selector.Horizontal)
        except _thread_selector.NoMatches:
            return
        sort_key = _thread_selector._active_sort_key(self._sort_by_updated)
        self._column_widths = self._compute_column_widths()
        with self.app.batch_update():
            await header.remove_children()
            cells: list[_thread_selector.Static] = [_thread_selector.Static("", classes="thread-cell thread-cell-cursor")]
            for key in _thread_selector._visible_column_keys(self._columns):
                cell = _thread_selector.Static(
                    _thread_selector._format_header_label(key),
                    classes=_thread_selector._header_cell_classes(key, sort_key=sort_key),
                    expand=key == "initial_prompt",
                    markup=False,
                )
                _thread_selector._apply_column_width(cell, key, self._column_widths)
                cells.append(cell)
            await header.mount(*cells)
