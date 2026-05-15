"""Layout and input-event helpers for the thread selector."""

from __future__ import annotations

from typing import TYPE_CHECKING

from invincat_cli.widgets import thread_selector as _thread_selector

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.events import Key


class ThreadSelectorLayoutMixin:
    """Handle screen composition, input events, and filter controls."""

    @staticmethod
    def _switch_id(column_key: str) -> str:
        """Return the DOM id for a column toggle switch."""
        return f"{_thread_selector._SWITCH_ID_PREFIX}{column_key}"

    @staticmethod
    def _switch_column_key(switch_id: str | None) -> str | None:
        """Extract the column key from a switch id.

        Args:
            switch_id: Widget id for a switch in the control panel.

        Returns:
            The corresponding column key, or `None` for unrelated ids.
        """
        if not switch_id or not switch_id.startswith(_thread_selector._SWITCH_ID_PREFIX):
            return None
        return switch_id.removeprefix(_thread_selector._SWITCH_ID_PREFIX)

    def _sync_selected_index(self) -> None:
        """Select the current thread when it exists in the loaded rows."""
        self._selected_index = 0
        for i, thread in enumerate(self._filtered_threads):
            if thread["thread_id"] == self._current_thread:
                self._selected_index = i
                break

    def _build_title(self, thread_url: str | None = None) -> str | _thread_selector.Content:
        """Build the title, optionally with a clickable thread ID link.

        Args:
            thread_url: LangSmith thread URL. When provided, the thread ID is
                rendered as a clickable hyperlink.

        Returns:
            Plain string or `_thread_selector.Content` with an embedded hyperlink.
        """
        if not self._current_thread:
            return _thread_selector.t("thread.title")
        if thread_url:
            return _thread_selector.Content.assemble(
                f"{_thread_selector.t('thread.title')} ({_thread_selector.t('thread.current_thread', thread_id='')}",
                (
                    self._current_thread,
                    _thread_selector.TStyle(
                        foreground=_thread_selector.TColor.parse(_thread_selector.theme.get_theme_colors(self).primary),
                        link=thread_url,
                    ),
                ),
                ")",
            )
        return f"{_thread_selector.t('thread.title')} ({_thread_selector.t('thread.current_thread', thread_id=self._current_thread)})"

    def _build_help_text(self) -> str:
        """Build the footer help text for the selector.

        Returns:
            Footer guidance for the active selector bindings.
        """
        glyphs = _thread_selector.get_glyphs()
        lines = (
            f"{glyphs.arrow_up}/{glyphs.arrow_down} {_thread_selector.t('thread.navigate')}"
            f" {glyphs.bullet} Enter {_thread_selector.t('thread.select_action')}"
            f" {glyphs.bullet} Tab/Shift+Tab {_thread_selector.t('thread.focus_options')}"
            f" {glyphs.bullet} Space {_thread_selector.t('thread.toggle_option')}"
            f" {glyphs.bullet} Ctrl+D {_thread_selector.t('thread.delete_action')}"
            f" {glyphs.bullet} Esc {_thread_selector.t('thread.cancel_action')}"
        )
        limit = self._effective_thread_limit()
        if len(self._threads) >= limit:
            lines += "\n" + _thread_selector.t("thread.showing_limit", limit=limit)
        return lines

    def _effective_thread_limit(self) -> int:
        """Return the resolved thread limit for display purposes."""
        if self._thread_limit is not None:
            return self._thread_limit
        from invincat_cli.sessions import get_thread_limit

        return get_thread_limit()

    def _format_sort_toggle_label(self) -> str:
        """Return the control-panel sort label for the toggle switch."""
        label = (
            _thread_selector.t("thread.sort_updated")
            if self._sort_by_updated
            else _thread_selector.t("thread.sort_created")
        )
        return _thread_selector.t("thread.sort_by", field=label)

    def _get_filter_input(self) -> _thread_selector.Input:
        """Return the cached search input widget."""
        if self._filter_input is None:
            self._filter_input = self.query_one("#thread-filter", _thread_selector.Input)
        return self._filter_input

    def _filter_focus_order(self) -> list[_thread_selector.Input | _thread_selector.Checkbox]:
        """Return the cached tab order for filter controls in the side panel."""
        if self._filter_controls is None:
            filter_input = self._get_filter_input()
            sort_switch = self.query_one(f"#{_thread_selector._SORT_SWITCH_ID}", _thread_selector.Checkbox)
            relative_switch = self.query_one(f"#{_thread_selector._RELATIVE_TIME_SWITCH_ID}", _thread_selector.Checkbox)
            column_switches = [
                self.query_one(f"#{self._switch_id(key)}", _thread_selector.Checkbox)
                for key in _thread_selector._COLUMN_ORDER
            ]
            self._filter_controls = [
                filter_input,
                sort_switch,
                relative_switch,
                *column_switches,
            ]
        return self._filter_controls

    def compose(self) -> ComposeResult:
        """Compose the screen layout.

        Yields:
            Widgets for the thread selector UI.
        """
        with _thread_selector.Vertical(id="thread-selector-shell"):
            yield _thread_selector.Static(
                self._build_title(), classes="thread-selector-title", id="thread-title"
            )

            yield _thread_selector.Input(
                placeholder=_thread_selector.t("thread.filter_placeholder"),
                select_on_focus=False,
                id="thread-filter",
            )

            with _thread_selector.Horizontal(classes="thread-selector-body"):
                if self._has_initial_threads:
                    with _thread_selector.Vertical(classes="thread-table-pane"):
                        with _thread_selector.Horizontal(
                            classes="thread-list-header",
                            id="thread-header",
                        ):
                            yield _thread_selector.Static("", classes="thread-cell thread-cell-cursor")
                            sort_key = _thread_selector._active_sort_key(self._sort_by_updated)
                            for key in _thread_selector._visible_column_keys(self._columns):
                                cell = _thread_selector.Static(
                                    _thread_selector._format_header_label(key),
                                    classes=_thread_selector._header_cell_classes(
                                        key, sort_key=sort_key
                                    ),
                                    expand=key == "initial_prompt",
                                    markup=False,
                                )
                                _thread_selector._apply_column_width(cell, key, self._column_widths)
                                yield cell

                        with _thread_selector.VerticalScroll(classes="thread-list"):
                            if self._filtered_threads:
                                self._option_widgets, _ = self._create_option_widgets()
                                yield from self._option_widgets
                            else:
                                yield _thread_selector.Static(
                                    _thread_selector.Content.styled(_thread_selector.t("thread.no_threads"), "dim"),
                                    classes="thread-empty",
                                )
                else:
                    with _thread_selector.Vertical(
                        classes="thread-table-pane", id="thread-loading-container"
                    ):
                        with _thread_selector.Vertical(classes="thread-loading-overlay"):
                            yield _thread_selector.Static(_thread_selector.t("thread.loading"))

                with _thread_selector.Vertical(classes="thread-controls"):
                    yield _thread_selector.Static(_thread_selector.t("thread.options"), classes="thread-controls-title")
                    yield _thread_selector.Static(
                        _thread_selector.t("thread.options_help"),
                        classes="thread-controls-help",
                        markup=False,
                    )
                    yield _thread_selector.Checkbox(
                        self._format_sort_toggle_label(),
                        self._sort_by_updated,
                        id=_thread_selector._SORT_SWITCH_ID,
                        classes="thread-column-toggle",
                        compact=True,
                    )
                    yield _thread_selector.Checkbox(
                        _thread_selector.t("thread.relative_time"),
                        self._relative_time,
                        id=_thread_selector._RELATIVE_TIME_SWITCH_ID,
                        classes="thread-column-toggle",
                        compact=True,
                    )
                    for key in _thread_selector._COLUMN_ORDER:
                        yield _thread_selector.Checkbox(
                            _thread_selector._COLUMN_TOGGLE_LABELS[key],
                            self._columns.get(key, False),
                            id=self._switch_id(key),
                            classes="thread-column-toggle",
                            compact=True,
                        )

            yield _thread_selector.Static(
                self._build_help_text(),
                classes="thread-selector-help",
                id="thread-help",
            )

    async def on_mount(self) -> None:
        """Fetch threads, configure border for ASCII terminals, and build the list."""
        if _thread_selector.is_ascii_mode():
            container = self.query_one("#thread-selector-shell", _thread_selector.Vertical)
            colors = _thread_selector.theme.get_theme_colors(self)
            container.styles.border = ("ascii", colors.success)

        filter_input = self._get_filter_input()
        self._filter_focus_order()
        filter_input.focus()

        if self._has_initial_threads:
            self.call_after_refresh(self._scroll_selected_into_view)
            if self._current_thread:
                self._resolve_thread_url()

        if self._has_initial_threads:
            # Defer by one message cycle so Textual finishes processing
            # mount messages before we start the DB refresh.
            self.call_after_refresh(self._start_thread_load)
        else:
            # _load_threads replaces self._threads and schedules background
            # enrichment (message counts, initial prompts) after load
            # completes.  Launch immediately when there are no cached rows
            # to render.
            self.run_worker(
                self._load_threads, exclusive=True, group="thread-selector-load"
            )

    def _start_thread_load(self) -> None:
        """Launch the thread-load worker after the initial layout pass."""
        if not self.is_attached:
            return
        self.run_worker(
            self._load_threads, exclusive=True, group="thread-selector-load"
        )

    def on_input_changed(self, event: _thread_selector.Input.Changed) -> None:
        """Filter threads as user types.

        Args:
            event: The input changed event.
        """
        self._filter_text = event.value
        self._schedule_filter_and_rebuild()

    def on_input_submitted(self, event: _thread_selector.Input.Submitted) -> None:
        """Handle Enter key when filter input is focused.

        Args:
            event: The input submitted event.
        """
        event.stop()
        self.action_select()

    def on_key(self, event: Key) -> None:
        """Return focus to search when letters are typed from other controls.

        Args:
            event: The key event.
        """
        if self._confirming_delete:
            return

        filter_input = self._get_filter_input()
        if filter_input.has_focus:
            return

        character = event.character
        if not character or not character.isalpha():
            return

        filter_input.focus()
        filter_input.insert_text_at_cursor(character)
        self.set_timer(0.01, self._collapse_search_selection)
        event.stop()

    def _collapse_search_selection(self) -> None:
        """Place the search cursor at the end without an active selection."""
        filter_input = self._get_filter_input()
        filter_input.selection = type(filter_input.selection).cursor(
            len(filter_input.value)
        )

    def on_checkbox_changed(self, event: _thread_selector.Checkbox.Changed) -> None:
        """Route sort, relative-time, and column-visibility checkbox changes.

        Args:
            event: The checkbox change event.
        """
        if event.checkbox.id == _thread_selector._SORT_SWITCH_ID:
            if self._sort_by_updated == event.value:
                return
            self._sort_by_updated = event.value
            self._apply_sort()
            self._sync_selected_index()
            self._update_help_widgets()
            self._schedule_list_rebuild()

            self._persist_sort_order("updated_at" if event.value else "created_at")
            return

        if event.checkbox.id == _thread_selector._RELATIVE_TIME_SWITCH_ID:
            if self._relative_time == event.value:
                return
            self._relative_time = event.value

            from invincat_cli.model_config import save_thread_relative_time

            self.run_worker(
                _thread_selector.asyncio.to_thread(save_thread_relative_time, event.value),
                group="thread-selector-save",
            )
            self._schedule_list_rebuild()
            return

        column_key = self._switch_column_key(event.checkbox.id)
        if column_key is None or column_key not in self._columns:
            return
        if self._columns[column_key] == event.value:
            return

        self._columns[column_key] = event.value
        self._apply_sort()
        self._sync_selected_index()
        self._update_help_widgets()
        if event.value and column_key in {"messages", "initial_prompt"}:
            self._schedule_checkpoint_enrichment()

        from invincat_cli.model_config import save_thread_columns

        snapshot = dict(self._columns)
        self.run_worker(
            _thread_selector.asyncio.to_thread(save_thread_columns, snapshot),
            group="thread-selector-save",
        )
        self._schedule_list_rebuild()
