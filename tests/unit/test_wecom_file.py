from __future__ import annotations

import asyncio
import base64
import json
import sys
from collections import OrderedDict, deque
from contextlib import suppress
from datetime import UTC
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest

from invincat_cli.wecom import file as wecom_file
from invincat_cli.wecom import media as wecom_media
from invincat_cli.wecom import turn as wecom_turn
from invincat_cli.wecom.bridge import WeComBridge, WeComOfflineError, WeComServerError
from invincat_cli.wecom.file import (
    WECOM_CONTEXT_FLAG,
    WECOM_FILE_MAX_BYTES,
    WECOM_FILE_TOOL_NAME,
    WeComFileMiddleware,
    parse_wecom_file_request,
)
from invincat_cli.wecom.media import (
    build_wecom_agent_input_with_media_downloads,
    decrypt_wecom_media_payload,
    download_wecom_inbound_media,
    send_wecom_file_from_tool_payload,
    upload_wecom_outbound_media,
    validate_wecom_media_url,
    wecom_filename_from_response,
)
from invincat_cli.wecom.protocol import (
    WeComInboundMedia,
    build_wecom_agent_input,
    build_wecom_file_frame,
    build_wecom_ping_frame,
    build_wecom_stream_frame,
    build_wecom_subscribe_frame,
    build_wecom_text_frame,
    extract_wecom_inbound_media,
    extract_wecom_mixed_text,
    extract_wecom_text_message,
    extract_wecom_voice_text,
    is_supported_wecom_message_frame,
    safe_wecom_content,
    wecom_frame_req_id,
)
from invincat_cli.wecom.session import (
    WeComMessageResponder,
    format_wecom_progress_line,
    wecom_user_facing_error,
)
from invincat_cli.wecom.turn import WeComTurnRunner
from invincat_cli.widgets.message_store import MessageData, MessageType, ToolStatus


def test_wecom_ping_frame_uses_official_ping_command() -> None:
    frame = build_wecom_ping_frame()

    assert frame["cmd"] == "ping"
    assert frame["headers"]["req_id"].startswith("ping_")
    assert frame["body"] == {}


def test_wecom_protocol_frames_and_safe_content_helpers() -> None:
    subscribe = build_wecom_subscribe_frame("bot-1", "secret")
    assert subscribe["cmd"] == "aibot_subscribe"
    assert subscribe["headers"]["req_id"].startswith("aibot_subscribe_")
    assert subscribe["body"] == {"bot_id": "bot-1", "secret": "secret"}

    stream = build_wecom_stream_frame(
        {"headers": {"req_id": "req-1"}, "body": {"chatid": "chat-1"}},
        "stream-1",
        "\x00hello",
        finish=False,
    )
    assert stream["headers"]["req_id"] == "req-1"
    assert stream["body"]["chatid"] == "chat-1"
    assert stream["body"]["stream"] == {
        "id": "stream-1",
        "content": "hello",
        "finish": False,
    }
    assert wecom_frame_req_id({"headers": {"req_id": 123}}) == "123"
    assert wecom_frame_req_id({}) == ""
    assert safe_wecom_content("   ") == "（空回复）"
    assert safe_wecom_content("abcdef", max_bytes=3) == "abc\n\n(输出过长，已截断)"


def test_wecom_text_and_support_helpers_reject_invalid_frames() -> None:
    assert extract_wecom_text_message({"cmd": "other"}) is None
    assert (
        extract_wecom_text_message(
            {"cmd": "aibot_msg_callback", "body": {"msgtype": "file"}}
        )
        is None
    )
    assert (
        extract_wecom_text_message(
            {
                "cmd": "aibot_msg_callback",
                "body": {"msgtype": "text", "text": {"content": "  hello  "}},
            }
        )
        == "hello"
    )
    assert (
        extract_wecom_text_message(
            {
                "cmd": "aibot_msg_callback",
                "body": {"msgtype": "text", "text": {"content": "  "}},
            }
        )
        is None
    )
    assert is_supported_wecom_message_frame({"cmd": "other"}) is False


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


def test_wecom_inbound_media_rejects_invalid_payloads() -> None:
    assert extract_wecom_inbound_media({"cmd": "other"}) == []
    assert (
        extract_wecom_inbound_media(
            {"cmd": "aibot_msg_callback", "body": {"msgtype": "text"}}
        )
        == []
    )
    assert (
        extract_wecom_inbound_media(
            {"cmd": "aibot_msg_callback", "body": {"msgtype": "file", "file": None}}
        )
        == []
    )
    assert (
        extract_wecom_inbound_media(
            {
                "cmd": "aibot_msg_callback",
                "body": {"msgtype": "file", "file": {"url": ""}},
            }
        )
        == []
    )
    assert (
        extract_wecom_inbound_media(
            {"cmd": "aibot_msg_callback", "body": {"msgtype": "mixed", "mixed": {}}}
        )
        == []
    )
    media = extract_wecom_inbound_media(
        {
            "cmd": "aibot_msg_callback",
            "body": {
                "msgtype": "mixed",
                "mixed": {
                    "msg_item": [
                        "bad",
                        {"msgtype": "file", "file": {"url": ""}},
                        {
                            "msgtype": "image",
                            "image": {
                                "fileUrl": "https://example.com/a.png",
                                "aesKey": 123,
                                "filename": 456,
                            },
                        },
                    ]
                },
            },
        }
    )
    assert media == [
        WeComInboundMedia(
            msgtype="image",
            url="https://example.com/a.png",
            aeskey="",
            filename_hint="456",
        )
    ]


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


def test_wecom_mixed_and_voice_extractors_handle_empty_shapes() -> None:
    assert extract_wecom_mixed_text({"body": {"mixed": None}}) == ""
    assert (
        extract_wecom_mixed_text(
            {
                "body": {
                    "mixed": {
                        "msg_item": [
                            "bad",
                            {"msgtype": "text", "text": "not-dict"},
                            {"msgtype": "text", "text": {"content": " first "}},
                            {
                                "msgtype": "image",
                                "image": {"url": "https://example.com/a.png"},
                            },
                            {"msgtype": "text", "text": {"content": "second"}},
                        ]
                    }
                }
            }
        )
        == "first\nsecond"
    )
    assert extract_wecom_voice_text({"cmd": "other"}) is None
    assert (
        extract_wecom_voice_text(
            {"cmd": "aibot_msg_callback", "body": {"msgtype": "text"}}
        )
        is None
    )
    assert (
        extract_wecom_voice_text(
            {
                "cmd": "aibot_msg_callback",
                "body": {"msgtype": "voice", "voice": ["not-dict"]},
            }
        )
        is None
    )
    assert (
        extract_wecom_voice_text(
            {
                "cmd": "aibot_msg_callback",
                "body": {"msgtype": "voice", "voice": {"recognition": "  "}},
            }
        )
        is None
    )


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


def test_wecom_media_client_and_crypto_error_paths() -> None:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    client = wecom_media.create_wecom_media_http_client()
    try:
        assert isinstance(client, httpx.AsyncClient)
    finally:
        asyncio.run(client.aclose())

    assert decrypt_wecom_media_payload(b"plain", "") == b"plain"
    with pytest.raises(ValueError, match="32 bytes"):
        wecom_media.decode_wecom_media_aes_key(base64.b64encode(b"short").decode())

    key = bytes(range(32))
    aeskey = base64.b64encode(key).decode("ascii").rstrip("=")
    with pytest.raises(ValueError, match="empty payload"):
        decrypt_wecom_media_payload(b"", aeskey)

    def encrypt_raw(raw: bytes) -> bytes:
        encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
        return encryptor.update(raw) + encryptor.finalize()

    with pytest.raises(ValueError, match="padding value"):
        decrypt_wecom_media_payload(
            encrypt_raw(b"0123456789abcdef"[:-1] + b"\x00"), aeskey
        )

    with pytest.raises(ValueError, match="padding bytes"):
        decrypt_wecom_media_payload(encrypt_raw(b"0123456789abc\x02\x02\x03"), aeskey)


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


def test_wecom_bridge_allows_configured_chatid_and_skips_unsupported(
    monkeypatch,
) -> None:
    monkeypatch.setenv("WECOM_ALLOWED_CHATIDS", "chat-allowed")
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

    async def _run() -> None:
        await bridge._handle_callback_frame({"cmd": "other"})
        await bridge._handle_callback_frame(
            {
                "cmd": "aibot_msg_callback",
                "headers": {"req_id": "req-chat"},
                "body": {
                    "msgtype": "text",
                    "chatid": "chat-allowed",
                    "text": {"content": "hello"},
                },
            }
        )
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert [frame["headers"]["req_id"] for frame in seen] == ["req-chat"]


def test_wecom_bridge_stop_ready_enqueue_and_memory_cap() -> None:
    async def _noop(_message: str) -> None:
        return None

    async def _on_message(_frame: dict) -> None:
        return None

    bridge = WeComBridge(
        on_status=_noop,
        on_error=_noop,
        on_message=_on_message,
        should_exit=lambda: False,
    )
    bridge.active = True
    bridge.enqueue({"cmd": "aibot_respond_msg", "body": {}})

    for index in range(501):
        bridge._remember_req_id(f"req-{index}")

    bridge.stop()

    assert bridge.active is False
    assert bridge.ready is bridge._bridge_ready
    assert len(bridge._outbox) == 1
    assert len(bridge._seen_req_ids) == 300
    assert "req-200" not in bridge._seen_req_ids
    assert "req-500" in bridge._seen_req_ids


def test_wecom_bridge_run_reports_missing_websockets(monkeypatch) -> None:
    errors: list[str] = []

    async def _noop(_message: str) -> None:
        return None

    async def _on_error(message: str) -> None:
        errors.append(message)

    async def _on_message(_frame: dict) -> None:
        return None

    monkeypatch.setitem(sys.modules, "websockets", None)
    bridge = WeComBridge(
        on_status=_noop,
        on_error=_on_error,
        on_message=_on_message,
        should_exit=lambda: False,
    )

    asyncio.run(bridge.run(bot_id="bot", secret="secret", ws_url="ws://example"))

    assert bridge.active is False
    assert errors and errors[0].startswith("Missing websockets dependency:")


