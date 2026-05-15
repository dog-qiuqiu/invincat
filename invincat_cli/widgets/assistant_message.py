"""Assistant message widget."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.containers import Vertical
from textual.widgets import Static

from invincat_cli.widgets import messages as _messages
from invincat_cli.widgets.message_styles import ASSISTANT_MESSAGE_CSS

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.widgets import Markdown
    from textual.widgets._markdown import MarkdownStream


class AssistantMessage(_messages._TimestampClickMixin, Vertical):
    """Widget displaying an assistant message with markdown support.

    Uses MarkdownStream for smoother streaming instead of re-rendering
    the full content on each update.
    """

    DEFAULT_CSS = ASSISTANT_MESSAGE_CSS

    def __init__(self, content: str = "", **kwargs: Any) -> None:
        """Initialize an assistant message.

        Args:
            content: Initial markdown content
            **kwargs: Additional arguments passed to parent
        """
        super().__init__(**kwargs)
        self._content = content
        self._reasoning_content = ""
        self._markdown: Markdown | None = None
        self._reasoning_widget: Static | None = None
        self._stream: MarkdownStream | None = None

    def compose(self) -> ComposeResult:  # noqa: PLR6301  # Textual widget method convention
        """Compose the assistant message layout.

        Yields:
            Markdown widget for rendering assistant content.
        """
        from textual.widgets import Markdown

        yield Markdown("", id="assistant-content")
        yield Static("", id="assistant-reasoning", classes="assistant-reasoning")

    def on_mount(self) -> None:
        """Store reference to markdown widget."""
        from textual.widgets import Markdown

        self._markdown = self.query_one("#assistant-content", Markdown)
        self._reasoning_widget = self.query_one("#assistant-reasoning", Static)

    def _get_markdown(self) -> Markdown:
        """Get the markdown widget, querying if not cached.

        Returns:
            The Markdown widget for this message.
        """
        if self._markdown is None:
            from textual.widgets import Markdown

            self._markdown = self.query_one("#assistant-content", Markdown)
        return self._markdown

    def _ensure_stream(self) -> MarkdownStream:
        """Ensure the markdown stream is initialized.

        Returns:
            The MarkdownStream instance for streaming content.
        """
        if self._stream is None:
            from textual.widgets import Markdown

            self._stream = Markdown.get_stream(self._get_markdown())
        return self._stream

    async def append_content(self, text: str) -> None:
        """Append content to the message (for streaming).

        Uses MarkdownStream for smoother rendering instead of re-rendering
        the full content on each chunk.

        Args:
            text: Text to append
        """
        if not text:
            return
        self._content += text
        stream = self._ensure_stream()
        await stream.write(text)

    async def append_reasoning(self, text: str) -> None:
        """Append reasoning text in a muted, separate display area.

        Args:
            text: Reasoning text chunk to append.
        """
        if not text:
            return
        self._reasoning_content += text
        if self._reasoning_widget is None:
            self._reasoning_widget = self.query_one("#assistant-reasoning", Static)
        self._reasoning_widget.display = True
        self._reasoning_widget.update(self._reasoning_content)

    async def write_initial_content(self) -> None:
        """Write initial content if provided at construction time."""
        if self._content:
            stream = self._ensure_stream()
            await stream.write(self._content)

    async def stop_stream(self) -> None:
        """Stop the streaming and finalize the content."""
        if self._stream is not None:
            await self._stream.stop()
            self._stream = None

    async def set_content(self, content: str) -> None:
        """Set the full message content.

        This stops any active stream and sets content directly.

        Args:
            content: The markdown content to display
        """
        await self.stop_stream()
        self._content = content
        if self._markdown:
            await self._markdown.update(content)
