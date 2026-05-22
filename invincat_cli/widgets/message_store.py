"""Message store for virtualized chat history.

This module provides data structures and management for message virtualization,
allowing the CLI to handle large message histories efficiently by keeping only
a sliding window of widgets in the DOM while storing all message data as
lightweight dataclasses.

The approach is inspired by Textual's `Log` widget, which only keeps `N` lines
in the DOM and recreates older ones on demand.
"""

from __future__ import annotations

import logging
from typing import Any

from invincat_cli.widgets.message_data import (
    MessageData as MessageData,
)
from invincat_cli.widgets.message_data import (
    MessageType as MessageType,
)
from invincat_cli.widgets.message_data import (
    ToolStatus as ToolStatus,
)

logger = logging.getLogger(__name__)

# Fields on MessageData that callers are allowed to update via update_message().
# Prevents accidental overwriting of identity fields like id/type/timestamp.
_UPDATABLE_FIELDS: frozenset[str] = frozenset(
    {
        "content",
        "tool_args",
        "tool_status",
        "tool_output",
        "tool_expanded",
        "tool_call_id",
        "skill_expanded",
        "is_streaming",
        "height_hint",
    }
)


class MessageStore:
    """Manages message data and widget window for virtualization.

    This class stores all messages as data and manages a sliding window
    of widgets that are actually mounted in the DOM.

    Attributes:
        WINDOW_SIZE: Maximum number of widgets to keep in DOM.

            Balances DOM performance with smooth scrolling experience.
        HYDRATE_BUFFER: Number of messages to hydrate when scrolling near edge.

            Provides enough buffer to avoid visible loading pauses.
    """

    WINDOW_SIZE: int = 50
    HYDRATE_BUFFER: int = 15

    def __init__(self) -> None:
        """Initialize the message store."""
        self._messages: list[MessageData] = []
        self._visible_start: int = 0
        self._visible_end: int = 0

        # Track active streaming message - never archive this
        self._active_message_id: str | None = None

        # O(1) lookup index: tool_call_id (str) → message id (str).
        # Both the raw value and its str()-normalized form are indexed so that
        # int IDs from streaming chunks and string IDs from ToolMessages are
        # both found without a full scan.
        self._tool_call_id_index: dict[str, str] = {}

    @property
    def total_count(self) -> int:
        """Total number of messages stored."""
        return len(self._messages)

    @property
    def visible_count(self) -> int:
        """Number of messages currently visible (as widgets)."""
        return self._visible_end - self._visible_start

    @property
    def has_messages_above(self) -> bool:
        """Check if there are archived messages above the visible window."""
        return self._visible_start > 0

    @property
    def has_messages_below(self) -> bool:
        """Check if there are archived messages below the visible window."""
        return self._visible_end < len(self._messages)

    def append(self, message: MessageData) -> None:
        """Add a new message to the store.

        Args:
            message: The message data to add.
        """
        self._messages.append(message)
        self._visible_end = len(self._messages)
        self._index_tool_call_id(message)

        # After appending, visible_count legitimately exceeds WINDOW_SIZE by 1
        # (which triggers the app's prune cycle).  If it grows much further
        # before pruning runs, the DOM and store diverge — log a warning so
        # the caller can investigate (e.g. prune callback is not wired up).
        overflow = self.visible_count - self.WINDOW_SIZE
        if overflow > 5:
            logger.warning(
                "MessageStore window overflow: visible=%d WINDOW_SIZE=%d excess=%d; "
                "prune cycle may not be running",
                self.visible_count,
                self.WINDOW_SIZE,
                overflow,
            )

    def bulk_load(
        self, messages: list[MessageData]
    ) -> tuple[list[MessageData], list[MessageData]]:
        """Load many messages at once, keeping only the tail visible.

        This is optimized for thread resumption: all messages are stored as
        lightweight data, but only the last `WINDOW_SIZE` entries are marked
        visible (i.e. will need DOM widgets).

        Args:
            messages: Ordered list of message data to load.

        Returns:
            Tuple of (archived, visible) message lists.
        """
        for msg in messages:
            self._messages.append(msg)
            self._index_tool_call_id(msg)
        total = len(self._messages)

        if total <= self.WINDOW_SIZE:
            self._visible_start = 0
        else:
            self._visible_start = total - self.WINDOW_SIZE

        self._visible_end = total

        archived = self._messages[: self._visible_start]
        visible = self._messages[self._visible_start : self._visible_end]
        return archived, visible

    def get_message(self, message_id: str) -> MessageData | None:
        """Get a message by its ID.

        Args:
            message_id: The ID of the message to find.

        Returns:
            The message data, or None if not found.
        """
        for msg in self._messages:
            if msg.id == message_id:
                return msg
        return None

    def get_message_at_index(self, index: int) -> MessageData | None:
        """Get a message by its index.

        Args:
            index: The index of the message.

        Returns:
            The message data, or None if index is out of bounds.
        """
        if 0 <= index < len(self._messages):
            return self._messages[index]
        return None

    def get_message_by_tool_call_id(
        self, tool_call_id: str | int
    ) -> MessageData | None:
        """Get a TOOL message by its tool_call_id.

        Uses an O(1) index keyed by str(tool_call_id) so both int and string
        variants of the same ID resolve correctly.

        Args:
            tool_call_id: The tool_call_id to search for.

        Returns:
            The message data, or None if not found.
        """
        key = str(tool_call_id) if tool_call_id is not None else None
        if key is None:
            return None
        msg_id = self._tool_call_id_index.get(key)
        if msg_id is None:
            return None
        # Resolve the id to the actual MessageData object.
        return self.get_message(msg_id)

    def _index_tool_call_id(self, message: MessageData) -> None:
        """Add a message's tool_call_id to the O(1) index if present."""
        if message.type == MessageType.TOOL and message.tool_call_id is not None:
            key = str(message.tool_call_id)
            self._tool_call_id_index[key] = message.id

    def update_message(self, message_id: str, **updates: Any) -> bool:
        """Update a message's data.

        Only fields in `_UPDATABLE_FIELDS` may be updated. Unknown field
        names raise `ValueError` to catch typos early.

        Args:
            message_id: The ID of the message to update.
            **updates: Fields to update.

        Returns:
            True if the message was found and updated.

        Raises:
            ValueError: If any key in `updates` is not in the updatable
                allowlist.
        """
        unknown = set(updates) - _UPDATABLE_FIELDS
        if unknown:
            msg = f"Cannot update unknown or protected fields: {unknown}"
            raise ValueError(msg)

        for msg_data in self._messages:
            if msg_data.id == message_id:
                old_tool_call_key: str | None = None
                if "tool_call_id" in updates and msg_data.tool_call_id is not None:
                    old_tool_call_key = str(msg_data.tool_call_id)
                for key, value in updates.items():
                    setattr(msg_data, key, value)
                # Keep the tool_call_id index in sync when the ID is updated
                # (e.g. after re-keying a widget from index key to real UUID).
                if "tool_call_id" in updates:
                    if (
                        old_tool_call_key is not None
                        and self._tool_call_id_index.get(old_tool_call_key)
                        == msg_data.id
                    ):
                        self._tool_call_id_index.pop(old_tool_call_key, None)
                    self._index_tool_call_id(msg_data)
                return True
        return False

    def set_active_message(self, message_id: str | None) -> None:
        """Set the currently active (streaming) message.

        Active messages are never archived.

        Args:
            message_id: The ID of the active message, or None to clear.
        """
        self._active_message_id = message_id

    def is_active(self, message_id: str) -> bool:
        """Check if a message is the active streaming message.

        Args:
            message_id: The message ID to check.

        Returns:
            True if this is the active message.
        """
        return message_id == self._active_message_id

    def window_exceeded(self) -> bool:
        """Check if the visible window exceeds the maximum size.

        Returns:
            True if we should prune some widgets.
        """
        return self.visible_count > self.WINDOW_SIZE

    def get_messages_to_prune(self, count: int | None = None) -> list[MessageData]:
        """Get the oldest visible messages that should be pruned.

        Returns a contiguous run of messages from the START of the visible
        window. Stops at the active streaming message to avoid creating gaps
        in the visible window (which would desync store state from the DOM).

        Args:
            count: Number of messages to prune, or None to prune
                enough to get back to WINDOW_SIZE.

        Returns:
            List of messages to prune (remove widgets for).
        """
        if count is None:
            count = max(0, self.visible_count - self.WINDOW_SIZE)

        if count <= 0:
            return []

        to_prune: list[MessageData] = []
        idx = self._visible_start

        while len(to_prune) < count and idx < self._visible_end:
            msg = self._messages[idx]
            # Stop at the active message to keep the window contiguous.
            # Pruning past the active streaming widget would remove it from the
            # DOM while content is still being streamed into it.  This means
            # the window may temporarily stay above WINDOW_SIZE when the
            # active message is near the front — acceptable since streaming is
            # short-lived and the next prune cycle will clear the remainder.
            if msg.id == self._active_message_id:
                logger.debug(
                    "get_messages_to_prune: stopped at active message id=%s "
                    "after %d pruned (requested %d); window may remain above "
                    "WINDOW_SIZE until streaming completes",
                    msg.id,
                    len(to_prune),
                    count,
                )
                break
            to_prune.append(msg)
            idx += 1

        return to_prune

    def get_messages_to_prune_below(
        self, count: int | None = None
    ) -> list[MessageData]:
        """Get newest visible messages that should be pruned from the DOM.

        Returns a contiguous run from the END of the visible window. This is
        used after hydrating older messages while the user is far from the
        bottom, so the DOM window can remain bounded without losing the newly
        visible older messages.
        """
        if count is None:
            count = max(0, self.visible_count - self.WINDOW_SIZE)

        if count <= 0:
            return []

        to_prune: list[MessageData] = []
        idx = self._visible_end - 1

        while len(to_prune) < count and idx >= self._visible_start:
            msg = self._messages[idx]
            if msg.id == self._active_message_id:
                logger.debug(
                    "get_messages_to_prune_below: stopped at active message id=%s "
                    "after %d pruned (requested %d); window may remain above "
                    "WINDOW_SIZE until streaming completes",
                    msg.id,
                    len(to_prune),
                    count,
                )
                break
            to_prune.append(msg)
            idx -= 1

        return to_prune

    def mark_pruned(self, message_ids: list[str]) -> None:
        """Mark messages as pruned (widgets removed).

        Advances `_visible_start` past consecutive pruned messages at the front
        of the window.

        Note: ``_tool_call_id_index`` entries are intentionally **kept** for
        pruned messages.  The index is used by the widget-recreation fallback
        path (``get_message_by_tool_call_id`` → recreate widget from stored
        data) when a ToolMessage result arrives after its widget has been
        pruned.  Removing the entry here would force a blank fallback widget
        with no tool name or args.  Entries are cleared only on ``clear()``.

        Args:
            message_ids: IDs of messages that were pruned.
        """
        pruned_set = set(message_ids)
        while (
            self._visible_start < self._visible_end
            and self._messages[self._visible_start].id in pruned_set
        ):
            self._visible_start += 1

    def mark_pruned_below(self, message_ids: list[str]) -> None:
        """Mark newest visible messages as pruned from the DOM."""
        pruned_set = set(message_ids)
        while (
            self._visible_end > self._visible_start
            and self._messages[self._visible_end - 1].id in pruned_set
        ):
            self._visible_end -= 1

    def get_messages_to_hydrate(self, count: int | None = None) -> list[MessageData]:
        """Get messages above the visible window to hydrate.

        Args:
            count: Number of messages to hydrate, or None for `HYDRATE_BUFFER`.

        Returns:
            List of messages to hydrate (create widgets for), in order.
        """
        if count is None:
            count = self.HYDRATE_BUFFER

        if self._visible_start <= 0:
            return []

        hydrate_start = max(0, self._visible_start - count)
        return self._messages[hydrate_start : self._visible_start]

    def mark_hydrated(self, count: int) -> int:
        """Mark that messages above were hydrated.

        Args:
            count: Number of messages that were hydrated.

        Returns:
            Actual number of messages whose window pointer was moved.
            This may be less than *count* if there are fewer archived
            messages than requested (e.g. when called with a stale count).
        """
        if count > self._visible_start:
            logger.warning(
                "mark_hydrated called with count=%d but only %d messages are "
                "above the visible window; clamping to avoid negative _visible_start",
                count,
                self._visible_start,
            )
        actual = min(count, self._visible_start)
        self._visible_start -= actual
        return actual

    def should_hydrate_above(
        self, scroll_position: float, viewport_height: int
    ) -> bool:
        """Check if we should hydrate messages above the current view.

        Args:
            scroll_position: Current scroll Y position.
            viewport_height: Height of the viewport.

        Returns:
            True if user is scrolling near the top and we have archived messages.
        """
        if not self.has_messages_above:
            return False

        # Hydrate when within 2x viewport height of the top
        threshold = viewport_height * 2
        return scroll_position < threshold

    def should_prune_below(
        self, scroll_position: float, viewport_height: int, content_height: int
    ) -> bool:
        """Check if we should prune messages below the current view.

        Note:
            Not yet integrated into the scroll handler. Intended for future
            pruning of messages below the viewport when the user scrolls far up.

        Args:
            scroll_position: Current scroll Y position.
            viewport_height: Height of the viewport.
            content_height: Total height of all content.

        Returns:
            True if we have too many widgets and bottom ones are far from view.
        """
        if self.visible_count <= self.WINDOW_SIZE:
            return False

        # Only prune if user is far from the bottom
        distance_from_bottom = content_height - scroll_position - viewport_height
        threshold = viewport_height * 3
        return distance_from_bottom > threshold

    def clear(self) -> None:
        """Clear all messages."""
        self._messages.clear()
        self._visible_start = 0
        self._visible_end = 0
        self._active_message_id = None
        self._tool_call_id_index.clear()

    def get_visible_range(self) -> tuple[int, int]:
        """Get the range of visible message indices.

        Returns:
            Tuple of (start_index, end_index).
        """
        return (self._visible_start, self._visible_end)

    def get_all_messages(self) -> list[MessageData]:
        """Get all stored messages.

        Returns:
            List of all message data (shallow copy).
        """
        return list(self._messages)

    def get_visible_messages(self) -> list[MessageData]:
        """Get messages in the visible window.

        Returns:
            List of visible message data.
        """
        return self._messages[self._visible_start : self._visible_end]