def test_wecom_bridge_run_handles_frames_and_exits(monkeypatch) -> None:
    statuses: list[str] = []
    sent: list[str] = []

    class _Ws:
        def __init__(self) -> None:
            self.frames = iter(
                [
                    "not-json",
                    json.dumps({"headers": {"req_id": "pending"}}),
                    json.dumps({}),
                    json.dumps(
                        {
                            "cmd": "aibot_send_msg",
                            "headers": {"req_id": "unmatched"},
                            "errcode": 40008,
                            "errmsg": "bad chat",
                        }
                    ),
                    json.dumps(
                        {
                            "cmd": "aibot_msg_callback",
                            "headers": {"req_id": "callback"},
                            "body": {"msgtype": "text", "text": {"content": "hi"}},
                        }
                    ),
                ]
            )
            self.recv_count = 0

        async def send(self, raw: str) -> None:
            sent.append(raw)

        async def recv(self) -> str:
            self.recv_count += 1
            return next(self.frames)

        async def close(self) -> None:
            return None

    class _Connect:
        def __init__(self, ws: _Ws) -> None:
            self.ws = ws

        async def __aenter__(self) -> _Ws:
            return self.ws

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def _on_status(message: str) -> None:
        statuses.append(message)

    async def _on_error(_message: str) -> None:
        raise AssertionError("unexpected error")

    async def _on_message(_frame: dict) -> None:
        return None

    ws = _Ws()
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        SimpleNamespace(connect=lambda *_args, **_kwargs: _Connect(ws)),
    )
    bridge = WeComBridge(
        on_status=_on_status,
        on_error=_on_error,
        on_message=_on_message,
        should_exit=lambda: ws.recv_count >= 5,
    )
    bridge.enqueue(
        {
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": "outbox"},
            "body": {"stream": {"finish": True}},
        }
    )

    async def _run() -> asyncio.Future[dict]:
        pending = asyncio.get_running_loop().create_future()
        bridge._pending_requests["pending"] = pending
        await bridge.run(bot_id="bot", secret="secret", ws_url="ws://example")
        return pending

    pending = asyncio.run(_run())

    assert pending.result()["headers"]["req_id"] == "pending"
    assert statuses == [
        "WeCom connected, awaiting subscription acknowledgement.",
        "WeCom subscription acknowledged.",
    ]
    assert [json.loads(raw)["cmd"] for raw in sent] == [
        "aibot_subscribe",
        "aibot_respond_msg",
    ]
    assert bridge.active is False
    assert bridge._ws is None


def test_wecom_bridge_run_marks_ack_when_control_handler_declines_frame(
    monkeypatch,
) -> None:
    class _Ws:
        def __init__(self) -> None:
            self.recv_count = 0

        async def send(self, _raw: str) -> None:
            return None

        async def recv(self) -> str:
            self.recv_count += 1
            return json.dumps({})

        async def close(self) -> None:
            return None

    class _Connect:
        def __init__(self, ws: _Ws) -> None:
            self.ws = ws

        async def __aenter__(self) -> _Ws:
            return self.ws

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def _noop(_message: str) -> None:
        return None

    async def _on_message(_frame: dict) -> None:
        return None

    async def decline_control(_frame: dict, *, saw_subscribe_ack: bool) -> bool:
        assert saw_subscribe_ack is False
        return False

    ws = _Ws()
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        SimpleNamespace(connect=lambda *_args, **_kwargs: _Connect(ws)),
    )
    bridge = WeComBridge(
        on_status=_noop,
        on_error=_noop,
        on_message=_on_message,
        should_exit=lambda: ws.recv_count >= 2,
    )
    bridge._handle_control_frame = decline_control

    asyncio.run(bridge.run(bot_id="bot", secret="secret", ws_url="ws://example"))

    assert bridge.active is False


def test_wecom_bridge_run_reports_disconnect_and_backs_off(monkeypatch) -> None:
    statuses: list[str] = []
    sleeps: list[float] = []
    attempts = 0

    class DisconnectError(RuntimeError):
        code = 1006
        reason = "closed"

    class _Connect:
        async def __aenter__(self) -> object:
            raise DisconnectError("lost")

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def _on_status(message: str) -> None:
        statuses.append(message)

    async def _noop(_message: str) -> None:
        return None

    async def _on_message(_frame: dict) -> None:
        return None

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def should_exit() -> bool:
        nonlocal attempts
        attempts += 1
        return attempts > 1

    monkeypatch.setitem(
        sys.modules,
        "websockets",
        SimpleNamespace(connect=lambda *_args, **_kwargs: _Connect()),
    )
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    bridge = WeComBridge(
        on_status=_on_status,
        on_error=_noop,
        on_message=_on_message,
        should_exit=should_exit,
    )

    asyncio.run(bridge.run(bot_id="bot", secret="secret", ws_url="ws://example"))

    assert sleeps == [1]
    assert bridge._reconnect_delay == 2
    assert statuses == [
        "WeCom disconnected: lost (code=1006, reason=closed). Reconnecting in 1s..."
    ]
    assert bridge.active is False


def test_wecom_bridge_run_handles_subscribe_failure_and_cancel(monkeypatch) -> None:
    errors: list[str] = []
    sent: list[str] = []

    class _Ws:
        def __init__(self, frames: list[str]) -> None:
            self.frames = iter(frames)

        async def send(self, raw: str) -> None:
            sent.append(raw)

        async def recv(self) -> str:
            return next(self.frames)

        async def close(self) -> None:
            return None

    class _Connect:
        def __init__(self, ws: _Ws) -> None:
            self.ws = ws

        async def __aenter__(self) -> _Ws:
            return self.ws

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def _noop(_message: str) -> None:
        return None

    async def _on_error(message: str) -> None:
        errors.append(message)

    async def _on_message(_frame: dict) -> None:
        return None

    subscribe_fail_ws = _Ws([json.dumps({"errcode": 7, "errmsg": "bad secret"})])
    monkeypatch.setitem(
        sys.modules,
        "websockets",
        SimpleNamespace(connect=lambda *_args, **_kwargs: _Connect(subscribe_fail_ws)),
    )
    bridge = WeComBridge(
        on_status=_noop,
        on_error=_on_error,
        on_message=_on_message,
        should_exit=lambda: False,
    )

    asyncio.run(bridge.run(bot_id="bot", secret="secret", ws_url="ws://example"))

    assert errors == ["WeCom subscribe failed: errcode=7 errmsg=bad secret"]
    assert bridge.active is False
    assert json.loads(sent[0])["cmd"] == "aibot_subscribe"

    cancel_ws = _Ws([])

    async def raise_cancel(_ws: object) -> str:
        raise asyncio.CancelledError

    monkeypatch.setitem(
        sys.modules,
        "websockets",
        SimpleNamespace(connect=lambda *_args, **_kwargs: _Connect(cancel_ws)),
    )
    cancel_bridge = WeComBridge(
        on_status=_noop,
        on_error=_on_error,
        on_message=_on_message,
        should_exit=lambda: False,
    )
    cancel_bridge._recv_raw = raise_cancel

    asyncio.run(cancel_bridge.run(bot_id="bot", secret="secret", ws_url="ws://example"))

    assert cancel_bridge.active is False
    assert cancel_bridge._ws is None


def test_wecom_bridge_run_cleans_heartbeat_on_recv_exception(monkeypatch) -> None:
    statuses: list[str] = []
    sleeps: list[float] = []
    attempts = 0

    class _Ws:
        async def send(self, _raw: str) -> None:
            return None

        async def close(self) -> None:
            return None

    class _Connect:
        async def __aenter__(self) -> _Ws:
            return _Ws()

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def _on_status(message: str) -> None:
        statuses.append(message)

    async def _noop(_message: str) -> None:
        return None

    async def _on_message(_frame: dict) -> None:
        return None

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def fail_recv(_ws: object) -> str:
        raise RuntimeError("recv failed")

    async def idle_heartbeat(_ws: object) -> None:
        await asyncio.Event().wait()

    def should_exit() -> bool:
        nonlocal attempts
        attempts += 1
        return attempts > 2

    monkeypatch.setitem(
        sys.modules,
        "websockets",
        SimpleNamespace(connect=lambda *_args, **_kwargs: _Connect()),
    )
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    bridge = WeComBridge(
        on_status=_on_status,
        on_error=_noop,
        on_message=_on_message,
        should_exit=should_exit,
    )
    bridge._recv_raw = fail_recv
    bridge._heartbeat = idle_heartbeat

    asyncio.run(bridge.run(bot_id="bot", secret="secret", ws_url="ws://example"))

    assert sleeps == [1]
    assert statuses[-1] == "WeCom disconnected: recv failed. Reconnecting in 1s..."
    assert bridge._ws is None


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


def test_wecom_flush_outbox_returns_false_without_socket_and_succeeds() -> None:
    sent: list[str] = []

    class _Ws:
        async def send(self, raw: str) -> None:
            sent.append(raw)

    async def _noop(_message: str) -> None:
        return None

    async def _on_message(_frame: dict) -> None:
        return None

    bridge = WeComBridge(
        on_status=_noop,
        on_error=_noop,
        on_message=_on_message,
        should_exit=lambda: False,
    )

    assert asyncio.run(bridge.flush_outbox()) is False

    bridge._ws = _Ws()
    bridge.enqueue(
        {
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": "req-1"},
            "body": {"chatid": "chat-1", "stream": {"id": "s", "finish": True}},
        }
    )

    assert asyncio.run(bridge.flush_outbox()) is True
    assert len(sent) == 1
    assert bridge._outbox == deque()


