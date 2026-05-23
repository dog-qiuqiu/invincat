"""Message dataclasses and widget serialization helpers."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from time import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from textual.widget import Widget

logger = logging.getLogger(__name__)


class MessageType(StrEnum):
    """Types of messages in the chat."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    SKILL = "skill"
    ERROR = "error"
    APP = "app"
    SUMMARIZATION = "summarization"
    DIFF = "diff"


class ToolStatus(StrEnum):
    """Status of a tool call."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    REJECTED = "rejected"
    SKIPPED = "skipped"


@dataclass
class MessageData:
    """In-memory message data for chat history and restore."""

    type: MessageType
    content: str
    id: str = field(default_factory=lambda: f"msg-{uuid.uuid4().hex[:8]}")
    timestamp: float = field(default_factory=time)

    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None
    tool_status: ToolStatus | None = None
    tool_output: str | None = None
    tool_expanded: bool = False
    tool_call_id: str | int | None = None

    diff_file_path: str | None = None

    skill_name: str | None = None
    skill_description: str | None = None
    skill_source: str | None = None
    skill_args: str | None = None
    skill_body: str | None = None
    skill_expanded: bool = False

    is_streaming: bool = False
    height_hint: int | None = None

    def __post_init__(self) -> None:
        if self.type == MessageType.TOOL and not self.tool_name:
            msg = "TOOL messages must have a tool_name"
            raise ValueError(msg)
        if self.type == MessageType.SKILL and not self.skill_name:
            msg = "SKILL messages must have a skill_name"
            raise ValueError(msg)

    def to_widget(self) -> Widget:
        """Recreate a widget from this message data."""
        from invincat_cli.widgets.messages import (
            AppMessage,
            AssistantMessage,
            DiffMessage,
            ErrorMessage,
            SkillMessage,
            SummarizationMessage,
            ToolCallMessage,
            UserMessage,
        )

        match self.type:
            case MessageType.USER:
                return UserMessage(self.content, id=self.id)
            case MessageType.ASSISTANT:
                return AssistantMessage(self.content, id=self.id)
            case MessageType.TOOL:
                widget = ToolCallMessage(
                    self.tool_name or "unknown",
                    self.tool_args,
                    tool_call_id=self.tool_call_id,
                    args_finalized=True,
                    id=self.id,
                )
                widget._deferred_status = self.tool_status
                widget._deferred_output = self.tool_output
                widget._deferred_expanded = self.tool_expanded
                return widget
            case MessageType.SKILL:
                widget = SkillMessage(
                    skill_name=self.skill_name or "unknown",
                    description=self.skill_description or "",
                    source=self.skill_source or "",
                    body=self.skill_body or "",
                    args=self.skill_args or "",
                    id=self.id,
                )
                widget._deferred_expanded = self.skill_expanded
                return widget
            case MessageType.ERROR:
                return ErrorMessage(self.content, id=self.id)
            case MessageType.APP:
                return AppMessage(self.content, id=self.id)
            case MessageType.SUMMARIZATION:
                return SummarizationMessage(self.content, id=self.id)
            case MessageType.DIFF:
                return DiffMessage(
                    self.content,
                    file_path=self.diff_file_path or "",
                    id=self.id,
                )
            case _:
                logger.warning(
                    "Unknown MessageType %r for message %s, falling back to AppMessage",
                    self.type,
                    self.id,
                )
                return AppMessage(self.content, id=self.id)

    @classmethod
    def from_widget(cls, widget: Widget) -> MessageData:
        """Create MessageData from an existing widget."""
        from invincat_cli.widgets.messages import (
            AppMessage,
            AssistantMessage,
            DiffMessage,
            ErrorMessage,
            SkillMessage,
            SummarizationMessage,
            ToolCallMessage,
            UserMessage,
        )

        widget_id = widget.id or f"msg-{uuid.uuid4().hex[:8]}"

        if isinstance(widget, SkillMessage):
            return cls(
                type=MessageType.SKILL,
                content="",
                id=widget_id,
                skill_name=widget._skill_name,
                skill_description=widget._description,
                skill_source=widget._source,
                skill_body=widget._body,
                skill_args=widget._args,
                skill_expanded=widget._expanded,
            )

        if isinstance(widget, UserMessage):
            return cls(type=MessageType.USER, content=widget._content, id=widget_id)

        if isinstance(widget, AssistantMessage):
            return cls(
                type=MessageType.ASSISTANT,
                content=widget._content,
                id=widget_id,
                is_streaming=widget._stream is not None,
            )

        if isinstance(widget, ToolCallMessage):
            tool_status: ToolStatus | None = None
            if widget._status:
                try:
                    tool_status = ToolStatus(widget._status)
                except ValueError:
                    logger.warning(
                        "Unknown tool status %r for widget %s",
                        widget._status,
                        widget_id,
                    )

            return cls(
                type=MessageType.TOOL,
                content="",
                id=widget_id,
                tool_name=widget._tool_name,
                tool_args=widget._args,
                tool_status=tool_status,
                tool_output=widget._output,
                tool_expanded=widget._expanded,
                tool_call_id=widget._tool_call_id,
            )

        if isinstance(widget, ErrorMessage):
            return cls(type=MessageType.ERROR, content=widget._content, id=widget_id)

        if isinstance(widget, DiffMessage):
            return cls(
                type=MessageType.DIFF,
                content=widget._diff_content,
                id=widget_id,
                diff_file_path=widget._file_path,
            )

        if isinstance(widget, SummarizationMessage):
            return cls(
                type=MessageType.SUMMARIZATION,
                content=str(widget._content),
                id=widget_id,
            )

        if isinstance(widget, AppMessage):
            return cls(type=MessageType.APP, content=str(widget._content), id=widget_id)

        logger.warning(
            "Unknown widget type %s (id=%s), storing as APP message",
            type(widget).__name__,
            widget_id,
        )
        return cls(
            type=MessageType.APP,
            content=f"[Unknown widget: {type(widget).__name__}]",
            id=widget_id,
        )
