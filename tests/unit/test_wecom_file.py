from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from invincat_cli.wecom_bridge import WeComBridge
from invincat_cli.wecom_media import (
    build_wecom_agent_input_with_media_downloads,
    decrypt_wecom_media_payload,
    download_wecom_inbound_media,
    send_wecom_file_from_tool_payload,
    upload_wecom_outbound_media,
    validate_wecom_media_url,
    wecom_filename_from_response,
)
from invincat_cli.wecom_file import (
    WECOM_CONTEXT_FLAG,
    WECOM_FILE_MAX_BYTES,
    WECOM_FILE_TOOL_NAME,
    WeComFileMiddleware,
    parse_wecom_file_request,
)
from invincat_cli.wecom_protocol import (
    WeComInboundMedia,
    build_wecom_agent_input,
    build_wecom_file_frame,
    build_wecom_ping_frame,
    extract_wecom_inbound_media,
    extract_wecom_mixed_text,
    extract_wecom_voice_text,
    is_supported_wecom_message_frame,
)
from invincat_cli.wecom_session import format_wecom_progress_line, wecom_user_facing_error


def test_wecom_ping_frame_uses_official_ping_command() -> None:
    frame = build_wecom_ping_frame()

    assert frame["cmd"] == "ping"
    assert frame["headers"]["req_id"].startswith("ping_")
    assert frame["body"] == {}


def test_wecom_extract_inbound_file_media() -> None:
    frame = {
        "cmd": "aibot_msg_callback",
        "body": {
            "msgtype": "file",
            "file": {
                "url": "https://example.com/download/report.docx",
                "aeskey": "a" * 43,
            },
        },
    }

    media = extract_wecom_inbound_media(frame)

    assert len(media) == 1
    assert media[0].msgtype == "file"
    assert media[0].url == "https://example.com/download/report.docx"
    assert media[0].aeskey == "a" * 43


def test_wecom_extract_inbound_file_media_accepts_sdk_aliases() -> None:
    frame = {
        "cmd": "aibot_msg_callback",
        "body": {
            "msgtype": "file",
            "file": {
                "download_url": "https://example.com/download/report.docx",
                "aes_key": "b" * 43,
                "name": "report.docx",
            },
        },
    }

    media = extract_wecom_inbound_media(frame)

    assert len(media) == 1
    assert media[0].url == "https://example.com/download/report.docx"
    assert media[0].aeskey == "b" * 43
    assert media[0].filename_hint == "report.docx"


def test_wecom_extract_inbound_mixed_text_and_image() -> None:
    frame = {
        "cmd": "aibot_msg_callback",
        "body": {
            "msgtype": "mixed",
            "mixed": {
                "msg_item": [
                    {"msgtype": "text", "text": {"content": "看下这张图"}},
                    {
                        "msgtype": "image",
                        "image": {
                            "url": "https://example.com/image",
                            "aeskey": "b" * 43,
                        },
                    },
                ]
            },
        },
    }

    assert extract_wecom_mixed_text(frame) == "看下这张图"
    media = extract_wecom_inbound_media(frame)
    assert len(media) == 1
    assert media[0].msgtype == "image"


def test_wecom_decrypt_media_payload_roundtrip() -> None:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    key = bytes(range(32))
    aeskey = base64.b64encode(key).decode("ascii").rstrip("=")
    plaintext = b"hello wecom file"
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()

    assert decrypt_wecom_media_payload(encrypted, aeskey) == plaintext


def test_wecom_decrypt_media_payload_accepts_urlsafe_aeskey() -> None:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    key = b"\xfb" * 32
    aeskey = base64.urlsafe_b64encode(key).decode("ascii").rstrip("=")
    plaintext = b"hello urlsafe key"
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()

    assert decrypt_wecom_media_payload(encrypted, aeskey) == plaintext


def test_wecom_decrypt_media_payload_accepts_wecom_32_byte_padding() -> None:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = bytes(range(32))
    aeskey = base64.b64encode(key).decode("ascii").rstrip("=")
    plaintext = b"0123456789abcdef"
    padded = plaintext + (bytes([32]) * 32)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()

    assert decrypt_wecom_media_payload(encrypted, aeskey) == plaintext


def test_wecom_extract_voice_text() -> None:
    frame = {
        "cmd": "aibot_msg_callback",
        "body": {
            "msgtype": "voice",
            "voice": {"recognition": "帮我总结这个语音"},
        },
    }

    assert is_supported_wecom_message_frame(frame) is True
    assert extract_wecom_voice_text(frame) == "帮我总结这个语音"


