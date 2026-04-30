"""WeCom-only file sending tool and payload helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ModelRequest, ModelResponse
    from langgraph.prebuilt.tool_node import ToolCallRequest


WECOM_CONTEXT_FLAG = "wecom_enabled"
WECOM_FILE_TOOL_NAME = "send_wecom_file"
WECOM_FILE_REQUEST_TYPE = "wecom_send_file"
WECOM_FILE_MAX_BYTES = 20 * 1024 * 1024


WECOM_FILE_TOOL_DESCRIPTION = """Send a local file as an attachment to the current WeCom user.

Use this tool whenever the WeCom user asks you to create or provide something as
a file, attachment, document, report, spreadsheet, archive, log, or other
downloadable artifact. This includes casual wording such as "发我", "发给我",
"传给我", "给我个文档", "写个文档发我", "导出", "打包", "send me the file",
or "share the document".

Expected workflow:
1. Create the requested artifact as a real local file inside the current project.
2. Call send_wecom_file(path) with that file path.
3. After the tool succeeds, briefly tell the user which file was sent.

Rules:
- Do not merely say that a file was created or paste the whole file content in chat when the user asked for it to be sent.
- Pass a local file path, not a directory.
- The file must already exist and be no larger than 20 MB.
- Prefer sending generated reports, markdown docs, spreadsheets, archives, logs, or other deliverables.
- Do not send secrets, credentials, environment files, private config files, or files the user did not request.
"""


def _is_wecom_context(runtime: Any) -> bool:  # noqa: ANN401
    ctx = getattr(runtime, "context", None)
    return isinstance(ctx, dict) and bool(ctx.get(WECOM_CONTEXT_FLAG))


def _tool_name(tool_obj: Any) -> str:  # noqa: ANN401
    if hasattr(tool_obj, "name"):
        return str(getattr(tool_obj, "name", ""))
    if isinstance(tool_obj, dict):
        return str(tool_obj.get("name", ""))
    return ""


def parse_wecom_file_request(content: Any) -> dict[str, Any] | None:  # noqa: ANN401
    """Parse a send_wecom_file ToolMessage payload."""
    if isinstance(content, list):
        text_parts = [
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        raw = "\n".join(text_parts).strip()
    else:
        raw = str(content or "").strip()
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("type") != WECOM_FILE_REQUEST_TYPE:
        return None
    return payload


class WeComFileMiddleware(AgentMiddleware):
    """Expose send_wecom_file only during /wecombot turns."""

    def __init__(self, *, allowed_root: str | Path) -> None:
        super().__init__()
        self._allowed_root = Path(allowed_root).expanduser().resolve()

        @tool(description=WECOM_FILE_TOOL_DESCRIPTION)
        def _send_wecom_file(
            path: str,
            tool_call_id: Annotated[str, InjectedToolCallId],
        ) -> str:
            """Request sending a local file to the current WeCom user."""
            resolved = self._resolve_allowed_file(path)
            stat = resolved.stat()
            payload = {
                "type": WECOM_FILE_REQUEST_TYPE,
                "path": str(resolved),
                "filename": resolved.name,
                "size": stat.st_size,
                "tool_call_id": tool_call_id,
            }
            return json.dumps(payload, ensure_ascii=False)

        _send_wecom_file.name = WECOM_FILE_TOOL_NAME
        self.tools = [_send_wecom_file]

    def _resolve_allowed_file(self, path: str) -> Path:
        raw = Path(path).expanduser()
        candidate = raw if raw.is_absolute() else self._allowed_root / raw
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self._allowed_root)
        except ValueError as exc:
            msg = (
                "send_wecom_file can only send files inside the current project "
                f"directory: {self._allowed_root}"
            )
            raise ValueError(msg) from exc
        if not resolved.is_file():
            raise ValueError(f"File does not exist or is not a regular file: {resolved}")
        size = resolved.stat().st_size
        if size <= 0:
            raise ValueError("Cannot send an empty file")
        if size > WECOM_FILE_MAX_BYTES:
            raise ValueError("File is larger than the WeCom 20 MB limit")
        return resolved

    def _reject_if_disabled(self, request: "ToolCallRequest") -> ToolMessage | None:
        if request.tool_call.get("name") != WECOM_FILE_TOOL_NAME:
            return None
        if _is_wecom_context(getattr(request, "runtime", None)):
            return None
        return ToolMessage(
            content="send_wecom_file is only available during /wecombot turns.",
            name=WECOM_FILE_TOOL_NAME,
            tool_call_id=request.tool_call["id"],
            status="error",
        )

    def wrap_model_call(
        self,
        request: "ModelRequest",
        handler: "Callable[[ModelRequest], ModelResponse]",
    ) -> "ModelResponse":
        tools = list(getattr(request, "tools", []))
        if not _is_wecom_context(request.runtime):
            tools = [t for t in tools if _tool_name(t) != WECOM_FILE_TOOL_NAME]
        return handler(request.override(tools=tools))

    async def awrap_model_call(
        self,
        request: "ModelRequest",
        handler: "Callable[[ModelRequest], Awaitable[ModelResponse]]",
    ) -> "ModelResponse":
        tools = list(getattr(request, "tools", []))
        if not _is_wecom_context(request.runtime):
            tools = [t for t in tools if _tool_name(t) != WECOM_FILE_TOOL_NAME]
        return await handler(request.override(tools=tools))

    def wrap_tool_call(
        self,
        request: "ToolCallRequest",
        handler: "Callable[[ToolCallRequest], ToolMessage]",
    ) -> ToolMessage:
        if (rejection := self._reject_if_disabled(request)) is not None:
            return rejection
        return handler(request)

    async def awrap_tool_call(
        self,
        request: "ToolCallRequest",
        handler: "Callable[[ToolCallRequest], Awaitable[ToolMessage]]",
    ) -> ToolMessage:
        if (rejection := self._reject_if_disabled(request)) is not None:
            return rejection
        return await handler(request)