def test_wecom_send_request_validates_offline_missing_id_and_server_error() -> None:
    class _Ws:
        async def send(self, _raw: str) -> None:
            return None

        async def close(self) -> None:
            return None

    async def _noop(_message: str) -> None:
        return None

    async def _on_message(_frame: dict) -> None:
        return None

    bridge = WeComBridge(
        on_status=_noop,
        on_error=_noop,
        on_message=_on_message,
        should_exit=lambda: False,
    )

    async def _run() -> None:
        with pytest.raises(WeComOfflineError, match="offline"):
            await bridge.send_request({"headers": {"req_id": "req-1"}})

        bridge._ws = _Ws()
        with pytest.raises(RuntimeError, match="missing headers.req_id"):
            await bridge.send_request({"headers": {}})

        bridge._pending_requests["req-error"] = (
            asyncio.get_running_loop().create_future()
        )
        bridge._pending_requests.clear()

        async def respond() -> None:
            await asyncio.sleep(0)
            bridge._pending_requests["req-error"].set_result(
                {
                    "cmd": "aibot_send_msg",
                    "errcode": 40008,
                    "errmsg": "bad chat",
                    "headers": {"req_id": "req-error"},
                    "body": [],
                }
            )

        task = asyncio.create_task(respond())
        with pytest.raises(WeComServerError) as exc_info:
            await bridge.send_request(
                {"cmd": "aibot_send_msg", "headers": {"req_id": "req-error"}},
                timeout=1,
            )
        await task
        assert exc_info.value.errcode == 40008
        assert exc_info.value.cmd == "aibot_send_msg"
        assert exc_info.value.req_id == "req-error"

    asyncio.run(_run())


def test_wecom_send_request_returns_success_response() -> None:
    class _Ws:
        async def send(self, _raw: str) -> None:
            return None

    async def _noop(_message: str) -> None:
        return None

    async def _on_message(_frame: dict) -> None:
        return None

    bridge = WeComBridge(
        on_status=_noop,
        on_error=_noop,
        on_message=_on_message,
        should_exit=lambda: False,
    )
    bridge._ws = _Ws()

    async def _run() -> dict:
        async def respond() -> None:
            await asyncio.sleep(0)
            bridge._pending_requests["req-ok"].set_result(
                {
                    "cmd": "aibot_send_msg",
                    "errcode": 0,
                    "errmsg": "",
                    "headers": {"req_id": "req-ok"},
                    "body": {"media_id": "media-1"},
                }
            )

        task = asyncio.create_task(respond())
        response = await bridge.send_request(
            {"cmd": "aibot_send_msg", "headers": {"req_id": "req-ok"}},
            timeout=1,
        )
        await task
        return response

    assert asyncio.run(_run())["body"] == {"media_id": "media-1"}


def test_wecom_send_request_timeout_and_send_failure_close_socket(
    monkeypatch,
) -> None:
    import invincat_cli.wecom.bridge as bridge_module

    class _Ws:
        def __init__(self, *, fail_send: bool = False) -> None:
            self.fail_send = fail_send
            self.closed = False

        async def send(self, _raw: str) -> None:
            if self.fail_send:
                raise OSError("offline")

        async def close(self) -> None:
            self.closed = True

    async def _noop(_message: str) -> None:
        return None

    async def _on_message(_frame: dict) -> None:
        return None

    async def _run() -> None:
        timeout_bridge = WeComBridge(
            on_status=_noop,
            on_error=_noop,
            on_message=_on_message,
            should_exit=lambda: False,
        )
        timeout_ws = _Ws()
        timeout_bridge._ws = timeout_ws
        with pytest.raises(RuntimeError, match="timed out"):
            await timeout_bridge.send_request(
                {"cmd": "aibot_send_msg", "headers": {"req_id": "req-timeout"}},
                timeout=0,
            )
        assert timeout_ws.closed is True
        assert timeout_bridge._ws is None

        send_bridge = WeComBridge(
            on_status=_noop,
            on_error=_noop,
            on_message=_on_message,
            should_exit=lambda: False,
        )
        send_ws = _Ws(fail_send=True)
        send_bridge._ws = send_ws
        with pytest.raises(OSError, match="offline"):
            await send_bridge.send_request(
                {"cmd": "aibot_send_msg", "headers": {"req_id": "req-send"}},
                timeout=1,
            )
        assert send_ws.closed is True
        assert send_bridge._ws is None

    monkeypatch.setattr(bridge_module, "WECOM_STALE_CONNECTION_SECONDS", 0)
    asyncio.run(_run())


def test_wecom_bridge_control_frame_close_and_cleanup_helpers() -> None:
    statuses: list[str] = []
    errors: list[str] = []

    async def _on_status(message: str) -> None:
        statuses.append(message)

    async def _on_error(message: str) -> None:
        errors.append(message)

    async def _on_message(_frame: dict) -> None:
        return None

    bridge = WeComBridge(
        on_status=_on_status,
        on_error=_on_error,
        on_message=_on_message,
        should_exit=lambda: False,
    )
    bridge.enqueue(
        {
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": "req-progress"},
            "body": {"stream": {"finish": False}},
        }
    )
    bridge.enqueue(
        {
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": "req-final"},
            "body": {"stream": {"finish": True}},
        }
    )

    class _Transport:
        def __init__(self) -> None:
            self.aborted = False

        def abort(self) -> None:
            self.aborted = True

    class _BadCloseWs:
        def __init__(self) -> None:
            self.transport = _Transport()

        async def close(self) -> None:
            raise OSError("close failed")

    ws = _BadCloseWs()

    async def _run() -> None:
        assert (
            await bridge._handle_control_frame(
                {"cmd": "aibot_msg_callback"},
                saw_subscribe_ack=False,
            )
            is False
        )
        assert (
            await bridge._handle_control_frame({"errcode": 0}, saw_subscribe_ack=False)
            is True
        )
        assert statuses == ["WeCom subscription acknowledged."]
        assert bridge.ready.is_set()

        bridge.active = True
        assert (
            await bridge._handle_control_frame(
                {"errcode": 7, "errmsg": "bad secret"},
                saw_subscribe_ack=False,
            )
            is True
        )
        assert bridge.active is False
        assert errors == ["WeCom subscribe failed: errcode=7 errmsg=bad secret"]

        await bridge._close_ws(ws)

    asyncio.run(_run())
    assert ws.transport.aborted is True

    bridge._discard_stale_progress_frames()
    assert [frame["headers"]["req_id"] for frame in bridge._outbox] == ["req-final"]

    loop = asyncio.new_event_loop()
    try:
        pending = loop.create_future()
        done = loop.create_future()
        done.set_result({})
        bridge._pending_requests = {"pending": pending, "done": done}
        bridge._cancel_pending_requests()
        assert pending.cancelled() is True
        assert bridge._pending_requests == {}
    finally:
        loop.close()


def test_wecom_bridge_message_task_done_logs_failures() -> None:
    async def _noop(_message: str) -> None:
        return None

    async def _on_message(_frame: dict) -> None:
        return None

    bridge = WeComBridge(
        on_status=_noop,
        on_error=_noop,
        on_message=_on_message,
        should_exit=lambda: False,
    )

    async def _run() -> tuple[asyncio.Task[None], asyncio.Task[None]]:
        async def fail() -> None:
            raise RuntimeError("boom")

        async def wait_forever() -> None:
            await asyncio.sleep(10)

        failed = asyncio.create_task(fail())
        cancelled = asyncio.create_task(wait_forever())
        bridge._message_tasks.update({failed, cancelled})
        await asyncio.sleep(0)
        bridge._on_message_task_done(failed)
        cancelled.cancel()
        with suppress(asyncio.CancelledError):
            await cancelled
        bridge._on_message_task_done(cancelled)
        return failed, cancelled

    failed, cancelled = asyncio.run(_run())

    assert failed not in bridge._message_tasks
    assert cancelled not in bridge._message_tasks


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


def test_wecom_build_agent_input_for_text_and_empty_fallbacks() -> None:
    text_frame = {
        "cmd": "aibot_msg_callback",
        "body": {"msgtype": "text", "text": {"content": "  hello  "}},
    }
    mixed_frame = {
        "cmd": "aibot_msg_callback",
        "body": {
            "msgtype": "mixed",
            "mixed": {
                "msg_item": [
                    {"msgtype": "text", "text": {"content": "part one"}},
                    {"msgtype": "text", "text": {"content": "part two"}},
                ]
            },
        },
    }

    assert build_wecom_agent_input(text_frame, saved_paths=[]) == "hello"
    assert build_wecom_agent_input(mixed_frame, saved_paths=[]) == "part one\npart two"
    assert build_wecom_agent_input({"body": {}}, saved_paths=[]) == (
        "收到企业微信 unknown 消息，但当前无法提取内容。"
    )


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


def test_wecom_filename_uses_hint_path_and_image_fallback() -> None:
    assert (
        wecom_filename_from_response(
            url="https://example.com/download",
            filename_hint="../report final?.txt",
            content_disposition="",
            content_type="",
            media_type="file",
            fallback="file_1",
        )
        == "report_final"
    )
    assert (
        wecom_filename_from_response(
            url="https://example.com/files/report.pdf?token=1",
            filename_hint="",
            content_disposition="",
            content_type="",
            media_type="file",
            fallback="file_1",
        )
        == "report.pdf"
    )
    assert (
        wecom_filename_from_response(
            url="https://example.com/",
            filename_hint="",
            content_disposition="",
            content_type="",
            media_type="image",
            fallback="image_1",
        )
        == "image_1.jpg"
    )


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


def test_wecom_download_inbound_media_handles_duplicate_and_bad_length(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(wecom_media, "WECOM_INBOUND_MEDIA_MAX_BYTES", 3)

    duplicate = tmp_path / ".invincat" / "wecom_downloads" / "dup.txt"
    duplicate.parent.mkdir(parents=True)
    duplicate.write_bytes(b"old")

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-length": "not-int"},
            content=b"ok",
            request=request,
        )
    )

    def _client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    path = asyncio.run(
        download_wecom_inbound_media(
            WeComInboundMedia(
                msgtype="other",
                url="https://example.com/dup.txt",
                aeskey="",
                filename_hint="dup.txt",
            ),
            inbound_frame={"body": {"msgid": "../msg-1"}},
            index=1,
            cwd=tmp_path,
            http_client_factory=_client,
        )
    )

    assert path.parent == duplicate.parent
    assert path.name.startswith("dup_")
    assert path.suffix == ".txt"
    assert path.read_bytes() == b"ok"


