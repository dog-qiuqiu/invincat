"""Helpers for WeCom inbound media handling."""

from __future__ import annotations

import asyncio
import base64
import email.message
import hashlib
import logging
import mimetypes
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from invincat_cli.wecom.protocol import WeComInboundMedia

logger = logging.getLogger(__name__)

WECOM_INBOUND_MEDIA_TYPES = {"file", "image"}
WECOM_INBOUND_MEDIA_MAX_BYTES = 20 * 1024 * 1024
WECOM_AES_CBC_PADDING_MAX_BYTES = 32
WECOM_UPLOAD_CHUNK_BYTES = 512 * 1024


def create_wecom_media_http_client() -> Any:
    """Create the HTTP client used for inbound WeCom media downloads."""
    import httpx

    return httpx.AsyncClient(follow_redirects=True, timeout=60.0)


def decode_wecom_media_aes_key(aeskey: str) -> bytes:
    padded = aeskey + ("=" * ((4 - len(aeskey) % 4) % 4))
    try:
        key = base64.b64decode(padded, validate=True)
    except Exception:
        key = base64.urlsafe_b64decode(padded)
    if len(key) != 32:  # noqa: PLR2004
        raise ValueError("WeCom media aeskey must decode to 32 bytes")
    return key


def decrypt_wecom_media_payload(data: bytes, aeskey: str) -> bytes:
    """Decrypt WeCom inbound media encrypted with AES-256-CBC."""
    if not aeskey:
        return data
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = decode_wecom_media_aes_key(aeskey)
    decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
    decrypted = decryptor.update(data) + decryptor.finalize()
    if not decrypted:
        raise ValueError("WeCom media decrypted to empty payload")
    pad_len = decrypted[-1]
    if (
        pad_len < 1
        or pad_len > WECOM_AES_CBC_PADDING_MAX_BYTES
        or pad_len > len(decrypted)
    ):
        raise ValueError(f"Invalid WeCom media padding value: {pad_len}")
    padding = decrypted[-pad_len:]
    if any(byte != pad_len for byte in padding):
        raise ValueError("Invalid WeCom media padding bytes")
    return decrypted[:-pad_len]


def safe_wecom_filename(name: str, *, default: str) -> str:
    candidate = unquote(name).split("?", 1)[0].split("#", 1)[0].strip()
    candidate = Path(candidate).name
    safe = "".join(ch if ch.isalnum() or ch in ".-_" else "_" for ch in candidate)
    safe = safe.strip("._")
    return safe or default


def wecom_filename_from_content_disposition(value: str) -> str:
    if not value:
        return ""
    message = email.message.Message()
    message["content-disposition"] = value
    filename = message.get_filename()
    return filename or ""


def wecom_filename_from_response(
    *,
    url: str,
    filename_hint: str,
    content_disposition: str,
    content_type: str,
    media_type: str,
    fallback: str,
) -> str:
    if filename_hint:
        return safe_wecom_filename(filename_hint, default=fallback)
    from_header = safe_wecom_filename(
        wecom_filename_from_content_disposition(content_disposition),
        default="",
    )
    if from_header:
        return from_header
    parsed = urlparse(url)
    from_path = safe_wecom_filename(Path(parsed.path).name, default="")
    if from_path:
        return from_path
    ext = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) or ""
    if media_type == "image" and not ext:
        ext = ".jpg"
    return safe_wecom_filename(f"{fallback}{ext}", default=fallback)


def validate_wecom_media_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Invalid WeCom media URL")


