"""Signal detection helpers for the structured memory agent."""

from __future__ import annotations

import os
import re
from typing import Any

MEMORY_SIGNAL_RE = re.compile(
    r"\b("
    r"always|never|prefer|preference|style|convention|rule|guideline|"
    r"remember|remember this|best practice|pattern|decision|constraint|"
    r"architecture|workflow|tooling|framework|stack|pipeline|structure|"
    r"we use|we always|our convention|by convention|standard|policy"
    r")\b|"
    r"(记住|偏好|规范|约定|规则|风格|最佳实践|约束|决策|"
    r"架构|工作流|工具链|框架|技术栈|我们用|统一用|约定好的|标准做法)",
    re.IGNORECASE,
)

EXPLICIT_MEMORY_REQUEST_RE = re.compile(
    r"\b("
    r"remember this|save this|save it|add to memory|store this|"
    r"please remember|record this|memorize"
    r")\b|"
    r"(请记住|记一下|存一下|写入记忆|保存到记忆|记到记忆|记住这条)",
    re.IGNORECASE,
)

TRIVIAL_TURN_RE = re.compile(
    r"^\s*("
    r"ok|okay|thanks|thank you|got it|sure|yes|no|confirmed|done|"
    r"continue|go ahead|proceed|sounds good|great|perfect|nice|"
    r"好的|谢谢|明白|知道了|好|嗯|是的|对|继续|好的好的|没问题|可以|"
    r"收到|了解|行|嗯嗯|好的收到"
    r")\s*[.!?。！？]?\s*$",
    re.IGNORECASE,
)


def env_int(name: str, default: int, minimum: int = 0) -> int:
    """Read an integer environment value with a minimum and fallback."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def env_float(name: str, default: float, minimum: float = 0.0) -> float:
    """Read a float environment value with a minimum and fallback."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def last_human_text(messages: list[Any]) -> str:
    """Return the text content of the latest human message."""
    for msg in reversed(messages):
        if getattr(msg, "type", "") != "human":
            continue
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            parts = [
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            ]
            return " ".join(filter(None, parts)).strip()
        return str(content).strip()
    return ""


def is_trivial_turn(messages: list[Any]) -> bool:
    """Return True when the last user message carries no extractable information."""
    text = last_human_text(messages)
    if not text:
        return True
    if MEMORY_SIGNAL_RE.search(text):
        return False
    return bool(TRIVIAL_TURN_RE.match(text))


def is_explicit_memory_request(text: str) -> bool:
    """Return whether the user directly asked to store memory."""
    return bool(EXPLICIT_MEMORY_REQUEST_RE.search(text or ""))


def detect_target_language(text: str) -> str:
    """Return a coarse language label for memory-field generation."""
    if not text:
        return "the language of the last human message"
    cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin_words = len(re.findall(r"[A-Za-z]{2,}", text))
    if cjk_chars >= 2 and cjk_chars >= latin_words:
        return "Chinese"
    if latin_words > 0:
        return "English"
    return "the language of the last human message"


def is_task_complete(messages: list[Any]) -> bool:
    """Return True when all tool calls have completed and AI has final response."""
    if not messages:
        return False

    last_msg = messages[-1]
    msg_type = getattr(last_msg, "type", "")
    if msg_type == "tool":
        return False
    if msg_type == "ai":
        return not bool(getattr(last_msg, "tool_calls", None))
    return False
