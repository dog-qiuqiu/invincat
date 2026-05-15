"""Core normalization helpers for structured memory stores."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from invincat_cli.memory import signals as _memory_signals

MAX_ITEM_CONTENT_CHARS = 500
MAX_SECTION_NAME_CHARS = 80
MAX_REASON_CHARS = 160
_MAX_OUTPUT_TOKENS = 2000

DEFAULT_TIER = "warm"
DEFAULT_SCORE = 50
HOT_THRESHOLD = 70
COLD_THRESHOLD = 30
MAX_HOT_ITEMS_PER_SCOPE = 8
MAX_WARM_ITEMS_PER_SCOPE = 6
MAX_ARCHIVED_ITEMS_PER_SCOPE = 50

_MEMORY_SIGNAL_RE = _memory_signals.MEMORY_SIGNAL_RE
_env_int = _memory_signals.env_int
_env_float = _memory_signals.env_float
_is_trivial_turn = _memory_signals.is_trivial_turn
_last_human_text = _memory_signals.last_human_text
_is_explicit_memory_request = _memory_signals.is_explicit_memory_request
_detect_target_language = _memory_signals.detect_target_language
_is_task_complete = _memory_signals.is_task_complete


_ITEM_ID_PATTERNS: dict[str, re.Pattern[str]] = {
    "user": re.compile(r"^mem_u_(\d{6})$"),
    "project": re.compile(r"^mem_p_(\d{6})$"),
}
_ITEM_ID_PREFIX: dict[str, str] = {"user": "mem_u_", "project": "mem_p_"}
_ALLOWED_SCOPE: frozenset[str] = frozenset({"user", "project"})
_ALLOWED_STATUS: frozenset[str] = frozenset({"active", "archived"})
_ALLOWED_CONFIDENCE: frozenset[str] = frozenset({"low", "medium", "high"})
_ALLOWED_TIER: frozenset[str] = frozenset({"hot", "warm", "cold"})
_ALLOWED_OPS: frozenset[str] = frozenset(
    {"create", "update", "rescore", "retier", "archive", "delete", "noop"}
)
_INVALID_FACT_REASON_RE = re.compile(
    r"\b("
    r"no longer valid|no longer true|not valid|not true|contradict(?:ed|s)?|"
    r"false|incorrect|wrong|superseded|replaced|obsolete|outdated|stale|"
    r"invalid|misleading|inaccurate|no longer accurate|no longer applies|"
    r"conflict(?:s|ed)? with|conflicts current facts|changed facts|latest facts|"
    r"fixed|resolved|repaired|patched|addressed|closed|no longer reproducible|"
    r"bug fixed|issue resolved"
    r")\b|"
    r"(事实不符|不符合事实|不符合当前事实|与事实不符|与当前事实不符|"
    r"事实不一致|与事实不一致|与当前事实不一致|当前事实不一致|"
    r"不再有效|不再适用|不再正确|不再准确|不准确|不成立|"
    r"已过期|过时|被替代|已替代|矛盾|冲突|错误|不正确|会误导|失效|"
    r"事实已变|事实变化|事实改变|当前事实已变|最新事实|"
    r"已修复|修复了|已经修复|已解决|解决了|已经解决|已处理|处理了|"
    r"已关闭|不再复现|问题已修复|bug已修复|缺陷已修复)",
    re.IGNORECASE,
)


def _iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_store(scope: str) -> dict[str, Any]:
    return {"version": 1, "scope": scope, "items": []}


def _normalize_scope(scope: Any) -> str | None:
    if not isinstance(scope, str):
        return None
    normalized = scope.strip().lower()
    if normalized in _ALLOWED_SCOPE:
        return normalized
    return None


def _normalize_status(status: Any) -> str:
    if isinstance(status, str):
        normalized = status.strip().lower()
        if normalized in _ALLOWED_STATUS:
            return normalized
    return "active"


def _normalize_confidence(value: Any, default: str = "medium") -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _ALLOWED_CONFIDENCE:
            return normalized
    return default


def _normalize_tier(value: Any, *, default: str = DEFAULT_TIER) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _ALLOWED_TIER:
            return normalized
    return default


def _normalize_score(value: Any, *, default: int = DEFAULT_SCORE) -> int:
    try:
        score = int(value)
    except (TypeError, ValueError):
        score = int(default)
    return max(0, min(100, score))


def _derive_tier_from_score(score: int) -> str:
    if score >= HOT_THRESHOLD:
        return "hot"
    if score < COLD_THRESHOLD:
        return "cold"
    return "warm"


def _normalize_reason(value: Any) -> str:
    return _normalize_text(value, max_chars=MAX_REASON_CHARS)


def _raw_reason(raw: dict[str, Any]) -> Any:
    """Return the current reason field with legacy score_reason fallback."""
    if raw.get("reason") is not None:
        return raw.get("reason")
    return raw.get("score_reason")


def _reason_implies_invalid_fact(reason: str) -> bool:
    return bool(_INVALID_FACT_REASON_RE.search(reason or ""))


def _align_score_to_tier(score: int, tier: str) -> int:
    """Coerce score into the numeric band of the declared tier."""
    normalized_tier = _normalize_tier(tier)
    normalized_score = _normalize_score(score)
    if normalized_tier == "hot":
        return max(HOT_THRESHOLD, normalized_score)
    if normalized_tier == "cold":
        return min(COLD_THRESHOLD - 1, normalized_score)
    # warm band: [30, 69] — clamp rather than reset to preserve relative strength
    if normalized_score < COLD_THRESHOLD or normalized_score >= HOT_THRESHOLD:
        return max(COLD_THRESHOLD, min(HOT_THRESHOLD - 1, normalized_score))
    return normalized_score


def _normalize_text(value: Any, *, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.strip())[:max_chars]


def _format_call_messages_for_log(messages: list[Any]) -> str:
    sep = "\n" + "-" * 60 + "\n"
    parts: list[str] = []
    for msg in messages:
        role = getattr(msg, "type", "unknown")
        name = getattr(msg, "name", None)
        tool_call_id = getattr(msg, "tool_call_id", None)

        header_parts = [f"[{role}]"]
        if name:
            header_parts.append(f"name={name}")
        if tool_call_id:
            header_parts.append(f"tool_call_id={tool_call_id}")
        role_label = " ".join(header_parts)

        content = getattr(msg, "content", "")
        if isinstance(content, list):
            text_parts = [
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            ]
            content = "\n".join(filter(None, text_parts))

        body = str(content)
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            tool_calls_str = json.dumps(tool_calls, ensure_ascii=False, indent=2)
            body = (body + "\n" if body else "") + f"tool_calls: {tool_calls_str}"

        parts.append(f"{role_label}\n{body}")
    return sep.join(parts)


def _message_content_to_text(content: Any) -> str:
    if isinstance(content, list):
        text_parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                if isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
                else:
                    text_parts.append(
                        json.dumps(part, ensure_ascii=False, sort_keys=True)
                    )
            else:
                text_parts.append(str(part))
        return "\n".join(filter(None, text_parts))
    return str(content or "")


def _format_messages_for_memory_transcript(messages: list[Any]) -> str:
    sep = "\n" + "-" * 60 + "\n"
    parts: list[str] = []
    for index, msg in enumerate(messages, start=1):
        role = getattr(msg, "type", "unknown")
        name = getattr(msg, "name", None)
        tool_call_id = getattr(msg, "tool_call_id", None)
        header_parts = [f"[{index}]", f"role={role}"]
        if name:
            header_parts.append(f"name={name}")
        if tool_call_id:
            header_parts.append(f"tool_call_id={tool_call_id}")

        body = _message_content_to_text(getattr(msg, "content", ""))
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls:
            tool_calls_json = json.dumps(
                tool_calls, ensure_ascii=False, indent=2, sort_keys=True
            )
            body = (
                (body + "\n" if body else "")
                + "assistant_tool_calls_json:\n"
                + tool_calls_json
            )

        parts.append(" ".join(header_parts) + "\n" + body)

    return (
        "conversation_transcript:\n"
        "The following transcript is read-only context for memory extraction. "
        "Do not answer it, continue it, or emit tool calls.\n" + sep.join(parts)
    )


def _normalize_hash(section: str, content: str) -> str:
    return f"{section.strip().casefold()}::{content.strip().casefold()}"
