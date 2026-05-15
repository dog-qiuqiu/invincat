"""Data loading and filtering helpers for the thread selector."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from invincat_cli.widgets import thread_selector as _thread_selector

if TYPE_CHECKING:
    from invincat_cli.sessions import ThreadInfo

logger = logging.getLogger(__name__)


class ThreadSelectorDataMixin:
    """Handle filtering, column sizing, thread loading, and URL enrichment."""

    def _update_filtered_list(self) -> None:
        """Update filtered threads based on search text using fuzzy matching."""
        query = self._filter_text.strip()
        if not query:
            self._filtered_threads = list(self._threads)
            self._apply_sort()
            self._sync_selected_index()
            self._column_widths = self._compute_column_widths()
            return

        tokens = query.split()
        try:
            matchers = [_thread_selector.Matcher(token, case_sensitive=False) for token in tokens]
            scored: list[tuple[float, ThreadInfo]] = []
            for thread in self._threads:
                search_text = self._get_search_text(thread)
                scores = [matcher.match(search_text) for matcher in matchers]
                if all(score > 0 for score in scores):
                    scored.append((min(scores), thread))
        except Exception:
            logger.warning(
                "Fuzzy matcher failed for query %r, falling back to full list",
                query,
                exc_info=True,
            )
            self._filtered_threads = list(self._threads)
            self._apply_sort()
            self._sync_selected_index()
            self._column_widths = self._compute_column_widths()
            return

        sort_key = _thread_selector._active_sort_key(self._sort_by_updated)
        self._filtered_threads = [
            thread
            for _, thread in sorted(
                scored,
                key=lambda item: (
                    item[0],
                    item[1].get(sort_key) or "",
                    item[1].get("updated_at") or "",
                    item[1]["thread_id"],
                ),
                reverse=True,
            )
        ]
        self._selected_index = 0
        self._column_widths = self._compute_column_widths()

    def _compute_column_widths(self) -> dict[str, int | None]:
        """Return effective widths for the current table state.

        Textual's `width: auto` computes per-widget widths, so this method
        derives shared widths from the visible data instead. Also populates
        `self._cell_text` as a side effect so that `ThreadOption.compose()` can
        reuse the formatted strings.

        Returns:
            Dict mapping column keys to their effective cell widths, with
                `None` for flex columns.
        """
        visible_keys = _thread_selector._visible_column_keys(self._columns)
        visible = frozenset(visible_keys)
        fingerprint = tuple(
            (t["thread_id"], t.get("latest_checkpoint_id"))
            for t in self._filtered_threads
        )

        if _thread_selector._column_widths_cache is not None:
            fp, vis, rel, cached_widths = _thread_selector._column_widths_cache
            if (
                fp == fingerprint
                and vis == visible
                and rel == self._relative_time
                and self._cell_text
            ):
                return dict(cached_widths)

        # Pre-format every visible cell in one pass.
        cell_text: dict[tuple[str, str], str] = {}
        for thread in self._filtered_threads:
            tid = thread["thread_id"]
            for key in visible_keys:
                cell_text[tid, key] = _thread_selector._format_column_value(
                    thread, key, relative_time=self._relative_time
                )
        self._cell_text = cell_text

        # Derive auto-widths from the pre-formatted values.
        widths = dict(_thread_selector._COLUMN_WIDTHS)
        for key in _thread_selector._AUTO_WIDTH_COLUMNS:
            if key not in visible:
                continue
            header_len = _thread_selector.cell_len(_thread_selector._format_header_label(key))
            max_cell = max(
                (
                    _thread_selector.cell_len(cell_text[t["thread_id"], key])
                    for t in self._filtered_threads
                ),
                default=0,
            )
            widths[key] = max(header_len, max_cell) + _thread_selector._CELL_PADDING_RIGHT

        _thread_selector._column_widths_cache = (fingerprint, visible, self._relative_time, widths)
        return widths

    @staticmethod
    def _get_search_text(thread: ThreadInfo) -> str:
        """Build searchable text from thread fields.

        The result is capped at `_thread_selector._MAX_SEARCH_TEXT_LEN` characters so that
        Textual's fuzzy `_thread_selector.Matcher` (which uses recursive backtracking) does
        not hit exponential performance on long initial prompts with
        repeated characters.

        Args:
            thread: Thread metadata.

        Returns:
            Concatenated searchable string, truncated to a safe length.
        """
        parts = [
            thread["thread_id"],
            thread.get("agent_name") or "",
            thread.get("git_branch") or "",
            thread.get("initial_prompt") or "",
        ]
        text = " ".join(parts)
        return text[:_thread_selector._MAX_SEARCH_TEXT_LEN]

    def _schedule_filter_and_rebuild(self) -> None:
        """Queue a filter + rebuild, coalescing rapid keystrokes."""
        self.run_worker(
            self._filter_and_build,
            exclusive=True,
            group="thread-selector-render",
        )

    async def _filter_and_build(self) -> None:
        """Run fuzzy filtering in a thread then rebuild the list."""
        query = self._filter_text.strip()
        threads = list(self._threads)
        sort_by_updated = self._sort_by_updated

        filtered = await _thread_selector.asyncio.to_thread(
            self._compute_filtered, query, threads, sort_by_updated
        )
        self._filtered_threads = filtered
        if query:
            self._selected_index = 0
        else:
            self._sync_selected_index()
        self._column_widths = self._compute_column_widths()
        await self._build_list(recompute_widths=False)

    @staticmethod
    def _compute_filtered(
        query: str,
        threads: list[ThreadInfo],
        sort_by_updated: bool,
    ) -> list[ThreadInfo]:
        """Compute filtered thread list off the main thread.

        Args:
            query: Current search query text.
            threads: Full thread list snapshot.
            sort_by_updated: Whether to sort by `updated_at`.

        Returns:
            Filtered and sorted thread list.
        """
        sort_key = _thread_selector._active_sort_key(sort_by_updated)

        if not query:
            result = list(threads)
            result.sort(key=lambda t: t.get(sort_key) or "", reverse=True)
            return result

        tokens = query.split()
        try:
            matchers = [_thread_selector.Matcher(token, case_sensitive=False) for token in tokens]
            scored: list[tuple[float, ThreadInfo]] = []
            for thread in threads:
                search_text = ThreadSelectorDataMixin._get_search_text(thread)
                scores = [matcher.match(search_text) for matcher in matchers]
                if all(score > 0 for score in scores):
                    scored.append((min(scores), thread))
        except Exception:
            logger.warning(
                "Fuzzy matcher failed for query %r, falling back to full list",
                query,
                exc_info=True,
            )
            result = list(threads)
            result.sort(key=lambda t: t.get(sort_key) or "", reverse=True)
            return result

        return [
            thread
            for _, thread in sorted(
                scored,
                key=lambda item: (
                    item[0],
                    item[1].get(sort_key) or "",
                    item[1].get("updated_at") or "",
                    item[1]["thread_id"],
                ),
                reverse=True,
            )
        ]

    def _schedule_list_rebuild(self) -> None:
        """Queue a list rebuild, coalescing rapid updates."""
        self.run_worker(
            self._build_list,
            exclusive=True,
            group="thread-selector-render",
        )

    def _pending_checkpoint_fields(self) -> tuple[bool, bool]:
        """Return which visible checkpoint-derived fields still need loading."""
        load_counts = self._columns.get("messages", False) and any(
            "message_count" not in thread for thread in self._threads
        )
        load_prompts = self._columns.get("initial_prompt", False) and any(
            "initial_prompt" not in thread for thread in self._threads
        )
        return load_counts, load_prompts

    async def _populate_visible_checkpoint_details(self) -> tuple[bool, bool]:
        """Load any still-missing checkpoint-derived fields for visible columns.

        Returns:
            Tuple indicating whether message counts and prompts were requested.
        """
        from invincat_cli.sessions import populate_thread_checkpoint_details

        load_counts, load_prompts = self._pending_checkpoint_fields()
        if not load_counts and not load_prompts:
            return False, False

        await populate_thread_checkpoint_details(
            self._threads,
            include_message_count=load_counts,
            include_initial_prompt=load_prompts,
        )
        return load_counts, load_prompts

    def _schedule_checkpoint_enrichment(self) -> None:
        """Schedule one checkpoint-enrichment pass for missing row fields."""
        has_missing_counts, has_missing_prompts = self._pending_checkpoint_fields()
        if not has_missing_counts and not has_missing_prompts:
            return
        self.run_worker(
            self._load_checkpoint_details,
            exclusive=True,
            group="thread-selector-checkpoints",
        )

    @staticmethod
    def _threads_match(old: list[ThreadInfo], new: list[ThreadInfo]) -> bool:
        """Check whether two thread lists have the same IDs and checkpoints in order.

        Args:
            old: Previous thread list.
            new: Fresh thread list.

        Returns:
            True if both lists have identical thread/checkpoint ID pairs.
        """
        if len(old) != len(new):
            return False
        for a, b in zip(old, new, strict=True):
            if a["thread_id"] != b["thread_id"]:
                return False
            if a.get("latest_checkpoint_id") != b.get("latest_checkpoint_id"):
                return False
        return True

    async def _load_threads(self) -> None:
        """Load thread rows first, then kick off background enrichment."""
        from invincat_cli.sessions import (
            apply_cached_thread_initial_prompts,
            apply_cached_thread_message_counts,
            list_threads,
        )

        old_threads = list(self._threads)

        try:
            limit = self._thread_limit
            if limit is None:
                from invincat_cli.sessions import get_thread_limit

                limit = get_thread_limit()
            sort_by = "updated" if self._sort_by_updated else "created"
            self._threads = await list_threads(
                limit=limit, include_message_count=False, sort_by=sort_by
            )
        except (OSError, _thread_selector.sqlite3.Error) as exc:
            logger.exception("Failed to load threads for thread selector")
            await self._show_mount_error(str(exc))
            return
        except Exception as exc:
            logger.exception("Unexpected error loading threads for thread selector")
            await self._show_mount_error(str(exc))
            return

        apply_cached_thread_message_counts(self._threads)
        apply_cached_thread_initial_prompts(self._threads)
        if not self._has_initial_threads:
            try:
                await self._populate_visible_checkpoint_details()
            except (OSError, _thread_selector.sqlite3.Error):
                logger.debug(
                    "Could not preload checkpoint details for thread selector",
                    exc_info=True,
                )
            except Exception:
                logger.warning(
                    "Unexpected error preloading checkpoint details "
                    "for thread selector",
                    exc_info=True,
                )
        self._update_filtered_list()
        self._sync_selected_index()

        if not self._has_initial_threads:
            await self._build_table_pane()
            self._has_initial_threads = True
        elif self._option_widgets and self._threads_match(
            old_threads, self._filtered_threads
        ):
            for widget, thread in zip(
                self._option_widgets,
                self._filtered_threads,
                strict=True,
            ):
                widget.thread = thread
            self._refresh_cell_labels()
        else:
            await self._build_list()

        self._schedule_checkpoint_enrichment()

        if self._current_thread:
            self._resolve_thread_url()

    async def _load_checkpoint_details(self) -> None:
        """Populate checkpoint-derived thread fields in one background pass."""
        if not self._threads:
            return

        try:
            _, load_prompts = await self._populate_visible_checkpoint_details()
        except (OSError, _thread_selector.sqlite3.Error):
            logger.debug(
                "Could not load checkpoint details for thread selector",
                exc_info=True,
            )
            return
        except Exception:
            logger.warning(
                "Unexpected error loading checkpoint details for thread selector",
                exc_info=True,
            )
            return

        if load_prompts and self._filter_text.strip():
            # Prompts may affect fuzzy match results; rebuild the filtered
            # list but preserve the user's cursor position.
            saved_tid = (
                self._filtered_threads[self._selected_index]["thread_id"]
                if self._selected_index < len(self._filtered_threads)
                else None
            )
            self._update_filtered_list()
            if saved_tid is not None:
                for i, thread in enumerate(self._filtered_threads):
                    if thread["thread_id"] == saved_tid:
                        self._selected_index = i
                        break
            self._schedule_list_rebuild()
        else:
            self._refresh_cell_labels()

    def _refresh_cell_labels(self) -> None:
        """Update visible cell text in-place without rebuilding the DOM."""
        visible_keys = _thread_selector._visible_column_keys(self._columns)

        # Recompute because thread data may have changed since
        # _compute_column_widths populated the cache.
        cell_text: dict[tuple[str, str], str] = {}
        for thread in self._filtered_threads:
            tid = thread["thread_id"]
            for key in visible_keys:
                cell_text[tid, key] = _thread_selector._format_column_value(
                    thread, key, relative_time=self._relative_time
                )
        self._cell_text = cell_text

        for widget in self._option_widgets:
            tid = widget.thread_id
            for key in visible_keys:
                try:
                    cell = widget.query_one(f".thread-cell-{key}", _thread_selector.Static)
                except _thread_selector.NoMatches:
                    continue
                cell.update(cell_text[tid, key])

    def _resolve_thread_url(self) -> None:
        """Start exclusive background worker to resolve LangSmith thread URL."""
        self.run_worker(
            self._fetch_thread_url, exclusive=True, group="thread-selector-url"
        )

    async def _fetch_thread_url(self) -> None:
        """Resolve the LangSmith URL and update the title with a clickable link."""
        if not self._current_thread:
            return
        try:
            thread_url = await _thread_selector.asyncio.wait_for(
                _thread_selector.asyncio.to_thread(_thread_selector.build_langsmith_thread_url, self._current_thread),
                timeout=_thread_selector._URL_FETCH_TIMEOUT,
            )
        except (TimeoutError, OSError):
            logger.debug(
                "Could not resolve LangSmith thread URL for '%s'",
                self._current_thread,
                exc_info=True,
            )
            return
        except Exception:
            logger.debug(
                "Unexpected error resolving LangSmith thread URL for '%s'",
                self._current_thread,
                exc_info=True,
            )
            return
        if thread_url:
            try:
                title_widget = self.query_one("#thread-title", _thread_selector.Static)
                title_widget.update(self._build_title(thread_url))
            except _thread_selector.NoMatches:
                logger.debug(
                    "Title widget #thread-title not found; "
                    "thread selector may have been dismissed during URL resolution"
                )
