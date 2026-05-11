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

from invincat_cli.wecom.bridge import WeComBridge
from invincat_cli.wecom.media import (
    build_wecom_agent_input_with_media_downloads,
    decrypt_wecom_media_payload,
    download_wecom_inbound_media,
    send_wecom_file_from_tool_payload,
    upload_wecom_outbound_media,
    validate_wecom_media_url,
    wecom_filename_from_response,
)
from invincat_cli.wecom.file import (
    WECOM_CONTEXT_FLAG,
    WECOM_FILE_MAX_BYTES,
    WECOM_FILE_TOOL_NAME,
    WeComFileMiddleware,
    parse_wecom_file_request,
)
from invincat_cli.wecom.protocol import (
    WeComInboundMedia,
    build_wecom_agent_input,
    build_wecom_file_frame,
    build_wecom_ping_frame,
    build_wecom_text_frame,
    extract_wecom_inbound_media,
    extract_wecom_mixed_text,
    extract_wecom_voice_text,
    is_supported_wecom_message_frame,
)
from invincat_cli.wecom.session import (
    WeComMessageResponder,
    format_wecom_progress_line,
    wecom_user_facing_error,
)
from invincat_cli.wecom.turn import WeComTurnRunner
from invincat_cli.widgets.message_store import MessageData, MessageType


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


def test_wecom_bridge_rejects_sender_outside_allowlist(monkeypatch) -> None:
    monkeypatch.setenv("WECOM_ALLOWED_USERIDS", "allowed-user")
    monkeypatch.setenv("WECOM_ALLOWED_CHATIDS", "allowed-chat")
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
        "headers": {"req_id": "req-denied"},
        "body": {
            "msgtype": "text",
            "chatid": "other-chat",
            "from": {"userid": "other-user"},
            "text": {"content": "hello"},
        },
    }

    async def _run() -> None:
        await bridge._handle_callback_frame(frame)
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert seen == []


def test_wecom_bridge_allows_configured_sender(monkeypatch) -> None:
    monkeypatch.setenv("WECOM_ALLOWED_USERIDS", "allowed-user")
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
        "headers": {"req_id": "req-allowed"},
        "body": {
            "msgtype": "text",
            "from": {"userid": "allowed-user"},
            "text": {"content": "hello"},
        },
    }

    async def _run() -> None:
        await bridge._handle_callback_frame(frame)
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert seen == [frame]


def test_wecom_recv_closes_stale_connection(monkeypatch) -> None:
    import invincat_cli.wecom.bridge as bridge_module

    monkeypatch.setattr(bridge_module, "WECOM_STALE_CONNECTION_SECONDS", 0)

    class _Ws:
        def __init__(self) -> None:
            self.closed = False

        async def recv(self) -> str:
            await asyncio.sleep(1)
            return "{}"

        async def close(self) -> None:
            self.closed = True

    async def _noop(_message: str) -> None:
        return None

    async def _on_message(_frame: dict) -> None:
        return None

    ws = _Ws()
    bridge = WeComBridge(
        on_status=_noop,
        on_error=_noop,
        on_message=_on_message,
        should_exit=lambda: False,
    )

    async def _run() -> None:
        with pytest.raises(RuntimeError, match="stale"):
            await asyncio.wait_for(bridge._recv_raw(ws), timeout=1)

    asyncio.run(_run())

    assert ws.closed is True


def test_wecom_heartbeat_closes_on_send_failure(monkeypatch) -> None:
    import invincat_cli.wecom.bridge as bridge_module

    monkeypatch.setattr(bridge_module, "WECOM_HEARTBEAT_INTERVAL", 0)

    class _Ws:
        def __init__(self) -> None:
            self.closed = False

        async def send(self, raw: str) -> None:
            raise OSError("offline")

        async def close(self) -> None:
            self.closed = True

    async def _noop(_message: str) -> None:
        return None

    async def _on_message(_frame: dict) -> None:
        return None

    ws = _Ws()
    bridge = WeComBridge(
        on_status=_noop,
        on_error=_noop,
        on_message=_on_message,
        should_exit=lambda: False,
    )

    async def _run() -> None:
        await asyncio.wait_for(bridge._heartbeat(ws), timeout=1)

    asyncio.run(_run())

    assert ws.closed is True


