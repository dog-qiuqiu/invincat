"""Message store for chat history.

This module stores all messages as lightweight dataclasses and keeps the
visible range aligned with the full message list.
"""

from __future__ import annotations

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
    """Manages message data for the chat transcript."""

    def __init__(self) -> None:
        """Initialize the message store."""
        self._messages: list[MessageData] = []
        self._visible_start: int = 0
        self._visible_end: int = 0

        # Track active streaming message.
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

    def append(self, message: MessageData) -> None:
        """Add a new message to the store.

        Args:
            message: The message data to add.
        """
        self._messages.append(message)
        self._visible_end = len(self._messages)
        self._index_tool_call_id(message)

    def insert_after(self, anchor_id: str | None, message: MessageData) -> None:
        """Insert a message immediately after an existing message.

        Falls back to append when the anchor is missing.

        Args:
            anchor_id: ID of the message to insert after.
            message: The message data to add.
        """
        if anchor_id is None:
            self.append(message)
            return

        for index, existing in enumerate(self._messages):
            if existing.id == anchor_id:
                self._messages.insert(index + 1, message)
                self._visible_end = len(self._messages)
                self._index_tool_call_id(message)
                return

        self.append(message)

    def bulk_load(
        self, messages: list[MessageData]
    ) -> tuple[list[MessageData], list[MessageData]]:
        """Load many messages at once, keeping all of them visible.

        Args:
            messages: Ordered list of message data to load.

        Returns:
            Tuple of (archived, visible) message lists.
        """
        for msg in messages:
            self._messages.append(msg)
            self._index_tool_call_id(msg)
        total = len(self._messages)
        self._visible_start = 0
        self._visible_end = total

        archived: list[MessageData] = []
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