def test_wecom_download_inbound_media_rejects_declared_and_decrypted_size(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(wecom_media, "WECOM_INBOUND_MEDIA_MAX_BYTES", 3)

    oversized_transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            headers={"content-length": "4"},
            content=b"",
            request=request,
        )
    )

    def _oversized_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=oversized_transport)

    async def _declared_run() -> None:
        await download_wecom_inbound_media(
            WeComInboundMedia(
                msgtype="other",
                url="https://example.com/media",
                aeskey="",
                filename_hint="",
            ),
            inbound_frame={"body": {"msgid": "msg-1"}},
            index=1,
            cwd=tmp_path,
            http_client_factory=_oversized_client,
        )

    with pytest.raises(ValueError, match="larger than the 20 MB limit"):
        asyncio.run(_declared_run())

    ok_transport = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x", request=request)
    )

    def _ok_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=ok_transport)

    monkeypatch.setattr(
        wecom_media,
        "decrypt_wecom_media_payload",
        lambda _data, _aeskey: b"toolong",
    )

    async def _decrypted_run() -> None:
        await download_wecom_inbound_media(
            WeComInboundMedia(
                msgtype="other",
                url="https://example.com/media",
                aeskey="",
                filename_hint="",
            ),
            inbound_frame={"body": {"msgid": "msg-1"}},
            index=1,
            cwd=tmp_path,
            http_client_factory=_ok_client,
        )

    with pytest.raises(ValueError, match="larger than the 20 MB limit"):
        asyncio.run(_decrypted_run())


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


def test_wecom_agent_input_without_media_delegates_to_protocol(tmp_path: Path) -> None:
    frame = {
        "cmd": "aibot_msg_callback",
        "body": {"msgtype": "text", "text": {"content": "hello"}},
    }

    assert (
        asyncio.run(
            build_wecom_agent_input_with_media_downloads(
                frame,
                cwd=tmp_path,
                http_client_factory=lambda: (_ for _ in ()).throw(
                    AssertionError("no media should not create a client")
                ),
            )
        )
        == "hello"
    )


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


def test_wecom_upload_outbound_media_uses_init_chunks_and_finish(
    tmp_path: Path,
) -> None:
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


def test_wecom_upload_outbound_media_rejects_invalid_inputs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    small = tmp_path / "small.txt"
    small.write_bytes(b"hi")

    async def _send_request(_payload: dict) -> dict:
        return {"body": {}}

    with pytest.raises(ValueError, match="empty file"):
        asyncio.run(upload_wecom_outbound_media(empty, send_request=_send_request))
    with pytest.raises(ValueError, match="chunk size"):
        asyncio.run(
            upload_wecom_outbound_media(
                small,
                send_request=_send_request,
                chunk_size=0,
            )
        )

    import invincat_cli.wecom.file as wecom_file_module

    monkeypatch.setattr(wecom_file_module, "WECOM_FILE_MAX_BYTES", 1)
    with pytest.raises(ValueError, match="larger than the WeCom 20 MB limit"):
        asyncio.run(upload_wecom_outbound_media(small, send_request=_send_request))


def test_wecom_upload_outbound_media_rejects_missing_response_ids(
    tmp_path: Path,
) -> None:
    path = tmp_path / "report.txt"
    path.write_text("hello", encoding="utf-8")

    async def _missing_upload_id(payload: dict) -> dict:
        assert payload["cmd"] == "aibot_upload_media_init"
        return {"body": {}}

    with pytest.raises(RuntimeError, match="missing upload_id"):
        asyncio.run(upload_wecom_outbound_media(path, send_request=_missing_upload_id))

    async def _missing_media_id(payload: dict) -> dict:
        if payload["cmd"] == "aibot_upload_media_init":
            return {"body": {"upload_id": "upload-1"}}
        return {"body": {}}

    with pytest.raises(RuntimeError, match="missing media_id"):
        asyncio.run(upload_wecom_outbound_media(path, send_request=_missing_media_id))


def test_wecom_send_file_from_tool_payload_uploads_and_sends_file(
    tmp_path: Path,
) -> None:
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


def test_wecom_send_file_from_tool_payload_validates_path(tmp_path: Path) -> None:
    async def _send_request(_payload: dict) -> dict:
        return {"body": {}}

    frame = {"body": {"chatid": "chat-1"}}

    with pytest.raises(ValueError, match="missing path"):
        asyncio.run(
            send_wecom_file_from_tool_payload(
                frame,
                {},
                cwd=tmp_path,
                send_request=_send_request,
            )
        )

    outside = tmp_path.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    with pytest.raises(ValueError, match="current project"):
        asyncio.run(
            send_wecom_file_from_tool_payload(
                frame,
                {"path": str(outside)},
                cwd=tmp_path,
                send_request=_send_request,
            )
        )

    missing = tmp_path / "missing.txt"
    with pytest.raises(ValueError, match="not a regular file"):
        asyncio.run(
            send_wecom_file_from_tool_payload(
                frame,
                {"path": str(missing)},
                cwd=tmp_path,
                send_request=_send_request,
            )
        )


def test_wecom_progress_and_error_helpers() -> None:
    assert (
        format_wecom_progress_line(
            running_tool="shell",
            completed_tools=2,
            assistant_started=False,
            tick=1,
        )
        == "处理中：正在执行工具 `shell`，已完成 2 个.."
    )
    assert (
        format_wecom_progress_line(
            running_tool=None,
            completed_tools=0,
            assistant_started=True,
            tick=0,
        )
        == "处理中：正在整理回复."
    )
    assert (
        format_wecom_progress_line(
            running_tool="search",
            completed_tools=0,
            assistant_started=False,
            tick=2,
        )
        == "处理中：正在执行工具 `search`..."
    )
    assert (
        format_wecom_progress_line(
            running_tool=None,
            completed_tools=3,
            assistant_started=True,
            tick=1,
        )
        == "处理中：已完成 3 个工具调用，正在整理回复.."
    )
    assert (
        format_wecom_progress_line(
            running_tool=None,
            completed_tools=1,
            assistant_started=False,
            tick=0,
        )
        == "处理中：已完成 1 个工具调用，正在继续分析."
    )
    assert (
        format_wecom_progress_line(
            running_tool=None,
            completed_tools=0,
            assistant_started=False,
            tick=0,
        )
        == "处理中：正在分析问题."
    )
    assert wecom_user_facing_error(ValueError("bad input")) == "bad input"
    assert wecom_user_facing_error(ValueError()) == "ValueError"


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


def test_wecom_turn_runner_streams_text_and_enters_context(tmp_path: Path) -> None:
    lock = asyncio.Lock()
    messages: list[MessageData] = []
    content: list[str] = []
    context_calls: list[str] = []

    async def _handle_user_message(
        _message: str,
        on_text_delta,
        _on_wecom_file_request,
    ) -> None:
        await on_text_delta("delta", "partial answer")
        messages.append(
            MessageData(
                type=MessageType.ASSISTANT,
                content=" final answer ",
                id="m-answer",
            )
        )

    async def _send_request(_payload: dict) -> dict:
        return {"body": {}}

    runner = WeComTurnRunner(
        lock=lock,
        cwd=tmp_path,
        is_busy=lambda: False,
        get_messages=lambda: list(messages),
        handle_user_message=_handle_user_message,
        send_request=_send_request,
        cancel_timed_out_turn=lambda: None,
        on_content=lambda value: content.append(value) or asyncio.sleep(0),
        enter_turn_context=lambda: context_calls.append("enter"),
        exit_turn_context=lambda: context_calls.append("exit"),
    )

    answer = asyncio.run(runner.run("hello", inbound_frame={"body": {}}))

    assert content == ["partial answer"]
    assert context_calls == ["enter", "exit"]
    assert answer == "final answer"


