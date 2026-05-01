"""Pure helpers for WeCom protocol frames and inbound message parsing."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from invincat_cli.wecom.media import WECOM_INBOUND_MEDIA_TYPES


@dataclass(frozen=True)
class WeComInboundMedia:
    msgtype: str
    url: str
    aeskey: str
    filename_hint: str


def wecom_req_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def extract_wecom_text_message(frame: dict[str, Any]) -> str | None:
    if frame.get("cmd") != "aibot_msg_callback":
        return None
    body = frame.get("body") or {}
    if body.get("msgtype") != "text":
        return None
    text_obj = body.get("text") or {}
    content = text_obj.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    return None


def is_supported_wecom_message_frame(frame: dict[str, Any]) -> bool:
    if frame.get("cmd") != "aibot_msg_callback":
        return False
    body = frame.get("body") or {}
    msgtype = body.get("msgtype")
    return msgtype in {"text", "file", "image", "mixed", "voice"}


def extract_wecom_inbound_media(frame: dict[str, Any]) -> list[WeComInboundMedia]:
    """Extract downloadable media descriptors from a WeCom callback frame."""
    if frame.get("cmd") != "aibot_msg_callback":
        return []
    body = frame.get("body") or {}
    msgtype = body.get("msgtype")

    def _from_payload(media_type: str, payload: Any) -> WeComInboundMedia | None:  # noqa: ANN401
        if not isinstance(payload, dict):
            return None
        url = (
            payload.get("url")
            or payload.get("download_url")
            or payload.get("downloadUrl")
            or payload.get("file_url")
            or payload.get("fileUrl")
        )
        aeskey = payload.get("aeskey") or payload.get("aes_key") or payload.get("aesKey")
        if not isinstance(url, str) or not url:
            return None
        filename_hint = payload.get("filename") or payload.get("name") or ""
        return WeComInboundMedia(
            msgtype=media_type,
            url=url,
            aeskey=aeskey if isinstance(aeskey, str) else "",
            filename_hint=str(filename_hint or ""),
        )

    if msgtype in WECOM_INBOUND_MEDIA_TYPES:
        media = _from_payload(str(msgtype), body.get(str(msgtype)))
        return [media] if media is not None else []

    if msgtype != "mixed":
        return []
    mixed = body.get("mixed") or {}
    items = mixed.get("msg_item") if isinstance(mixed, dict) else None
    if not isinstance(items, list):
        return []
    media_items: list[WeComInboundMedia] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_type = item.get("msgtype")
        if item_type in WECOM_INBOUND_MEDIA_TYPES:
            media = _from_payload(str(item_type), item.get(str(item_type)))
            if media is not None:
                media_items.append(media)
    return media_items


def extract_wecom_mixed_text(frame: dict[str, Any]) -> str:
    body = frame.get("body") or {}
    mixed = body.get("mixed") or {}
    items = mixed.get("msg_item") if isinstance(mixed, dict) else None
    if not isinstance(items, list):
        return ""
    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict) or item.get("msgtype") != "text":
            continue
        text_obj = item.get("text") or {}
        content = text_obj.get("content") if isinstance(text_obj, dict) else None
        if isinstance(content, str) and content.strip():
            parts.append(content.strip())
    return "\n".join(parts)


def extract_wecom_voice_text(frame: dict[str, Any]) -> str | None:
    if frame.get("cmd") != "aibot_msg_callback":
        return None
    body = frame.get("body") or {}
    if body.get("msgtype") != "voice":
        return None
    voice = body.get("voice") or {}
    if not isinstance(voice, dict):
        return None
    for key in ("recognition", "content", "text"):
        value = voice.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def build_wecom_subscribe_frame(bot_id: str, secret: str) -> dict[str, Any]:
    return {
        "cmd": "aibot_subscribe",
        "headers": {"req_id": wecom_req_id("aibot_subscribe")},
        "body": {"bot_id": bot_id, "secret": secret},
    }


def build_wecom_ping_frame() -> dict[str, Any]:
    return {
        "cmd": "ping",
        "headers": {"req_id": wecom_req_id("ping")},
        "body": {},
    }


def safe_wecom_content(content: str, max_bytes: int = 20480) -> str:
    """Normalize content to valid UTF-8 and enforce the byte-size limit."""
    safe = content.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")
    safe = "".join(ch for ch in safe if (ch >= " " or ch in "\n\t\r"))
    encoded = safe.encode("utf-8")
    if len(encoded) > max_bytes:
        safe = encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n\n(输出过长，已截断)"
    return safe if safe.strip() else "（空回复）"


def build_wecom_stream_frame(
    inbound_frame: dict[str, Any],
    stream_id: str,
    content: str,
    *,
    finish: bool,
) -> dict[str, Any]:
    """Build a stream-type reply frame using the official WeCom streaming API."""
    inbound_req_id = ((inbound_frame.get("headers") or {}).get("req_id")) or ""
    inbound_body = inbound_frame.get("body") or {}
    chatid = inbound_body.get("chatid")
    safe = safe_wecom_content(content)
    body: dict[str, Any] = {
        "msgtype": "stream",
        "stream": {"id": stream_id, "content": safe, "finish": finish},
    }
    if isinstance(chatid, str) and chatid:
        body["chatid"] = chatid
    return {"cmd": "aibot_respond_msg", "headers": {"req_id": inbound_req_id}, "body": body}


def build_wecom_file_frame(
    inbound_frame: dict[str, Any],
    media_id: str,
) -> dict[str, Any]:
    """Build an active file send frame for an uploaded WeCom media id."""
    chatid = resolve_wecom_active_chat_id(inbound_frame)
    return {
        "cmd": "aibot_send_msg",
        "headers": {"req_id": wecom_req_id("aibot_send_msg")},
        "body": {
            "msgtype": "file",
            "file": {"media_id": media_id},
            "chatid": chatid,
        },
    }


def resolve_wecom_active_chat_id(inbound_frame: dict[str, Any]) -> str:
    """Resolve the active-send target from a WeCom message callback."""
    inbound_body = inbound_frame.get("body") or {}
    chatid = inbound_body.get("chatid")
    if isinstance(chatid, str) and chatid:
        return chatid
    chattype = inbound_body.get("chattype")
    from_obj = inbound_body.get("from") or {}
    from_userid = from_obj.get("userid") if isinstance(from_obj, dict) else None
    if chattype == "single" and isinstance(from_userid, str) and from_userid:
        return from_userid
    raise RuntimeError(
        "WeCom callback missing active-send target: expected body.chatid or "
        "body.from.userid for single chat"
    )


def wecom_frame_req_id(frame: dict[str, Any]) -> str:
    return str((frame.get("headers") or {}).get("req_id") or "")


def build_wecom_agent_input(
    frame: dict[str, Any],
    *,
    saved_paths: list[Path],
) -> str:
    """Build the user text injected into the agent for an inbound WeCom frame."""
    text = extract_wecom_text_message(frame)
    if text is not None:
        return text
    voice_text = extract_wecom_voice_text(frame)
    if voice_text is not None:
        return voice_text

    body = frame.get("body") or {}
    msgtype = body.get("msgtype")
    mixed_text = extract_wecom_mixed_text(frame) if msgtype == "mixed" else ""
    if not saved_paths:
        return mixed_text or f"收到企业微信 {msgtype or 'unknown'} 消息，但当前无法提取内容。"

    lines: list[str] = []
    if mixed_text:
        lines.append(mixed_text)
        lines.append("")
    noun = "文件" if msgtype == "file" else "附件"
    lines.append(f"用户通过企业微信发送了{noun}，已下载到本地：")
    lines.extend(f"- {path}" for path in saved_paths)
    lines.append("")
    lines.append("请根据用户需求处理这些本地文件；如需查看内容，可以直接读取上述路径。")
    return "\n".join(lines)
