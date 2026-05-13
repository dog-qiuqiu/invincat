"""Exception formatting and retry classification for the Textual app."""

from __future__ import annotations

import json
from typing import Any


def format_exception_details(exc: BaseException) -> str:
    """Render exception details for UI without flattening structured payloads."""
    payload = extract_exception_payload(exc)
    if payload is not None:
        try:
            return json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return str(payload)

    text = str(exc).strip()
    if not text:
        return type(exc).__name__
    if type(exc).__name__ in text:
        return text
    return f"{type(exc).__name__}: {text}"


def extract_exception_payload(exc: BaseException) -> dict[str, Any] | None:
    """Extract structured payload from wrapped remote exceptions."""
    if exc.args and isinstance(exc.args[0], dict):
        return exc.args[0]
    data_attr = getattr(exc, "data", None)
    if isinstance(data_attr, dict):
        return data_attr
    return None


def looks_like_masked_internal_error(exc: BaseException) -> bool:
    """Detect generic upstream internal errors with low diagnostic value."""
    payload = extract_exception_payload(exc)
    if payload is not None:
        message = str(payload.get("message", "")).strip().lower()
        return message == "an internal error occurred"
    return "an internal error occurred" in str(exc).lower()


def is_scheduled_retryable_error(exc: BaseException) -> bool:
    """Return True for transient model/network errors worth retrying once."""
    names = {cls.__name__ for cls in type(exc).__mro__}
    if names & {
        "ConnectError",
        "ConnectTimeout",
        "ReadError",
        "ReadTimeout",
        "RemoteProtocolError",
        "TransportError",
        "WriteError",
        "WriteTimeout",
        "PoolTimeout",
        "NetworkError",
        "APIConnectionError",
        "APITimeoutError",
        "InternalServerError",
        "RateLimitError",
    }:
        return True
    payload = extract_exception_payload(exc)
    text_parts = [type(exc).__name__, str(exc)]
    if payload is not None:
        text_parts.extend(str(v) for v in payload.values())
    text = " ".join(text_parts).lower()
    return any(
        marker in text
        for marker in (
            "readerror",
            "read error",
            "timeout",
            "timed out",
            "connection",
            "network",
            "transport",
            "temporarily unavailable",
            "rate limit",
            "429",
            "500",
            "502",
            "503",
            "504",
            "an internal error occurred",
        )
    )