def test_wecom_flush_outbox_closes_on_send_failure() -> None:
    class _Ws:
        def __init__(self) -> None:
            self.closed = False

        async def send(self, raw: str) -> None:
            raise OSError("offline")

        async def close(self) -> None:
            self.closed = True

    async def _noop(_message: str) -> None:
        return None

    async def _on_message(_frame: dict) -> None:
        return None

    ws = _Ws()
    bridge = WeComBridge(
        on_status=_noop,
        on_error=_noop,
        on_message=_on_message,
        should_exit=lambda: False,
    )
    bridge._ws = ws
    bridge.enqueue({"cmd": "aibot_respond_msg", "body": {}})

    async def _run() -> bool:
        return await bridge.flush_outbox()

    assert asyncio.run(_run()) is False
    assert ws.closed is True
    assert bridge._ws is None


def test_wecom_bridge_rejects_message_when_queue_full(monkeypatch) -> None:
    import invincat_cli.wecom.bridge as bridge_module

    monkeypatch.setattr(bridge_module, "WECOM_MAX_MESSAGE_TASKS", 1)

    async def _noop(_message: str) -> None:
        return None

    async def _on_message(_frame: dict) -> None:
        await asyncio.sleep(1)

    sent: list[str] = []

    class _Ws:
        async def send(self, raw: str) -> None:
            sent.append(raw)

        async def close(self) -> None:
            return None

    bridge = WeComBridge(
        on_status=_noop,
        on_error=_noop,
        on_message=_on_message,
        should_exit=lambda: False,
    )
    bridge._ws = _Ws()
    first = {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req-1"},
        "body": {"msgtype": "text", "text": {"content": "one"}},
    }
    second = {
        "cmd": "aibot_msg_callback",
        "headers": {"req_id": "req-2"},
        "body": {"msgtype": "text", "text": {"content": "two"}},
    }

    async def _run() -> None:
        await bridge._handle_callback_frame(first)
        await bridge._handle_callback_frame(second)
        bridge._cancel_message_tasks()

    asyncio.run(_run())

    assert len(bridge._message_tasks) == 0
    assert len(sent) == 1
    payload = json.loads(sent[0])
    assert payload["headers"]["req_id"] == "req-2"
    assert payload["body"]["stream"]["finish"] is True
    assert "队列繁忙" in payload["body"]["stream"]["content"]


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


def test_wecom_file_frame_uses_target_for_scheduled_synthetic_frame() -> None:
    """Scheduled-task synthetic frames carry the real chatid under
    ``body._wecom_target_chatid`` and use ``__scheduled_<id>`` for thread
    isolation; file sends must reach the real chatid, not the synthetic one."""
    frame = {
        "headers": {"req_id": "inbound-sched-1"},
        "body": {
            "chatid": "__scheduled_task-42",
            "_wecom_target_chatid": "wr_real_chat",
        },
    }

    payload = build_wecom_file_frame(frame, "media-1")

    assert payload["cmd"] == "aibot_send_msg"
    assert payload["body"]["chatid"] == "wr_real_chat"


def test_wecom_text_frame_sends_active_markdown_to_chat() -> None:
    payload = build_wecom_text_frame("chat-1", "hello")

    assert payload["cmd"] == "aibot_send_msg"
    assert payload["headers"]["req_id"].startswith("aibot_send_msg_")
    assert payload["body"] == {
        "msgtype": "markdown",
        "markdown": {"content": "hello"},
        "chatid": "chat-1",
    }


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


def test_wecom_turn_runner_returns_new_assistant_message(tmp_path: Path) -> None:
    lock = asyncio.Lock()
    messages: list[MessageData] = [
        MessageData(type=MessageType.USER, content="before", id="m-before")
    ]
    handled: list[str] = []

    async def _handle_user_message(
        message: str,
        on_text_delta,
        on_wecom_file_request,
    ) -> None:
        handled.append(message)
        await on_text_delta("answer", "answer")
        messages.append(
            MessageData(type=MessageType.ASSISTANT, content="answer", id="m-answer")
        )

    async def _send_request(payload: dict) -> dict:
        return {"body": {}}

    runner = WeComTurnRunner(
        lock=lock,
        cwd=tmp_path,
        is_busy=lambda: False,
        get_messages=lambda: list(messages),
        handle_user_message=_handle_user_message,
        send_request=_send_request,
        cancel_timed_out_turn=lambda: None,
    )

    answer = asyncio.run(runner.run("hello", inbound_frame={"body": {}}))

    assert handled == ["hello"]
    assert answer == "answer"


