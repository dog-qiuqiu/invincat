from __future__ import annotations

import json
from types import SimpleNamespace

from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from invincat_cli.wecom_file import (
    WECOM_CONTEXT_FLAG,
    WECOM_FILE_MAX_BYTES,
    WECOM_FILE_TOOL_NAME,
    WeComFileMiddleware,
    parse_wecom_file_request,
)


def test_wecom_ping_frame_uses_official_ping_command() -> None:
    from invincat_cli.app import _wecom_build_ping_frame

    frame = _wecom_build_ping_frame()

    assert frame["cmd"] == "ping"
    assert frame["headers"]["req_id"].startswith("ping_")
    assert frame["body"] == {}


def test_wecom_file_frame_uses_active_send_when_chatid_present() -> None:
    from invincat_cli.app import _wecom_build_file_frame

    frame = {"headers": {"req_id": "inbound-1"}, "body": {"chatid": "chat-1"}}

    payload = _wecom_build_file_frame(frame, "media-1")

    assert payload["cmd"] == "aibot_send_msg"
    assert payload["headers"]["req_id"].startswith("aibot_send_msg_")
    assert payload["body"] == {
        "msgtype": "file",
        "file": {"media_id": "media-1"},
        "chatid": "chat-1",
    }


def test_wecom_file_frame_uses_from_userid_for_single_chat() -> None:
    from invincat_cli.app import _wecom_build_file_frame

    frame = {
        "headers": {"req_id": "inbound-1"},
        "body": {"chattype": "single", "from": {"userid": "user-1"}},
    }

    payload = _wecom_build_file_frame(frame, "media-1")

    assert payload["cmd"] == "aibot_send_msg"
    assert payload["body"] == {
        "msgtype": "file",
        "file": {"media_id": "media-1"},
        "chatid": "user-1",
    }


def test_wecom_file_frame_requires_active_send_target() -> None:
    from invincat_cli.app import _wecom_build_file_frame

    frame = {"headers": {"req_id": "inbound-1"}, "body": {}}

    try:
        _wecom_build_file_frame(frame, "media-1")
    except RuntimeError as exc:
        assert "missing active-send target" in str(exc)
    else:
        raise AssertionError("expected missing target to fail")


class _ModelRequest:
    def __init__(self, *, tools: list[object], context: dict | None = None) -> None:
        self.tools = tools
        self.runtime = SimpleNamespace(context=context or {})

    def override(self, **kwargs):
        return _ModelRequest(
            tools=kwargs.get("tools", self.tools),
            context=self.runtime.context,
        )


def test_wecom_file_tool_hidden_outside_wecom_context(tmp_path) -> None:
    middleware = WeComFileMiddleware(allowed_root=tmp_path)
    request = _ModelRequest(tools=[*middleware.tools, {"name": "other"}])

    response = middleware.wrap_model_call(request, lambda req: req.tools)

    names = [getattr(tool, "name", None) or tool.get("name") for tool in response]
    assert names == ["other"]


def test_wecom_file_tool_visible_in_wecom_context(tmp_path) -> None:
    middleware = WeComFileMiddleware(allowed_root=tmp_path)
    request = _ModelRequest(
        tools=[*middleware.tools],
        context={WECOM_CONTEXT_FLAG: True},
    )

    response = middleware.wrap_model_call(request, lambda req: req.tools)

    assert [tool.name for tool in response] == [WECOM_FILE_TOOL_NAME]


def test_wecom_file_tool_rejects_direct_call_without_wecom_context(tmp_path) -> None:
    middleware = WeComFileMiddleware(allowed_root=tmp_path)
    request = ToolCallRequest(
        tool_call={"name": WECOM_FILE_TOOL_NAME, "id": "call-1", "args": {}},
        tool=None,
        state={},
        runtime=SimpleNamespace(context={}),
    )

    result = middleware.wrap_tool_call(
        request,
        lambda _request: ToolMessage("should not run", tool_call_id="call-1"),
    )

    assert result.status == "error"
    assert "only available during /wecombot" in str(result.content)


def test_wecom_file_tool_emits_request_payload(tmp_path) -> None:
    file_path = tmp_path / "report.txt"
    file_path.write_text("hello", encoding="utf-8")
    tool = WeComFileMiddleware(allowed_root=tmp_path).tools[0]

    result = tool.invoke(
        {
            "type": "tool_call",
            "name": WECOM_FILE_TOOL_NAME,
            "args": {"path": str(file_path)},
            "id": "call-1",
        }
    )

    payload = parse_wecom_file_request(result.content)
    assert payload is not None
    assert payload["path"] == str(file_path.resolve())
    assert payload["filename"] == "report.txt"
    assert payload["size"] == 5
    assert payload["tool_call_id"] == "call-1"


def test_wecom_file_tool_blocks_outside_root(tmp_path) -> None:
    outside = tmp_path.parent / "outside-wecom-file.txt"
    outside.write_text("secret", encoding="utf-8")
    tool = WeComFileMiddleware(allowed_root=tmp_path).tools[0]

    try:
        tool.invoke(
            {
                "type": "tool_call",
                "name": WECOM_FILE_TOOL_NAME,
                "args": {"path": str(outside)},
                "id": "call-1",
            }
        )
    except ValueError as exc:
        assert "current project" in str(exc)
    else:
        raise AssertionError("expected outside-root file to be rejected")


def test_parse_wecom_file_request_rejects_non_marker_payload() -> None:
    assert parse_wecom_file_request("not json") is None
    assert parse_wecom_file_request(json.dumps({"type": "other"})) is None
    assert WECOM_FILE_MAX_BYTES == 20 * 1024 * 1024