def test_wecom_bridge_dispatches_supported_message_once() -> None:
    seen: list[dict] = []

    async def _noop(_message: str) -> None:
        return None

    async def _on_message(frame: dict) -> None:
        seen.append(frame)

    bridge = WeComBridge(
        on_status=_noop,
        on_error=_noop,
        on_message=_on_message,
        should_exit=lambda: False,
    )
    frame = {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req-1"},
        "body": {
            "msgtype": "text",
            "msgid": "msg-1",
            "text": {"content": "hello"},
        },
    }

    async def _run() -> None:
        await bridge._handle_callback_frame(frame)
        await asyncio.sleep(0)
        await bridge._handle_callback_frame(frame)
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert seen == [frame]


def test_wecom_build_agent_input_for_mixed_media(tmp_path: Path) -> None:
    frame = {
        "cmd": "aibot_msg_callback",
        "body": {
            "msgtype": "mixed",
            "mixed": {
                "msg_item": [
                    {"msgtype": "text", "text": {"content": "请看附件"}},
                    {"msgtype": "image", "image": {"url": "https://example.com/i"}},
                ]
            },
        },
    }
    path = tmp_path / "image.jpg"

    text = build_wecom_agent_input(frame, saved_paths=[path])

    assert "请看附件" in text
    assert str(path) in text
    assert "已下载到本地" in text


def test_wecom_build_agent_input_for_voice() -> None:
    frame = {
        "cmd": "aibot_msg_callback",
        "body": {
            "msgtype": "voice",
            "voice": {"recognition": "语音转文字"},
        },
    }

    assert build_wecom_agent_input(frame, saved_paths=[]) == "语音转文字"


def test_wecom_filename_prefers_content_disposition() -> None:
    filename = wecom_filename_from_response(
        url="https://example.com/download",
        filename_hint="",
        content_disposition='attachment; filename="report final.txt"',
        content_type="text/plain",
        media_type="file",
        fallback="file_1",
    )

    assert filename == "report_final.txt"


def test_wecom_validate_media_url_rejects_non_http() -> None:
    with pytest.raises(ValueError, match="Invalid WeCom media URL"):
        validate_wecom_media_url("file:///etc/passwd")


def test_wecom_download_inbound_media_streams_and_writes_file(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    key = bytes(range(32))
    aeskey = base64.b64encode(key).decode("ascii").rstrip("=")
    padder = PKCS7(128).padder()
    padded = padder.update(b"hello") + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={
                "content-disposition": 'attachment; filename="from-header.txt"',
                "content-type": "text/plain",
            },
            content=encrypted,
            request=request,
        )
    )

    def _client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    async def _run() -> Path:
        return await download_wecom_inbound_media(
            WeComInboundMedia(
                msgtype="file",
                url="https://example.com/media",
                aeskey=aeskey,
                filename_hint="",
            ),
            inbound_frame={"body": {"msgid": "msg-1"}},
            index=1,
            cwd=tmp_path,
            http_client_factory=_client,
        )

    path = asyncio.run(_run())

    assert path == tmp_path / ".invincat" / "wecom_downloads" / "from-header.txt"
    assert path.read_bytes() == b"hello"


def test_wecom_download_inbound_media_rejects_stream_over_limit(
    tmp_path: Path,
) -> None:
    class _OversizedStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"a" * (20 * 1024 * 1024 + 32)
            yield b"b"

    aeskey = base64.b64encode(bytes(range(32))).decode("ascii").rstrip("=")

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=_OversizedStream(), request=request)

    transport = httpx.MockTransport(_handler)

    def _client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    async def _run() -> None:
        await download_wecom_inbound_media(
            WeComInboundMedia(
                msgtype="file",
                url="https://example.com/media",
                aeskey=aeskey,
                filename_hint="",
            ),
            inbound_frame={"body": {"msgid": "msg-1"}},
            index=1,
            cwd=tmp_path,
            http_client_factory=_client,
        )

    with pytest.raises(ValueError, match="larger than the 20 MB limit"):
        asyncio.run(_run())


def test_wecom_download_inbound_media_requires_aeskey(tmp_path: Path) -> None:
    async def _run() -> None:
        await download_wecom_inbound_media(
            WeComInboundMedia(
                msgtype="file",
                url="https://example.com/media",
                aeskey="",
                filename_hint="",
            ),
            inbound_frame={"body": {"msgid": "msg-1"}},
            index=1,
            cwd=tmp_path,
            http_client_factory=lambda: httpx.AsyncClient(),
        )

    with pytest.raises(ValueError, match="missing aeskey"):
        asyncio.run(_run())


def test_wecom_agent_input_downloads_inbound_media(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.primitives.padding import PKCS7

    key = bytes(range(32))
    aeskey = base64.b64encode(key).decode("ascii").rstrip("=")
    padder = PKCS7(128).padder()
    padded = padder.update(b"image-bytes") + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-type": "image/jpeg"},
            content=encrypted,
            request=request,
        )
    )

    def _client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    frame = {
        "cmd": "aibot_msg_callback",
        "body": {
            "msgid": "msg-1",
            "msgtype": "mixed",
            "mixed": {
                "msg_item": [
                    {"msgtype": "text", "text": {"content": "看图"}},
                    {
                        "msgtype": "image",
                        "image": {
                            "url": "https://example.com/media",
                            "aeskey": aeskey,
                        },
                    },
                ],
            },
        },
    }

    text = asyncio.run(
        build_wecom_agent_input_with_media_downloads(
            frame,
            cwd=tmp_path,
            http_client_factory=_client,
        )
    )

    assert "看图" in text
    assert "已下载到本地" in text
    assert ".invincat/wecom_downloads" in text


