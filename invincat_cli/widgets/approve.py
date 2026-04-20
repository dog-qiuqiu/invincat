"""Approve widget for plan confirmation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, ClassVar, Literal, TypedDict

from textual.binding import Binding, BindingType
from textual.containers import Container, Vertical
from textual.content import Content
from textual.message import Message
from textual.widgets import Markdown, Static

if TYPE_CHECKING:
    import asyncio

    from textual import events
    from textual.app import ComposeResult

from invincat_cli import theme
from invincat_cli.config import get_glyphs, is_ascii_mode
from invincat_cli.i18n import t

logger = logging.getLogger(__name__)


class ApproveWidget(Container):
    """Interactive widget for approving a plan.

    Displays a todo list and asks the user to confirm (y) or reject (n).
    """

    can_focus = True
    can_focus_children = True

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("y", "approve", "Approve", show=True),
        Binding("n", "reject", "Reject", show=True),
        Binding("escape", "reject", "Reject", show=False),
    ]

    class Approved(Message):
        """Message sent when user approves the plan."""

        def __init__(self) -> None:  # noqa: D107
            super().__init__()

    class Rejected(Message):
        """Message sent when user rejects the plan."""

        def __init__(self) -> None:  # noqa: D107
            super().__init__()

    def __init__(  # noqa: D107
        self,
        todos: list[dict[str, Any]],
        id: str | None = None,  # noqa: A002
        **kwargs: Any,
    ) -> None:
        super().__init__(id=id or "approve-widget", classes="approve-widget", **kwargs)
        self._todos = todos
        self._future: asyncio.Future[ApproveResult] | None = None
        self._submitted = False

    def set_future(self, future: asyncio.Future[ApproveResult]) -> None:
        """Set the future to resolve when user answers."""
        self._future = future

    def compose(self) -> ComposeResult:  # noqa: D102
        glyphs = get_glyphs()
        yield Static(
            f"{glyphs.cursor} Plan Ready for Approval",
            classes="approve-title",
        )
        yield Static("")

        with Vertical(classes="approve-todos"):
            for i, todo in enumerate(self._todos):
                content = todo.get("content", "")
                status = todo.get("status", "pending")
                status_icon = "○" if status == "pending" else "◐" if status == "in_progress" else "●"
                yield Static(
                    f"{status_icon} {i + 1}. {content}",
                    classes=f"approve-todo approve-todo-{status}",
                )

        yield Static("")
        yield Static(
            t("approve.prompt"),
            classes="approve-prompt",
        )

    def on_mount(self) -> None:  # noqa: D102
        self.focus()

    def action_approve(self) -> None:  # noqa: D102
        if self._submitted:
            return
        self._submitted = True
        self.post_message(self.Approved())
        if self._future and not self._future.done():
            self._future.set_result({"type": "approved"})

    def action_reject(self) -> None:  # noqa: D102
        if self._submitted:
            return
        self._submitted = True
        self.post_message(self.Rejected())
        if self._future and not self._future.done():
            self._future.set_result({"type": "rejected"})

    def on_key(self, event: events.Key) -> None:  # noqa: D102
        if event.key.lower() == "y":
            self.action_approve()
        elif event.key.lower() == "n":
            self.action_reject()
        event.stop()


ApproveResult = Literal["approved", "rejected"]
"""Result type for approve widget."""


class ApproveWidgetResult(TypedDict):
    """Result from approve widget."""

    type: ApproveResult