def test_wecom_turn_runner_does_not_hold_lock_while_waiting_for_idle(tmp_path: Path) -> None:
    lock = asyncio.Lock()

    async def _handle_user_message(
        _message: str,
        _on_text_delta,
        _on_wecom_file_request,
    ) -> None:
        raise AssertionError("should not inject while busy")

    async def _send_request(_payload: dict) -> dict:
        return {"body": {}}

    runner = WeComTurnRunner(
        lock=lock,
        cwd=tmp_path,
        is_busy=lambda: True,
        get_messages=lambda: [],
        handle_user_message=_handle_user_message,
        send_request=_send_request,
        cancel_timed_out_turn=lambda: None,
    )

    async def _run_and_check() -> None:
        task = asyncio.create_task(runner.run("hello", inbound_frame={"body": {}}))
        await asyncio.sleep(0.2)
        assert not lock.locked()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run_and_check())


def test_wecom_message_responder_streams_ack_content_and_final() -> None:
    queued: list[dict] = []
    flush_count = 0

    def _enqueue(payload: dict) -> None:
        queued.append(payload)

    async def _flush() -> bool:
        nonlocal flush_count
        flush_count += 1
        return True

    async def _build_agent_input(frame: dict) -> str:
        return "hello"

    async def _run_turn(text, frame, on_content) -> str:
        await on_content(f"streaming {text}")
        return "final answer"

    async def _report_error(message: str) -> None:
        raise AssertionError(message)

    responder = WeComMessageResponder(
        enqueue=_enqueue,
        flush=_flush,
        build_agent_input=_build_agent_input,
        run_turn=_run_turn,
        report_error=_report_error,
    )
    frame = {
        "headers": {"req_id": "inbound-1"},
        "body": {"chatid": "chat-1"},
    }

    asyncio.run(responder.handle(frame))

    assert flush_count == 3
    assert [payload["body"]["stream"]["finish"] for payload in queued] == [
        False,
        False,
        True,
    ]
    assert queued[0]["headers"]["req_id"] == "inbound-1"
    assert queued[1]["body"]["stream"]["content"] == "streaming hello"
    assert queued[2]["body"]["stream"]["content"] == "final answer"


def test_headless_wecom_handler_passes_scheduled_runtime_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.scheduler.tool import SCHEDULE_CONTEXT_FLAG
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    contexts: list[dict] = []

    class FakeAgent:
        async def astream(self, *_args, **kwargs):  # noqa: ANN002, ANN003
            contexts.append(kwargs["context"])
            if False:
                yield None

    async def send_request(_payload: dict) -> dict:
        return {"errcode": 0}

    monkeypatch.setattr(
        "invincat_cli.config.build_stream_config",
        lambda _thread_id, _agent_name: {},
    )
    handler = HeadlessWeComHandler(
        agent=FakeAgent(),
        cwd=tmp_path,
        send_request=send_request,
    )

    asyncio.run(
        handler._run_agent_turn(
            "hello",
            thread_id="thread-1",
            inbound_frame={"body": {"chatid": "chat-1"}},
            on_content=lambda _content: asyncio.sleep(0),
            runtime_context={SCHEDULE_CONTEXT_FLAG: True, "wecom_enabled": False},
        )
    )

    assert contexts == [{SCHEDULE_CONTEXT_FLAG: True, "wecom_enabled": True}]


def test_wecom_daemon_scheduled_timeout_result_is_delivered() -> None:
    from invincat_cli.wecom.daemon import _deliver_scheduled_timeout_result

    sent_payloads: list[dict] = []

    class FakeBridge:
        def __init__(self) -> None:
            self.ready = asyncio.Event()
            self.ready.set()

        async def send_request(self, payload: dict, *, timeout: float) -> dict:  # noqa: ARG002
            sent_payloads.append(payload)
            return {"errcode": 0}

    task = SimpleNamespace(
        title="Daily report",
        delivery=SimpleNamespace(
            channels=[{"type": "wecom", "chatid": "chat-1"}]
        ),
    )
    store = SimpleNamespace(load_task=lambda _task_id: task)

    delivered = asyncio.run(
        _deliver_scheduled_timeout_result(
            store,
            [FakeBridge()],
            task_id="task-1",
        )
    )

    assert delivered is True
    assert sent_payloads[0]["body"]["chatid"] == "chat-1"
    assert "定时任务执行超时" in sent_payloads[0]["body"]["markdown"]["content"]
    assert "Daily report" in sent_payloads[0]["body"]["markdown"]["content"]


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