async def download_wecom_inbound_media(
    media: WeComInboundMedia,
    *,
    inbound_frame: dict[str, Any],
    index: int,
    cwd: str | Path,
    http_client_factory: Any,
) -> Path:
    """Download, decrypt, and persist one inbound WeCom media item."""
    validate_wecom_media_url(media.url)
    if media.msgtype in WECOM_INBOUND_MEDIA_TYPES and not media.aeskey:
        raise ValueError(f"WeCom {media.msgtype} message is missing aeskey")

    encrypted_parts: list[bytes] = []
    encrypted_size = 0
    encrypted_max_bytes = (
        WECOM_INBOUND_MEDIA_MAX_BYTES + WECOM_AES_CBC_PADDING_MAX_BYTES
        if media.aeskey
        else WECOM_INBOUND_MEDIA_MAX_BYTES
    )
    content_type = ""
    content_disposition = ""
    async with http_client_factory() as client:
        async with client.stream("GET", media.url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            content_disposition = response.headers.get("content-disposition", "")
            content_length = response.headers.get("content-length")
            if content_length is not None:
                try:
                    declared_size = int(content_length)
                except ValueError:
                    declared_size = 0
                if declared_size > encrypted_max_bytes:
                    raise ValueError(
                        "Inbound WeCom media is larger than the 20 MB limit"
                    )
            async for chunk in response.aiter_bytes():
                encrypted_size += len(chunk)
                if encrypted_size > encrypted_max_bytes:
                    raise ValueError(
                        "Inbound WeCom media is larger than the 20 MB limit"
                    )
                encrypted_parts.append(chunk)

    encrypted = b"".join(encrypted_parts)
    data = await asyncio.to_thread(decrypt_wecom_media_payload, encrypted, media.aeskey)
    if len(data) > WECOM_INBOUND_MEDIA_MAX_BYTES:
        raise ValueError("Inbound WeCom media is larger than the 20 MB limit")

    body = inbound_frame.get("body") or {}
    msgid = safe_wecom_filename(
        str(body.get("msgid") or uuid.uuid4().hex),
        default="message",
    )
    fallback = f"{media.msgtype}_{msgid}_{index}"
    filename = wecom_filename_from_response(
        url=media.url,
        filename_hint=media.filename_hint,
        content_disposition=content_disposition,
        content_type=content_type,
        media_type=media.msgtype,
        fallback=fallback,
    )
    target_dir = Path(cwd).expanduser().resolve() / ".invincat" / "wecom_downloads"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / filename
    if target.exists():
        stem = target.stem or fallback
        suffix = target.suffix
        target = target_dir / f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"

    await asyncio.to_thread(target.write_bytes, data)
    return target


async def build_wecom_agent_input_with_media_downloads(
    frame: dict[str, Any],
    *,
    cwd: str | Path,
    http_client_factory: Any = create_wecom_media_http_client,
) -> str:
    """Download inbound media, if present, and build the text injected into the agent."""
    from invincat_cli.wecom.protocol import (
        build_wecom_agent_input,
        extract_wecom_inbound_media,
    )

    media_items = extract_wecom_inbound_media(frame)
    if not media_items:
        return build_wecom_agent_input(frame, saved_paths=[])

    saved_paths: list[Path] = []
    for index, media in enumerate(media_items, start=1):
        target = await download_wecom_inbound_media(
            media,
            inbound_frame=frame,
            index=index,
            cwd=cwd,
            http_client_factory=http_client_factory,
        )
        logger.info(
            "wecom inbound media downloaded msgtype=%s path=%s size=%d",
            media.msgtype,
            target,
            target.stat().st_size,
        )
        saved_paths.append(target)

    return build_wecom_agent_input(frame, saved_paths=saved_paths)


async def upload_wecom_outbound_media(
    path: Path,
    *,
    send_request: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
    chunk_size: int = WECOM_UPLOAD_CHUNK_BYTES,
) -> str:
    """Upload a local file through WeCom's long-connection media protocol."""
    data = await asyncio.to_thread(path.read_bytes)
    size = len(data)
    if size <= 0:
        raise ValueError("Cannot send an empty file")
    from invincat_cli.wecom.file import WECOM_FILE_MAX_BYTES
    from invincat_cli.wecom.protocol import wecom_req_id

    if size > WECOM_FILE_MAX_BYTES:
        raise ValueError("File is larger than the WeCom 20 MB limit")
    if chunk_size <= 0:
        raise ValueError("WeCom upload chunk size must be positive")

    chunks = [data[i : i + chunk_size] for i in range(0, size, chunk_size)]
    logger.info(
        "wecom file upload start path=%s size=%d chunks=%d",
        path,
        size,
        len(chunks),
    )
    init_frame = {
        "cmd": "aibot_upload_media_init",
        "headers": {"req_id": wecom_req_id("aibot_upload_media_init")},
        "body": {
            "type": "file",
            "filename": path.name,
            "total_size": size,
            "total_chunks": len(chunks),
            "md5": hashlib.md5(data).hexdigest(),  # noqa: S324  # protocol checksum
        },
    }
    init_resp = await send_request(init_frame)
    init_body = init_resp.get("body") or {}
    upload_id = init_body.get("upload_id")
    if not isinstance(upload_id, str) or not upload_id:
        raise RuntimeError("WeCom upload init response missing upload_id")
    logger.debug("wecom file upload initialized upload_id=%s", upload_id)

    for index, chunk in enumerate(chunks):
        logger.debug(
            "wecom file upload chunk upload_id=%s index=%d size=%d",
            upload_id,
            index,
            len(chunk),
        )
        chunk_frame = {
            "cmd": "aibot_upload_media_chunk",
            "headers": {"req_id": wecom_req_id("aibot_upload_media_chunk")},
            "body": {
                "upload_id": upload_id,
                "chunk_index": index,
                "base64_data": base64.b64encode(chunk).decode("ascii"),
            },
        }
        await send_request(chunk_frame)

    finish_frame = {
        "cmd": "aibot_upload_media_finish",
        "headers": {"req_id": wecom_req_id("aibot_upload_media_finish")},
        "body": {"upload_id": upload_id},
    }
    finish_resp = await send_request(finish_frame)
    finish_body = finish_resp.get("body") or {}
    media_id = finish_body.get("media_id")
    if not isinstance(media_id, str) or not media_id:
        raise RuntimeError("WeCom upload finish response missing media_id")
    logger.info(
        "wecom file upload finish path=%s media_id=%s",
        path,
        media_id,
    )
    return media_id


async def send_wecom_file_from_tool_payload(
    frame: dict[str, Any],
    payload: dict[str, Any],
    *,
    cwd: str | Path,
    send_request: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
) -> None:
    """Handle a send_wecom_file tool payload by uploading and replying."""
    from invincat_cli.wecom.protocol import (
        build_wecom_file_frame,
        resolve_wecom_active_chat_id,
        wecom_frame_req_id,
    )

    raw_path = str(payload.get("path") or "").strip()
    if not raw_path:
        raise ValueError("send_wecom_file payload missing path")
    path = Path(raw_path).expanduser().resolve()
    root = Path(cwd).expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(
            f"WeCom file sending is limited to the current project: {root}"
        ) from exc
    if not path.is_file():
        raise ValueError(f"File does not exist or is not a regular file: {path}")

    inbound_body = frame.get("body") or {}
    target_chat_id = resolve_wecom_active_chat_id(frame)
    logger.info(
        "wecom file send requested path=%s target_chatid=%s inbound_chatid=%s chattype=%s inbound_req_id=%s",
        path,
        target_chat_id,
        inbound_body.get("chatid", ""),
        inbound_body.get("chattype", ""),
        wecom_frame_req_id(frame),
    )
    media_id = await upload_wecom_outbound_media(path, send_request=send_request)
    await send_request(build_wecom_file_frame(frame, media_id))
    logger.info("wecom file send completed path=%s media_id=%s", path, media_id)
