"""Helpers for WeCom inbound media handling."""

from __future__ import annotations

import asyncio
import base64
import email.message
import mimetypes
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from invincat_cli.wecom_protocol import WeComInboundMedia

WECOM_INBOUND_MEDIA_TYPES = {"file", "image"}
WECOM_INBOUND_MEDIA_MAX_BYTES = 20 * 1024 * 1024
WECOM_AES_CBC_PADDING_MAX_BYTES = 32


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
    media: "WeComInboundMedia",
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
                    raise ValueError("Inbound WeCom media is larger than the 20 MB limit")
            async for chunk in response.aiter_bytes():
                encrypted_size += len(chunk)
                if encrypted_size > encrypted_max_bytes:
                    raise ValueError("Inbound WeCom media is larger than the 20 MB limit")
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