def test_wecom_file_frame_uses_active_send_when_chatid_present() -> None:
    frame = {"headers": {"req_id": "inbound-1"}, "body": {"chatid": "chat-1"}}

    payload = build_wecom_file_frame(frame, "media-1")

    assert payload["cmd"] == "aibot_send_msg"
    assert payload["headers"]["req_id"].startswith("aibot_send_msg_")
    assert payload["body"] == {
        "msgtype": "file",
        "file": {"media_id": "media-1"},
        "chatid": "chat-1",
    }


def test_wecom_file_frame_uses_from_userid_for_single_chat() -> None:
    frame = {
        "headers": {"req_id": "inbound-1"},
        "body": {"chattype": "single", "from": {"userid": "user-1"}},
    }

    payload = build_wecom_file_frame(frame, "media-1")

    assert payload["cmd"] == "aibot_send_msg"
    assert payload["body"] == {
        "msgtype": "file",
        "file": {"media_id": "media-1"},
        "chatid": "user-1",
    }


def test_wecom_file_frame_requires_active_send_target() -> None:
    frame = {"headers": {"req_id": "inbound-1"}, "body": {}}

    try:
        build_wecom_file_frame(frame, "media-1")
    except RuntimeError as exc:
        assert "missing active-send target" in str(exc)
    else:
        raise AssertionError("expected missing target to fail")


def test_wecom_upload_outbound_media_uses_init_chunks_and_finish(tmp_path: Path) -> None:
    path = tmp_path / "report.txt"
    path.write_text("hello", encoding="utf-8")
    sent: list[dict] = []

    async def _send_request(payload: dict) -> dict:
        sent.append(payload)
        if payload["cmd"] == "aibot_upload_media_init":
            return {"body": {"upload_id": "upload-1"}}
        if payload["cmd"] == "aibot_upload_media_finish":
            return {"body": {"media_id": "media-1"}}
        return {"body": {}}

    media_id = asyncio.run(
        upload_wecom_outbound_media(path, send_request=_send_request, chunk_size=2)
    )

    assert media_id == "media-1"
    assert [payload["cmd"] for payload in sent] == [
        "aibot_upload_media_init",
        "aibot_upload_media_chunk",
        "aibot_upload_media_chunk",
        "aibot_upload_media_chunk",
        "aibot_upload_media_finish",
    ]
    assert sent[0]["body"]["filename"] == "report.txt"
    assert sent[0]["body"]["total_size"] == 5
    assert sent[0]["body"]["total_chunks"] == 3
    assert [payload["body"]["chunk_index"] for payload in sent[1:4]] == [0, 1, 2]
    assert [payload["body"]["base64_data"] for payload in sent[1:4]] == [
        "aGU=",
        "bGw=",
        "bw==",
    ]
    assert sent[-1]["body"] == {"upload_id": "upload-1"}


def test_wecom_send_file_from_tool_payload_uploads_and_sends_file(tmp_path: Path) -> None:
    path = tmp_path / "report.txt"
    path.write_text("hello", encoding="utf-8")
    sent: list[dict] = []

    async def _send_request(payload: dict) -> dict:
        sent.append(payload)
        if payload["cmd"] == "aibot_upload_media_init":
            return {"body": {"upload_id": "upload-1"}}
        if payload["cmd"] == "aibot_upload_media_finish":
            return {"body": {"media_id": "media-1"}}
        return {"body": {}}

    frame = {
        "headers": {"req_id": "inbound-1"},
        "body": {"chatid": "chat-1"},
    }

    asyncio.run(
        send_wecom_file_from_tool_payload(
            frame,
            {"path": str(path), "filename": "report.txt"},
            cwd=tmp_path,
            send_request=_send_request,
        )
    )

    assert sent[-1]["cmd"] == "aibot_send_msg"
    assert sent[-1]["body"] == {
        "msgtype": "file",
        "file": {"media_id": "media-1"},
        "chatid": "chat-1",
    }


def test_wecom_progress_and_error_helpers() -> None:
    assert format_wecom_progress_line(
        running_tool="shell",
        completed_tools=2,
        assistant_started=False,
        tick=1,
    ) == "处理中：正在执行工具 `shell`，已完成 2 个.."
    assert format_wecom_progress_line(
        running_tool=None,
        completed_tools=0,
        assistant_started=True,
        tick=0,
    ) == "处理中：正在整理回复."
    assert wecom_user_facing_error(ValueError("bad input")) == "bad input"


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