def test_wecom_turn_runner_busy_timeout_and_lock_race(
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(wecom_turn.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(wecom_turn, "WECOM_IDLE_TIMEOUT", 0.1)

    async def _handle_user_message(
        _message: str,
        _on_text_delta,
        _on_wecom_file_request,
    ) -> None:
        raise AssertionError("should not inject while busy")

    async def _send_request(_payload: dict) -> dict:
        return {"body": {}}

    idle_runner = WeComTurnRunner(
        lock=asyncio.Lock(),
        cwd=tmp_path,
        is_busy=lambda: True,
        get_messages=lambda: [],
        handle_user_message=_handle_user_message,
        send_request=_send_request,
        cancel_timed_out_turn=lambda: None,
    )

    assert (
        asyncio.run(idle_runner.run("hello", inbound_frame={"body": {}}))
        == "当前会话忙碌，请稍后再试。"
    )

    busy_states = iter([False, True])
    race_runner = WeComTurnRunner(
        lock=asyncio.Lock(),
        cwd=tmp_path,
        is_busy=lambda: next(busy_states),
        get_messages=lambda: [],
        handle_user_message=_handle_user_message,
        send_request=_send_request,
        cancel_timed_out_turn=lambda: None,
    )

    assert (
        asyncio.run(race_runner.run("hello", inbound_frame={"body": {}}))
        == "当前会话忙碌，请稍后再试。"
    )


def test_wecom_turn_runner_does_not_hold_lock_while_waiting_for_idle(
    tmp_path: Path,
) -> None:
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


def test_wecom_turn_runner_handles_wecom_file_request_success_and_failure(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sent_payloads: list[dict] = []
    content: list[str] = []
    messages: list[MessageData] = []

    async def fake_send_file(inbound_frame, payload, **kwargs):  # noqa: ANN001
        sent_payloads.append(
            {"inbound_frame": inbound_frame, "payload": payload, **kwargs}
        )
        if payload["path"] == "bad.txt":
            raise RuntimeError("upload failed")

    async def _handle_user_message(
        _message: str,
        _on_text_delta,
        on_wecom_file_request,
    ) -> None:
        await on_wecom_file_request({"path": "ok.txt", "filename": "ok.txt"})
        await on_wecom_file_request({"path": "bad.txt"})
        messages.append(
            MessageData(type=MessageType.ASSISTANT, content="done", id="m-answer")
        )

    async def _send_request(payload: dict) -> dict:
        return {"body": payload}

    monkeypatch.setattr(wecom_turn, "send_wecom_file_from_tool_payload", fake_send_file)
    runner = WeComTurnRunner(
        lock=asyncio.Lock(),
        cwd=tmp_path,
        is_busy=lambda: False,
        get_messages=lambda: list(messages),
        handle_user_message=_handle_user_message,
        send_request=_send_request,
        cancel_timed_out_turn=lambda: None,
        on_content=lambda value: content.append(value) or asyncio.sleep(0),
    )

    assert (
        asyncio.run(runner.run("hello", inbound_frame={"body": {"chatid": "c"}}))
        == "done"
    )
    assert [item["payload"]["path"] for item in sent_payloads] == ["ok.txt", "bad.txt"]
    assert content == ["已发送文件：ok.txt", "文件发送失败：upload failed"]


def test_wecom_turn_runner_suppresses_progress_right_after_file_notification(
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    async def fake_send_file(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(wecom_turn.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(wecom_turn, "send_wecom_file_from_tool_payload", fake_send_file)
    monkeypatch.setattr(wecom_turn, "WECOM_FILE_NOTIFY_HOLD", 60.0)
    busy_states = iter([True, False])
    content: list[str] = []
    messages: list[MessageData] = []

    async def _handle_user_message(
        _message: str,
        _on_text_delta,
        on_wecom_file_request,
    ) -> None:
        await on_wecom_file_request({"path": "report.txt"})
        messages.append(
            MessageData(type=MessageType.ASSISTANT, content="done", id="m-answer")
        )

    async def _send_request(_payload: dict) -> dict:
        return {"body": {}}

    runner = WeComTurnRunner(
        lock=asyncio.Lock(),
        cwd=tmp_path,
        is_busy=lambda: next(busy_states),
        get_messages=lambda: list(messages),
        handle_user_message=_handle_user_message,
        send_request=_send_request,
        cancel_timed_out_turn=lambda: None,
        on_content=lambda value: content.append(value) or asyncio.sleep(0),
    )

    assert (
        asyncio.run(runner._run_locked("hello", inbound_frame={"body": {}})) == "done"
    )
    assert content == ["已发送文件：report.txt"]


def test_wecom_turn_runner_observes_completed_tools_and_tracks_final_tool_ids(
    tmp_path: Path,
) -> None:
    messages = [
        MessageData(
            type=MessageType.TOOL,
            content="",
            id="tool-done",
            tool_name="shell",
            tool_status=ToolStatus.SUCCESS,
        ),
        MessageData(
            type=MessageType.ASSISTANT,
            content="draft",
            id="m-answer",
        ),
    ]

    async def _handle_user_message(
        _message: str,
        _on_text_delta,
        _on_wecom_file_request,
    ) -> None:
        return None

    async def _send_request(_payload: dict) -> dict:
        return {"body": {}}

    runner = WeComTurnRunner(
        lock=asyncio.Lock(),
        cwd=tmp_path,
        is_busy=lambda: False,
        get_messages=lambda: list(messages),
        handle_user_message=_handle_user_message,
        send_request=_send_request,
        cancel_timed_out_turn=lambda: None,
    )
    sent_tool_ids: set[str] = set()

    assert runner._observe_progress(set(), sent_tool_ids, 0) == (None, True, 1)
    assert sent_tool_ids == {"tool-done"}

    sent_tool_ids.clear()
    assert (
        runner._final_answer(
            before_ids=set(),
            before_assistant_count=1,
            before_error_count=0,
            sent_tool_ids=sent_tool_ids,
        )
        == "未获取到有效回复。"
    )
    assert sent_tool_ids == {"tool-done"}


def test_wecom_turn_runner_reports_progress_timeout_and_final_fallbacks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(wecom_turn.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(wecom_turn, "WECOM_AGENT_TIMEOUT", 0.2)
    progress_messages: list[MessageData] = []
    content: list[str] = []
    cancelled: list[bool] = []

    async def _handle_user_message(
        _message: str,
        _on_text_delta,
        _on_wecom_file_request,
    ) -> None:
        progress_messages.append(
            MessageData(
                type=MessageType.TOOL,
                content="",
                id="tool-running",
                tool_name="shell",
                tool_status=ToolStatus.RUNNING,
            )
        )
        return None

    async def _send_request(_payload: dict) -> dict:
        return {"body": {}}

    timeout_runner = WeComTurnRunner(
        lock=asyncio.Lock(),
        cwd=tmp_path,
        is_busy=lambda: True,
        get_messages=lambda: list(progress_messages),
        handle_user_message=_handle_user_message,
        send_request=_send_request,
        cancel_timed_out_turn=lambda: cancelled.append(True),
        on_content=lambda value: content.append(value) or asyncio.sleep(0),
    )

    assert (
        asyncio.run(timeout_runner._run_locked("hello", inbound_frame={"body": {}}))
        == "处理超时，请稍后再试。"
    )
    assert cancelled == [True]
    assert content
    assert "正在执行工具 `shell`" in content[0]

    fallback_runner = WeComTurnRunner(
        lock=asyncio.Lock(),
        cwd=tmp_path,
        is_busy=lambda: False,
        get_messages=lambda: [
            MessageData(
                type=MessageType.TOOL,
                content="",
                id="tool-done",
                tool_name="shell",
                tool_status=ToolStatus.SUCCESS,
            )
        ],
        handle_user_message=_handle_user_message,
        send_request=_send_request,
        cancel_timed_out_turn=lambda: None,
    )
    assert (
        asyncio.run(fallback_runner._run_locked("hello", inbound_frame={"body": {}}))
        == "未获取到有效回复。"
    )

    error_runner = WeComTurnRunner(
        lock=asyncio.Lock(),
        cwd=tmp_path,
        is_busy=lambda: False,
        get_messages=lambda: [
            MessageData(type=MessageType.ERROR, content=" previous ", id="e-before"),
            MessageData(type=MessageType.ERROR, content=" failed ", id="e-after"),
        ],
        handle_user_message=_handle_user_message,
        send_request=_send_request,
        cancel_timed_out_turn=lambda: None,
    )
    assert (
        error_runner._final_answer(
            before_ids={"e-before"},
            before_assistant_count=0,
            before_error_count=1,
            sent_tool_ids=set(),
        )
        == "failed"
    )


def test_wecom_turn_runner_blinks_stream_when_answer_pauses(
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(wecom_turn.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(wecom_turn, "WECOM_STREAM_BLINK_DELAY", 0.0)
    monkeypatch.setattr(wecom_turn, "WECOM_BLINK_INTERVAL", 0.0)
    busy_states = iter([True, False])
    content: list[str] = []
    messages: list[MessageData] = []

    async def _handle_user_message(
        _message: str,
        on_text_delta,
        _on_wecom_file_request,
    ) -> None:
        await on_text_delta("a", "answer")
        messages.append(
            MessageData(type=MessageType.ASSISTANT, content="answer", id="m-answer")
        )

    async def _send_request(_payload: dict) -> dict:
        return {"body": {}}

    runner = WeComTurnRunner(
        lock=asyncio.Lock(),
        cwd=tmp_path,
        is_busy=lambda: next(busy_states),
        get_messages=lambda: list(messages),
        handle_user_message=_handle_user_message,
        send_request=_send_request,
        cancel_timed_out_turn=lambda: None,
        on_content=lambda value: content.append(value) or asyncio.sleep(0),
    )

    assert (
        asyncio.run(runner._run_locked("hello", inbound_frame={"body": {}})) == "answer"
    )
    assert content == ["answer", "answer ▏"]


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


def test_wecom_message_responder_reports_turn_failure_after_stream_flush_failure() -> (
    None
):
    queued: list[dict] = []
    reports: list[str] = []
    flush_count = 0

    def _enqueue(payload: dict) -> None:
        queued.append(payload)

    async def _flush() -> bool:
        nonlocal flush_count
        flush_count += 1
        if flush_count == 2:
            raise RuntimeError("stream closed")
        return True

    async def _build_agent_input(frame: dict) -> str:
        assert frame["body"]["text"] == "hello"
        return "hello"

    async def _run_turn(text, frame, on_content) -> str:  # noqa: ANN001
        await on_content(f"partial {text}")
        raise ValueError("turn failed")

    async def _report_error(message: str) -> None:
        reports.append(message)
        raise RuntimeError("report failed")

    responder = WeComMessageResponder(
        enqueue=_enqueue,
        flush=_flush,
        build_agent_input=_build_agent_input,
        run_turn=_run_turn,
        report_error=_report_error,
    )
    frame = {
        "headers": {"req_id": "inbound-1"},
        "body": {"chatid": "chat-1", "text": "hello"},
    }

    asyncio.run(responder.handle(frame))

    assert flush_count == 3
    assert reports == ["WeCom message failed: turn failed"]
    assert [payload["body"]["stream"]["content"] for payload in queued] == [
        "⏳ 正在处理，请稍候…",
        "partial hello",
        "处理消息时发生异常：turn failed",
    ]
    assert queued[-1]["body"]["stream"]["finish"] is True


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


def test_headless_debounced_content_emitter_sends_cancels_and_swallows_errors() -> None:
    from invincat_cli.wecom.headless import _DebouncedContentEmitter

    emitted: list[str] = []

    async def _run() -> None:
        emitter = _DebouncedContentEmitter(
            lambda content: emitted.append(content) or asyncio.sleep(0),
            interval=100,
        )
        await emitter.emit("first")
        await emitter.emit("second")
        assert emitted == ["first"]
        await emitter.close()
        assert emitted == ["first"]
        await emitter.flush()
        assert emitted == ["first", "second"]
        await emitter.flush()
        assert emitted == ["first", "second"]

        async def fail(_content: str) -> None:
            raise RuntimeError("callback failed")

        failing = _DebouncedContentEmitter(fail, interval=0)
        await failing.emit("ignored")
        assert failing._last_sent == "ignored"

        delayed = _DebouncedContentEmitter(fail, interval=0)
        delayed._latest = "later"
        task = asyncio.create_task(delayed._send_later(0))
        delayed._task = task
        await task
        assert delayed._task is None
        assert delayed._last_sent == "later"

        async def bad_send(_content: str) -> None:
            raise RuntimeError("send later failed")

        later_failure = _DebouncedContentEmitter(
            lambda content: emitted.append(content) or asyncio.sleep(0),
            interval=0,
        )
        later_failure._latest = "not-sent"
        later_failure._send = bad_send
        task = asyncio.create_task(later_failure._send_later(0))
        later_failure._task = task
        await task
        assert later_failure._task is None

        cancellable = _DebouncedContentEmitter(
            lambda content: emitted.append(content) or asyncio.sleep(0),
            interval=100,
        )
        task = asyncio.create_task(cancellable._send_later(100))
        cancellable._task = task
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())


def test_headless_run_turn_sessions_and_chatid_resolution(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from invincat_cli.wecom import headless as headless_module
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    ids = iter(["thread-1", "thread-2", "thread-3", "thread-4"])
    monkeypatch.setattr("invincat_cli.sessions.generate_thread_id", lambda: next(ids))
    monkeypatch.setattr(headless_module, "_MAX_SESSIONS", 2)

    async def send_request(_payload: dict) -> dict:
        return {"errcode": 0}

    handler = HeadlessWeComHandler(
        agent=object(),
        cwd=tmp_path,
        send_request=send_request,
        max_concurrent_turns=0,
    )
    calls: list[tuple[str, str, dict | None]] = []

    async def run_agent_turn(
        text: str,
        *,
        thread_id: str,
        inbound_frame: dict,
        on_content,
        runtime_context: dict | None = None,
    ) -> str:
        calls.append((text, thread_id, runtime_context))
        await on_content(f"seen {inbound_frame['body']}")
        return f"answer {thread_id}"

    monkeypatch.setattr(handler, "_run_agent_turn", run_agent_turn)
    emitted: list[str] = []

    answer1 = asyncio.run(
        handler.run_turn(
            "one",
            {"body": {"chatid": "chat-1"}},
            lambda content: emitted.append(content) or asyncio.sleep(0),
            runtime_context={"x": 1},
        )
    )
    answer2 = asyncio.run(
        handler.run_turn(
            "two",
            {"body": {"from": {"userid": "user-1"}}},
            lambda content: emitted.append(content) or asyncio.sleep(0),
        )
    )
    answer3 = asyncio.run(
        handler.run_turn(
            "three",
            {"body": {}},
            lambda content: emitted.append(content) or asyncio.sleep(0),
        )
    )

    assert [answer1, answer2, answer3] == [
        "answer thread-1",
        "answer thread-2",
        "answer thread-3",
    ]
    assert calls == [
        ("one", "thread-1", {"x": 1}),
        ("two", "thread-2", None),
        ("three", "thread-3", None),
    ]
    assert handler.messages_handled == 3
    assert list(handler._sessions) == ["user-1", "default"]

    existing_default = handler._get_or_create_session("default")
    assert existing_default[0] == "thread-3"
    assert list(handler._sessions)[-1] == "default"


def test_headless_run_turn_reraises_agent_failures(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    async def send_request(_payload: dict) -> dict:
        return {"errcode": 0}

    handler = HeadlessWeComHandler(
        agent=object(),
        cwd=tmp_path,
        send_request=send_request,
    )

    async def fail_turn(*_args: object, **_kwargs: object) -> str:
        raise RuntimeError("agent failed")

    monkeypatch.setattr(handler, "_run_agent_turn", fail_turn)

    with pytest.raises(RuntimeError, match="agent failed"):
        asyncio.run(
            handler.run_turn(
                "hello",
                {"body": {}},
                lambda _content: asyncio.sleep(0),
            )
        )

    assert handler.messages_handled == 0


def test_headless_session_eviction_keeps_locked_entries(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from invincat_cli.wecom import headless as headless_module
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    async def send_request(_payload: dict) -> dict:
        return {"errcode": 0}

    handler = HeadlessWeComHandler(
        agent=object(),
        cwd=tmp_path,
        send_request=send_request,
    )
    locked = asyncio.Lock()
    unlocked = asyncio.Lock()
    extra = asyncio.Lock()
    asyncio.run(locked.acquire())
    handler._sessions = OrderedDict(
        {
            "locked": ("thread-locked", locked),
            "idle": ("thread-idle", unlocked),
            "extra": ("thread-extra", extra),
        }
    )

    monkeypatch.setattr(headless_module, "_MAX_SESSIONS", 1)
    handler._evict_idle_sessions()

    assert list(handler._sessions) == ["locked"]
    locked.release()


def test_headless_extract_ai_text_handles_content_shapes() -> None:
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    assert (
        HeadlessWeComHandler._extract_ai_text(SimpleNamespace(content="hello"))
        == "hello"
    )
    assert (
        HeadlessWeComHandler._extract_ai_text(
            SimpleNamespace(
                content=[
                    "a",
                    {"type": "text", "text": "b"},
                    {"type": "image", "text": "ignored"},
                    123,
                ]
            )
        )
        == "ab"
    )
    assert HeadlessWeComHandler._extract_ai_text(SimpleNamespace(content=123)) == ""


def test_headless_wecom_handler_debounces_fast_text_stream(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    class FakeAgent:
        async def astream(self, *_args, **_kwargs):  # noqa: ANN002, ANN003
            for chunk in ("a", "b", "c", "d"):
                yield ((), "messages", (AIMessage(content=chunk), {}))

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
    emitted: list[str] = []

    async def on_content(content: str) -> None:
        emitted.append(content)

    answer = asyncio.run(
        handler._run_agent_turn(
            "hello",
            thread_id="thread-1",
            inbound_frame={"body": {"chatid": "chat-1"}},
            on_content=on_content,
        )
    )

    assert answer == "abcd"
    assert emitted == ["a", "abcd"]


def test_headless_agent_turn_filters_streams_tools_files_and_resumes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from langgraph.types import Command

    from invincat_cli.wecom.file import WECOM_FILE_REQUEST_TYPE
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    sent_files: list[dict] = []
    stream_inputs: list[object] = []

    async def fake_send_file(inbound_frame, payload, **kwargs):  # noqa: ANN001
        sent_files.append(
            {"inbound_frame": inbound_frame, "payload": payload, **kwargs}
        )

    class FakeAgent:
        async def astream(self, stream_input, **_kwargs):  # noqa: ANN001
            stream_inputs.append(stream_input)
            if len(stream_inputs) == 1:
                yield "bad"
                yield (("subgraph",), "messages", (AIMessage(content="ignored"), {}))
                yield ((), "messages", ("bad-data",))
                yield (
                    (),
                    "messages",
                    (AIMessage(content="memory"), {"lc_source": "memory_agent"}),
                )
                yield (
                    (),
                    "messages",
                    (
                        AIMessage(
                            content="",
                            tool_calls=[{"name": "shell", "args": {}, "id": "tool-1"}],
                        ),
                        {},
                    ),
                )
                yield (
                    (),
                    "messages",
                    (
                        AIMessage(
                            content="",
                            tool_call_chunks=[
                                {
                                    "name": "shell",
                                    "args": "",
                                    "id": "tool-1",
                                    "index": 0,
                                }
                            ],
                        ),
                        {},
                    ),
                )
                yield (
                    (),
                    "messages",
                    (
                        AIMessage(
                            content=[
                                {"type": "tool_use", "name": "python"},
                                {"type": "text", "text": "hi"},
                            ],
                        ),
                        {},
                    ),
                )
                file_payload = {
                    "type": WECOM_FILE_REQUEST_TYPE,
                    "path": str(tmp_path / "report.txt"),
                    "tool_call_id": "file-call",
                }
                for _ in range(2):
                    yield (
                        (),
                        "messages",
                        (
                            ToolMessage(
                                content=json.dumps(file_payload),
                                name=WECOM_FILE_TOOL_NAME,
                                tool_call_id="file-call",
                            ),
                            {},
                        ),
                    )
                yield (
                    (),
                    "updates",
                    {"__interrupt__": [SimpleNamespace(id="interrupt-1")]},
                )
            else:
                assert isinstance(stream_input, Command)
                yield ((), "messages", (AIMessage(content=" there"), {}))

    async def send_request(_payload: dict) -> dict:
        return {"errcode": 0}

    monkeypatch.setattr(
        "invincat_cli.config.build_stream_config",
        lambda _thread_id, _agent_name: {},
    )
    monkeypatch.setattr(
        "invincat_cli.wecom.media.send_wecom_file_from_tool_payload",
        fake_send_file,
    )
    handler = HeadlessWeComHandler(
        agent=FakeAgent(),
        cwd=tmp_path,
        send_request=send_request,
    )
    emitted: list[str] = []

    answer = asyncio.run(
        handler._run_agent_turn(
            "hello",
            thread_id="thread-1",
            inbound_frame={"body": {"chatid": "chat-1"}},
            on_content=lambda content: emitted.append(content) or asyncio.sleep(0),
        )
    )

    assert answer == "hi there"
    assert any("正在执行工具 `shell`" in content for content in emitted)
    assert any("正在执行工具 `python`" in content for content in emitted)
    assert sent_files == [
        {
            "inbound_frame": {"body": {"chatid": "chat-1"}},
            "payload": {
                "type": WECOM_FILE_REQUEST_TYPE,
                "path": str(tmp_path / "report.txt"),
                "tool_call_id": "file-call",
            },
            "cwd": tmp_path,
            "send_request": send_request,
        }
    ]
    assert len(stream_inputs) == 2


def test_headless_agent_turn_swallows_content_and_file_send_errors(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from invincat_cli.wecom.file import WECOM_FILE_REQUEST_TYPE
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    async def fake_send_file(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("send failed")

    class FakeAgent:
        async def astream(self, *_args, **_kwargs):  # noqa: ANN002
            yield (
                (),
                "messages",
                (
                    AIMessage(
                        content="",
                        tool_calls=[{"name": "shell", "args": {}, "id": "tool-1"}],
                    ),
                    {},
                ),
            )
            yield (
                (),
                "messages",
                (
                    ToolMessage(
                        content=json.dumps(
                            {
                                "type": WECOM_FILE_REQUEST_TYPE,
                                "path": str(tmp_path / "report.txt"),
                            }
                        ),
                        name=WECOM_FILE_TOOL_NAME,
                        tool_call_id="file-call",
                    ),
                    {},
                ),
            )

    async def send_request(_payload: dict) -> dict:
        return {"errcode": 0}

    async def fail_content(_content: str) -> None:
        raise RuntimeError("content failed")

    monkeypatch.setattr(
        "invincat_cli.config.build_stream_config",
        lambda _thread_id, _agent_name: {},
    )
    monkeypatch.setattr(
        "invincat_cli.wecom.media.send_wecom_file_from_tool_payload",
        fake_send_file,
    )
    handler = HeadlessWeComHandler(
        agent=FakeAgent(),
        cwd=tmp_path,
        send_request=send_request,
    )

    assert (
        asyncio.run(
            handler._run_agent_turn(
                "hello",
                thread_id="thread-1",
                inbound_frame={"body": {"chatid": "chat-1"}},
                on_content=fail_content,
            )
        )
        == "（空回复）"
    )


def test_headless_agent_turn_closes_emitter_on_cancellation(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    class FakeAgent:
        async def astream(self, *_args, **_kwargs):  # noqa: ANN002
            await asyncio.Event().wait()
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

    async def _run() -> None:
        task = asyncio.create_task(
            handler._run_agent_turn(
                "hello",
                thread_id="thread-1",
                inbound_frame={"body": {"chatid": "chat-1"}},
                on_content=lambda _content: asyncio.sleep(0),
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(_run())


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
        delivery=SimpleNamespace(channels=[{"type": "wecom", "chatid": "chat-1"}]),
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


def test_headless_schedule_payload_create_update_cancel_and_run_now(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime

    from invincat_cli.scheduler.tool import (
        SCHEDULE_CANCEL_TYPE,
        SCHEDULE_CREATE_TYPE,
        SCHEDULE_RUN_NOW_TYPE,
        SCHEDULE_UPDATE_TYPE,
    )
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    saved: list[object] = []
    deleted: list[str] = []
    fired: list[object] = []
    tasks: dict[str, object] = {}
    next_run = datetime(2026, 1, 1, tzinfo=UTC)

    class FakeStore:
        def load_task(self, task_id: str):
            return tasks.get(task_id)

        def save_task(self, task) -> None:  # noqa: ANN001
            saved.append(task)
            tasks[task.id] = task

        def delete_task(self, task_id: str) -> bool:
            deleted.append(task_id)
            tasks.pop(task_id, None)
            return True

    async def send_request(_payload: dict) -> dict:
        return {"errcode": 0}

    async def on_schedule_run_now(task) -> None:  # noqa: ANN001
        fired.append(task)

    monkeypatch.setattr("invincat_cli.scheduler.store.SchedulerStore", FakeStore)
    monkeypatch.setattr(
        "invincat_cli.scheduler.runner.compute_next_run",
        lambda *_args, **_kwargs: next_run,
    )
    handler = HeadlessWeComHandler(
        agent=object(),
        cwd=tmp_path,
        send_request=send_request,
        on_schedule_run_now=on_schedule_run_now,
    )

    asyncio.run(
        handler._process_schedule_payload(
            {
                "type": SCHEDULE_CREATE_TYPE,
                "task_id": "task-1",
                "title": "Daily Report!",
                "cron": "0 8 * * *",
                "timezone": "Asia/Shanghai",
                "prompt": "summarize",
                "schedule_type": "invalid",
                "output_mode": "report",
                "report_format": "markdown",
                "misfire_policy": "run_once",
                "timeout_seconds": 60,
            },
            {
                "body": {
                    "chattype": "single",
                    "from": {"userid": "user-1"},
                }
            },
        )
    )

    created = tasks["task-1"]
    assert created.schedule_type == "recurring"
    assert created.delivery.channels == [{"type": "wecom", "chatid": "user-1"}]
    assert created.report.filename_template == "daily-report-{date}.md"
    assert created.next_run_at == next_run.isoformat()

    asyncio.run(
        handler._process_schedule_payload(
            {
                "type": SCHEDULE_UPDATE_TYPE,
                "task_id": "task-1",
                "updates": {
                    "title": "Updated",
                    "cron": "0 9 * * *",
                    "prompt": "new prompt",
                    "enabled": False,
                    "timezone": "UTC",
                },
            },
            {"body": {"chatid": "chat-1"}},
        )
    )

    updated = tasks["task-1"]
    assert updated.title == "Updated"
    assert updated.cron == "0 9 * * *"
    assert updated.prompt == "new prompt"
    assert updated.enabled is False
    assert updated.timezone == "UTC"
    assert updated.next_run_at == next_run.isoformat()

    asyncio.run(
        handler._process_schedule_payload(
            {"type": SCHEDULE_RUN_NOW_TYPE, "task_id": "task-1"},
            {"body": {"chatid": "chat-1"}},
        )
    )
    assert fired == [updated]

    asyncio.run(
        handler._process_schedule_payload(
            {"type": SCHEDULE_CANCEL_TYPE, "task_id": "task-1"},
            {"body": {"chatid": "chat-1"}},
        )
    )
    assert deleted == ["task-1"]


def test_headless_schedule_payload_create_without_wecom_delivery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime

    from invincat_cli.scheduler.tool import SCHEDULE_CREATE_TYPE
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    saved: list[object] = []
    next_run = datetime(2026, 1, 1, tzinfo=UTC)

    class FakeStore:
        def save_task(self, task) -> None:  # noqa: ANN001
            saved.append(task)

    async def send_request(_payload: dict) -> dict:
        return {"errcode": 0}

    monkeypatch.setattr("invincat_cli.scheduler.store.SchedulerStore", FakeStore)
    monkeypatch.setattr(
        "invincat_cli.scheduler.runner.compute_next_run",
        lambda *_args, **_kwargs: next_run,
    )
    handler = HeadlessWeComHandler(
        agent=object(),
        cwd=tmp_path,
        send_request=send_request,
    )

    asyncio.run(
        handler._process_schedule_payload(
            {
                "type": SCHEDULE_CREATE_TYPE,
                "task_id": "task-no-delivery",
                "title": "No delivery",
                "cron": "0 8 * * *",
                "timezone": "Asia/Shanghai",
            },
            {"body": {"chatid": "__scheduled_task-1"}},
        )
    )

    assert saved[0].delivery.channels == [{"type": "tui"}]


def test_headless_schedule_payload_create_without_active_target_and_next_run_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime

    from invincat_cli.scheduler.tool import SCHEDULE_CREATE_TYPE
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    saved: list[object] = []
    next_run = datetime(2026, 1, 1, tzinfo=UTC)

    class FakeStore:
        def save_task(self, task) -> None:  # noqa: ANN001
            saved.append(task)

    async def send_request(_payload: dict) -> dict:
        return {"errcode": 0}

    monkeypatch.setattr("invincat_cli.scheduler.store.SchedulerStore", FakeStore)
    monkeypatch.setattr(
        "invincat_cli.scheduler.runner.compute_next_run",
        lambda *_args, **_kwargs: next_run,
    )
    handler = HeadlessWeComHandler(
        agent=object(),
        cwd=tmp_path,
        send_request=send_request,
    )

    asyncio.run(
        handler._process_schedule_payload(
            {
                "type": SCHEDULE_CREATE_TYPE,
                "task_id": "task-no-chat",
                "title": "No chat",
                "cron": "0 8 * * *",
                "timezone": "Asia/Shanghai",
            },
            {"body": {}},
        )
    )
    assert saved[0].delivery.channels == [{"type": "tui"}]

    monkeypatch.setattr(
        "invincat_cli.scheduler.runner.compute_next_run",
        lambda *_args, **_kwargs: None,
    )
    with pytest.raises(ValueError, match="Could not compute"):
        asyncio.run(
            handler._process_schedule_payload(
                {
                    "type": SCHEDULE_CREATE_TYPE,
                    "task_id": "task-no-next-run",
                    "title": "No next",
                    "cron": "bad",
                    "timezone": "Asia/Shanghai",
                },
                {"body": {"chatid": "chat-1"}},
            )
        )


def test_headless_schedule_payload_rejects_missing_task_and_update_next_run_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.scheduler.tool import SCHEDULE_UPDATE_TYPE
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    task = SimpleNamespace(
        id="task-1",
        title="Task",
        cwd=str(tmp_path),
        cron="0 8 * * *",
        prompt="old",
        enabled=True,
        timezone="Asia/Shanghai",
        schedule_type="recurring",
        run_at=None,
        next_run_at=None,
        updated_at="old",
    )
    tasks: dict[str, object] = {"task-1": task}

    class FakeStore:
        def load_task(self, task_id: str):
            return tasks.get(task_id)

        def save_task(self, _task) -> None:  # noqa: ANN001
            return None

    async def send_request(_payload: dict) -> dict:
        return {"errcode": 0}

    monkeypatch.setattr("invincat_cli.scheduler.store.SchedulerStore", FakeStore)
    handler = HeadlessWeComHandler(
        agent=object(),
        cwd=tmp_path,
        send_request=send_request,
    )

    with pytest.raises(ValueError, match="not found"):
        asyncio.run(
            handler._process_schedule_payload(
                {
                    "type": SCHEDULE_UPDATE_TYPE,
                    "task_id": "missing",
                    "updates": {"title": "new"},
                },
                {"body": {"chatid": "chat-1"}},
            )
        )

    monkeypatch.setattr(
        "invincat_cli.scheduler.runner.compute_next_run",
        lambda *_args, **_kwargs: None,
    )
    with pytest.raises(ValueError, match="Could not compute"):
        asyncio.run(
            handler._process_schedule_payload(
                {
                    "type": SCHEDULE_UPDATE_TYPE,
                    "task_id": "task-1",
                    "updates": {"cron": "bad"},
                },
                {"body": {"chatid": "chat-1"}},
            )
        )


def test_headless_schedule_payload_rejects_cross_cwd_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.scheduler.tool import SCHEDULE_UPDATE_TYPE
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    saved: list[object] = []
    other_task = SimpleNamespace(
        id="task-other",
        title="Other",
        cwd=str(tmp_path / "other"),
        cron="0 8 * * *",
        prompt="old",
        enabled=True,
        timezone="Asia/Shanghai",
        schedule_type="recurring",
        run_at=None,
        next_run_at=None,
        updated_at="old",
    )

    class FakeStore:
        def load_task(self, _task_id: str):
            return other_task

        def save_task(self, task) -> None:  # noqa: ANN001
            saved.append(task)

    async def send_request(_payload: dict) -> dict:
        return {"errcode": 0}

    monkeypatch.setattr("invincat_cli.scheduler.store.SchedulerStore", FakeStore)
    handler = HeadlessWeComHandler(
        agent=object(),
        cwd=tmp_path / "current",
        send_request=send_request,
    )

    with pytest.raises(ValueError, match="belongs to another project"):
        asyncio.run(
            handler._process_schedule_payload(
                {
                    "type": SCHEDULE_UPDATE_TYPE,
                    "task_id": "task-other",
                    "updates": {"title": "mutated"},
                },
                {"body": {"chatid": "chat-1"}},
            )
        )

    assert saved == []
    assert other_task.title == "Other"


def test_headless_schedule_payload_rejects_invalid_create_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.scheduler.tool import SCHEDULE_CREATE_TYPE
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    saved: list[object] = []

    class FakeStore:
        def save_task(self, task) -> None:  # noqa: ANN001
            saved.append(task)

    async def send_request(_payload: dict) -> dict:
        return {"errcode": 0}

    monkeypatch.setattr("invincat_cli.scheduler.store.SchedulerStore", FakeStore)
    handler = HeadlessWeComHandler(
        agent=object(),
        cwd=tmp_path,
        send_request=send_request,
    )

    with pytest.raises(ValueError, match="misfire_policy"):
        asyncio.run(
            handler._process_schedule_payload(
                {
                    "type": SCHEDULE_CREATE_TYPE,
                    "task_id": "bad-policy",
                    "title": "Bad policy",
                    "cron": "0 8 * * *",
                    "timezone": "Asia/Shanghai",
                    "prompt": "test",
                    "misfire_policy": "later",
                },
                {"body": {"chatid": "chat-1"}},
            )
        )

    assert saved == []


def test_headless_schedule_payload_rejects_cross_cwd_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.scheduler.tool import SCHEDULE_CANCEL_TYPE
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    deleted: list[str] = []
    other_task = SimpleNamespace(id="task-other", cwd=str(tmp_path / "other"))

    class FakeStore:
        def load_task(self, _task_id: str):
            return other_task

        def delete_task(self, task_id: str) -> bool:
            deleted.append(task_id)
            return True

    async def send_request(_payload: dict) -> dict:
        return {"errcode": 0}

    monkeypatch.setattr("invincat_cli.scheduler.store.SchedulerStore", FakeStore)
    handler = HeadlessWeComHandler(
        agent=object(),
        cwd=tmp_path / "current",
        send_request=send_request,
    )

    with pytest.raises(ValueError, match="belongs to another project"):
        asyncio.run(
            handler._process_schedule_payload(
                {"type": SCHEDULE_CANCEL_TYPE, "task_id": "task-other"},
                {"body": {"chatid": "chat-1"}},
            )
        )

    assert deleted == []


def test_headless_schedule_payload_rejects_cross_cwd_run_now(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.scheduler.tool import SCHEDULE_RUN_NOW_TYPE
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    fired: list[object] = []
    other_task = SimpleNamespace(id="task-other", cwd=str(tmp_path / "other"))

    class FakeStore:
        def load_task(self, _task_id: str):
            return other_task

    async def send_request(_payload: dict) -> dict:
        return {"errcode": 0}

    async def on_schedule_run_now(task) -> None:  # noqa: ANN001
        fired.append(task)

    monkeypatch.setattr("invincat_cli.scheduler.store.SchedulerStore", FakeStore)
    handler = HeadlessWeComHandler(
        agent=object(),
        cwd=tmp_path / "current",
        send_request=send_request,
        on_schedule_run_now=on_schedule_run_now,
    )

    with pytest.raises(ValueError, match="belongs to another project"):
        asyncio.run(
            handler._process_schedule_payload(
                {"type": SCHEDULE_RUN_NOW_TYPE, "task_id": "task-other"},
                {"body": {"chatid": "chat-1"}},
            )
        )

    assert fired == []


def test_headless_schedule_payload_error_propagates_from_agent_turn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from invincat_cli.scheduler.tool import SCHEDULE_CANCEL_TYPE
    from invincat_cli.wecom.headless import HeadlessWeComHandler

    other_task = SimpleNamespace(id="task-other", cwd=str(tmp_path / "other"))

    class FakeStore:
        def load_task(self, _task_id: str):
            return other_task

    class FakeAgent:
        async def astream(self, *_args, **_kwargs):  # noqa: ANN002
            yield (
                (),
                "messages",
                (
                    ToolMessage(
                        content=json.dumps(
                            {
                                "type": SCHEDULE_CANCEL_TYPE,
                                "task_id": "task-other",
                            }
                        ),
                        name="cancel_scheduled_task",
                        tool_call_id="call-1",
                    ),
                    {},
                ),
            )

    async def send_request(_payload: dict) -> dict:
        return {"errcode": 0}

    monkeypatch.setattr("invincat_cli.scheduler.store.SchedulerStore", FakeStore)
    monkeypatch.setattr(
        "invincat_cli.config.build_stream_config",
        lambda _thread_id, _agent_name: {},
    )
    handler = HeadlessWeComHandler(
        agent=FakeAgent(),
        cwd=tmp_path / "current",
        send_request=send_request,
    )

    with pytest.raises(ValueError, match="belongs to another project"):
        asyncio.run(
            handler._run_agent_turn(
                "delete it",
                thread_id="thread-1",
                inbound_frame={"body": {"chatid": "chat-1"}},
                on_content=lambda _content: asyncio.sleep(0),
            )
        )


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


def test_wecom_file_tool_filter_keeps_unknown_tool_objects(tmp_path) -> None:
    middleware = WeComFileMiddleware(allowed_root=tmp_path)
    unknown = object()
    request = _ModelRequest(tools=[*middleware.tools, unknown, {"name": "other"}])

    response = middleware.wrap_model_call(request, lambda req: req.tools)

    assert response == [unknown, {"name": "other"}]


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


def test_wecom_file_tool_allows_non_wecom_tool_and_wecom_runtime(tmp_path) -> None:
    middleware = WeComFileMiddleware(allowed_root=tmp_path)
    other_request = ToolCallRequest(
        tool_call={"name": "other", "id": "call-1", "args": {}},
        tool=None,
        state={},
        runtime=SimpleNamespace(context={}),
    )
    allowed_request = ToolCallRequest(
        tool_call={"name": WECOM_FILE_TOOL_NAME, "id": "call-2", "args": {}},
        tool=None,
        state={},
        runtime=SimpleNamespace(context={WECOM_CONTEXT_FLAG: True}),
    )

    other = middleware.wrap_tool_call(
        other_request,
        lambda _request: ToolMessage("other ran", tool_call_id="call-1"),
    )
    allowed = middleware.wrap_tool_call(
        allowed_request,
        lambda _request: ToolMessage("wecom ran", tool_call_id="call-2"),
    )

    assert other.content == "other ran"
    assert allowed.content == "wecom ran"


def test_wecom_file_tool_async_wrappers(tmp_path) -> None:
    middleware = WeComFileMiddleware(allowed_root=tmp_path)
    request = _ModelRequest(tools=[*middleware.tools, {"name": "other"}])

    async def _model_handler(req: _ModelRequest) -> list[object]:
        return req.tools

    assert asyncio.run(middleware.awrap_model_call(request, _model_handler)) == [
        {"name": "other"}
    ]

    rejected_request = ToolCallRequest(
        tool_call={"name": WECOM_FILE_TOOL_NAME, "id": "call-1", "args": {}},
        tool=None,
        state={},
        runtime=SimpleNamespace(context={}),
    )

    async def _tool_handler(_request: ToolCallRequest) -> ToolMessage:
        return ToolMessage("ran", tool_call_id="call-1")

    rejected = asyncio.run(middleware.awrap_tool_call(rejected_request, _tool_handler))
    assert rejected.status == "error"

    allowed_request = ToolCallRequest(
        tool_call={"name": WECOM_FILE_TOOL_NAME, "id": "call-2", "args": {}},
        tool=None,
        state={},
        runtime=SimpleNamespace(context={WECOM_CONTEXT_FLAG: True}),
    )

    async def _allowed_handler(_request: ToolCallRequest) -> ToolMessage:
        return ToolMessage("ran async", tool_call_id="call-2")

    allowed = asyncio.run(middleware.awrap_tool_call(allowed_request, _allowed_handler))
    assert allowed.content == "ran async"


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


def test_wecom_file_tool_rejects_missing_empty_and_oversized_files(
    monkeypatch,
    tmp_path,
) -> None:
    middleware = WeComFileMiddleware(allowed_root=tmp_path)

    with pytest.raises(ValueError, match="not a regular file"):
        middleware._resolve_allowed_file("missing.txt")

    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    with pytest.raises(ValueError, match="empty file"):
        middleware._resolve_allowed_file("empty.txt")

    large = tmp_path / "large.txt"
    large.write_bytes(b"hi")
    monkeypatch.setattr(wecom_file, "WECOM_FILE_MAX_BYTES", 1)
    with pytest.raises(ValueError, match="larger than the WeCom 20 MB limit"):
        middleware._resolve_allowed_file("large.txt")


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
    assert parse_wecom_file_request("") is None
    assert parse_wecom_file_request("not json") is None
    assert parse_wecom_file_request("[1]") is None
    assert parse_wecom_file_request(json.dumps({"type": "other"})) is None
    assert (
        parse_wecom_file_request(
            [
                {"type": "image", "text": "ignored"},
                {"type": "text", "text": json.dumps({"type": "other"})},
            ]
        )
        is None
    )
    payload = {"type": "wecom_send_file", "path": "/tmp/report.txt"}
    assert (
        parse_wecom_file_request([{"type": "text", "text": json.dumps(payload)}])
        == payload
    )
    assert WECOM_FILE_MAX_BYTES == 20 * 1024 * 1024
