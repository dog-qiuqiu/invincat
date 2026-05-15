"""Textual messages emitted by background server startup."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from textual.message import Message

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel


class ServerReady(Message):
    """Posted by the background server-startup worker on success."""

    def __init__(
        self,
        agent: Any,
        server_proc: Any,
        mcp_server_info: list[Any] | None,
        model: BaseChatModel | None = None,
    ) -> None:
        super().__init__()
        self.agent = agent
        self.server_proc = server_proc
        self.mcp_server_info = mcp_server_info
        self.model = model


class ServerStartFailed(Message):
    """Posted by the background server-startup worker on failure."""

    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error
