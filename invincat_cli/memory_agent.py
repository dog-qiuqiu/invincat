"""Dedicated memory agent middleware with structured memory stores.

Runs an independent model call with a focused system prompt after every
non-trivial conversation turn to extract durable memory operations. By
default the agent runs once per turn (no wall-clock or file cooldown)
so memory stays in sync with the latest signal; the turn-interval,
wall-clock, and file cooldown throttles can still be re-enabled via
the INVINCAT_MEMORY_MIN_TURN_INTERVAL / INVINCAT_MEMORY_MIN_SECONDS_BETWEEN_RUNS
/ INVINCAT_MEMORY_FILE_COOLDOWN_SECONDS environment variables.

The extraction runs in ``aafter_agent`` so it does not block the user from
receiving the main response.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import tempfile
import time
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
    PrivateStateAttr,
)
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.config import get_config

logger = logging.getLogger(__name__)

MAX_OPERATIONS_PER_RUN = 8
MAX_ITEM_CONTENT_CHARS = 500
MAX_SECTION_NAME_CHARS = 80
MAX_SCORE_REASON_CHARS = 160
_MAX_OUTPUT_TOKENS = 2000
_MAX_CONVERSATION_CHARS = 1500

DEFAULT_TIER = "warm"
DEFAULT_SCORE = 50
HOT_THRESHOLD = 70
COLD_THRESHOLD = 30
MAX_RESCORING_CANDIDATES_PER_SCOPE = 12
MAX_HOT_ITEMS_PER_SCOPE = 8
MAX_WARM_ITEMS_PER_SCOPE = 6
MAX_SNAPSHOT_ITEMS_PER_SCOPE = 80

_MEMORY_SIGNAL_RE = re.compile(
    r"\b("
    r"always|never|prefer|preference|style|convention|rule|guideline|"
    r"remember|remember this|best practice|pattern|decision|constraint"
    r")\b|"
    r"(记住|偏好|规范|约定|规则|风格|最佳实践|约束|决策)",
    re.IGNORECASE,
)

_EXPLICIT_MEMORY_REQUEST_RE = re.compile(
    r"\b("
    r"remember this|save this|save it|add to memory|store this|"
    r"please remember|record this|memorize"
    r")\b|"
    r"(请记住|记一下|存一下|写入记忆|保存到记忆|记到记忆|记住这条)",
    re.IGNORECASE,
)

_TRIVIAL_RE = re.compile(
    r"^\s*("
    r"ok|okay|thanks|thank you|got it|sure|yes|no|confirmed|done|"
    r"continue|go ahead|proceed|sounds good|great|perfect|nice|"
    r"好的|谢谢|明白|知道了|好|嗯|是的|对|继续|好的好的|没问题|可以|"
    r"收到|了解|行|嗯嗯|好的收到"
    r")\s*[.!?。！？]?\s*$",
    re.IGNORECASE,
)

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
    {"create", "update", "rescore", "retier", "archive", "noop"}
)

_SYSTEM_PROMPT = """\
You are a conservative memory curator for an AI assistant.

Your job: read a recent conversation and a memory snapshot, then emit a
small set of operations that keep the memory store minimal, durable, and
reusable across future turns. Prefer precision over recall. When
uncertain, emit noop.

====================================================================
INPUT
====================================================================
The user message contains two sections:
1) recent_conversation — latest turns between user and assistant.
2) memory_snapshot — JSON shaped as:
   {
     "user":    {"items": [...], "rescore_candidates": [...]},
     "project": {"items": [...], "rescore_candidates": [...]}
   }
   Each item has: id, section, content, status, tier, score,
   score_reason, last_scored_at.
   rescore_candidates is the subset of IDs eligible for rescore/retier
   this turn — do not target other IDs with those ops.

====================================================================
OUTPUT CONTRACT
====================================================================
Return STRICT JSON only. No prose, no markdown fences, no file paths,
no full markdown memory files.
The first non-whitespace character must be "{".
Output exactly one JSON object with a top-level "operations" array:

  {"operations": [<op>, <op>, ...]}

Allowed op shapes (fields marked "optional" may be omitted):

  create:
    {"op": "create", "scope": "user"|"project",
     "section": "<short category>", "content": "<durable fact>",
     "confidence": "low"|"medium"|"high",
     "tier": "hot"|"warm"|"cold", "score": <integer 0-100>,
     "score_reason": "<specific evidence>"}
    Omit the id field entirely — the store assigns it.

  update:
    {"op": "update", "scope": "...", "id": "mem_u_000001",
     "content": "..." (optional),
     "confidence": "..." (optional),
     "tier": "..." (optional),
     "score": <integer> (optional),
     "score_reason": "..." (optional)}
    At least one non-id field must be present.

  rescore:
    {"op": "rescore", "scope": "...", "id": "mem_u_000001",
     "score": <integer 0-100>, "score_reason": "..."}
    Only IDs in rescore_candidates are valid.

  retier:
    {"op": "retier", "scope": "...", "id": "mem_u_000001",
     "tier": "hot"|"warm"|"cold", "score_reason": "..."}
    Only IDs in rescore_candidates are valid.

  archive:
    {"op": "archive", "scope": "...", "id": "mem_p_000031",
     "reason": "<why no longer valid>"}

  noop:
    {"op": "noop"}

====================================================================
SCOPE ROUTING
====================================================================
- user: cross-project traits of the person — communication style,
  coding habits, preferred tools and workflows.
- project: repository-specific conventions, architecture, stack,
  constraints, domain rules.
- If ambiguous, prefer project or noop (never guess user scope).

====================================================================
WHAT TO STORE
====================================================================
Store facts that are:
- durable (still true next week),
- specific (actionable, not generic advice),
- reusable (would meaningfully shape future responses).

Do NOT store:
- temporary runtime states, one-off errors, ephemeral paths/tokens/secrets
- short-lived plans/todos, volatile metrics/status
- information already derivable from code or git history
- reasoning steps, intermediate conclusions, session narration

====================================================================
OPERATION DISCIPLINE
====================================================================
- Keep operations sparse and local to recent evidence.
- At most one operation per item id per run.
- When an existing item already matches, prefer update over create.
  Never produce a near-duplicate.
- update vs archive:
    * update  — the fact is still true but phrasing/score/tier needs
                refinement, or new evidence strengthens it
    * archive — the fact is no longer valid, has been contradicted,
                or its context no longer applies
- rescore/retier require clear new evidence this turn and must target
  only IDs listed in rescore_candidates.

====================================================================
FIELD GUIDANCE
====================================================================
Language rule: write section, content, and score_reason in the same
language as the conversation. If the user writes in Chinese, all
three fields must be in Chinese. If the user writes in English, use
English. Never translate the user's language into another language.

section (<= 80 chars) — a short reusable category in Title Case.
  Good (English): "Code Style", "Testing Conventions", "Architecture",
                  "Communication Preferences", "Deployment Workflow"
  Good (Chinese): "代码风格", "测试规范", "架构约定", "沟通偏好", "部署流程"
  Bad:  "general", "user info", "notes", "things user said"

content (<= 500 chars) — one self-contained durable fact. Declarative,
  no meta-language like "the user said" / "用户说".
  Good (English user):   "Prefers concise bullet-style responses over prose."
  Good (Chinese user):   "偏好简洁的要点式回复，而非大段散文。"
  Good (project, English): "All API handlers live under src/api/ and return
                            typed Response objects."
  Good (project, Chinese): "所有 API 处理器放在 src/api/ 下，必须返回带类型的 Response 对象。"
  Bad:  "User mentioned they like short answers." / "用户提到他喜欢简短回答。"

confidence — belief that the fact is true AND stable.
  high   — explicitly stated, or strongly repeated
  medium — inferred from consistent behavior in the conversation
  low    — single weak signal (usually prefer noop instead)

score (0-100 integer) — durability and cross-turn usefulness.
  Bands: hot >= 70, warm 30..69, cold < 30.
  Anchors:
    90 — explicit standing rule the user asked to remember
    75 — strong repeated preference; clearly load-bearing
    55 — observed habit; useful but not always relevant
    35 — niche convention; applies in specific contexts only
    20 — weak or fading signal; candidate for future archive
  Keep score consistent with tier (the system coerces mismatches
  into the declared tier's band, so inconsistency wastes evidence).

score_reason (<= 160 chars) — one short sentence citing specific
  evidence from the conversation, not a generic label. Write in the
  same language as the conversation.
  Good (English): "User explicitly asked to always prefer bullet lists over prose."
  Good (Chinese): "用户明确要求每次回复都用要点列表而非大段文字。"
  Bad:  "User preference." / "用户偏好。"

====================================================================
EXAMPLES
====================================================================
Example A — no durable signal (routine task request):
  conversation: user asks "can you fix the typo in README.md?"
  output: {"operations": [{"op": "noop"}]}

Example B — explicit standing rule:
  conversation: user says "from now on, always run `pytest -x` before
                suggesting commits."
  output: {"operations": [
    {"op": "create", "scope": "project", "section": "Testing Workflow",
     "content": "Always run `pytest -x` before suggesting any commit.",
     "confidence": "high", "tier": "hot", "score": 85,
     "score_reason": "User explicitly stated this as a standing rule."}
  ]}

Example C — existing item is contradicted:
  snapshot has mem_p_000007 "Uses Poetry for dependency management".
  conversation: user says "we migrated off Poetry, everything is on
                uv now."
  output: {"operations": [
    {"op": "archive", "scope": "project", "id": "mem_p_000007",
     "reason": "User stated the project migrated from Poetry to uv."},
    {"op": "create", "scope": "project", "section": "Tooling",
     "content": "Uses `uv` for dependency management.",
     "confidence": "high", "tier": "hot", "score": 80,
     "score_reason": "User confirmed migration from Poetry to uv."}
  ]}

Example D — refine an existing item:
  snapshot has mem_u_000003 "Prefers terse responses" (score 60).
  conversation: user says "yeah really, keep them to 2-3 bullets max."
  output: {"operations": [
    {"op": "update", "scope": "user", "id": "mem_u_000003",
     "content": "Prefers terse responses, 2-3 bullets maximum.",
     "confidence": "high", "tier": "hot", "score": 78,
     "score_reason": "User reinforced and quantified the preference."}
  ]}

Example E — Chinese conversation (language must match):
  conversation: 用户说"以后提交代码前必须先跑 `make lint`，不然不给合并。"
  output: {"operations": [
    {"op": "create", "scope": "project", "section": "提交规范",
     "content": "提交代码前必须先执行 `make lint`，否则不允许合并。",
     "confidence": "high", "tier": "hot", "score": 88,
     "score_reason": "用户明确要求将 lint 检查作为合并前的强制步骤。"}
  ]}
"""

_USER_TEMPLATE = """\
recent_conversation:
{conversation}

memory_snapshot:
{snapshot}
"""

_USER_POLICY_TEMPLATE = """\
turn_policy:
- explicit_memory_request: {explicit_memory_request}
- When true, the user has directly asked to record something; you may
  create with confidence "high" and score >= 70 if the evidence is
  explicit. Still avoid near-duplicates — prefer update when an
  existing item already matches.
- When false, be conservative: prefer update over create, and prefer
  noop when the signal is ambiguous or transient.
"""


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, float(raw))
    except ValueError:
        return default


def _is_trivial_turn(messages: list[Any]) -> bool:
    """Return True when the last user message carries no extractable information."""
    text = _last_human_text(messages)
    if not text:
        return True
    # Short user messages can still be memory-worthy (especially in Chinese).
    if _MEMORY_SIGNAL_RE.search(text):
        return False
    return bool(_TRIVIAL_RE.match(text))


def _last_human_text(messages: list[Any]) -> str:
    for msg in reversed(messages):
        if getattr(msg, "type", "") != "human":
            continue
        content = getattr(msg, "content", "")
        if isinstance(content, list):
            parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in content]
            return " ".join(filter(None, parts)).strip()
        return str(content).strip()
    return ""


def _is_explicit_memory_request(text: str) -> bool:
    return bool(_EXPLICIT_MEMORY_REQUEST_RE.search(text or ""))


def _is_task_complete(messages: list[Any]) -> bool:
    """Return True when all tool calls have completed and AI has given final response."""
    if not messages:
        return False

    last_msg = messages[-1]
    msg_type = getattr(last_msg, "type", "")

    if msg_type == "tool":
        return False

    if msg_type == "ai":
        tool_calls = getattr(last_msg, "tool_calls", None)
        if tool_calls:
            return False
        return True

    return False


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


def _normalize_score_reason(value: Any) -> str:
    return _normalize_text(value, max_chars=MAX_SCORE_REASON_CHARS)


def _align_score_to_tier(score: int, tier: str) -> int:
    """Coerce score into the numeric band of the declared tier."""
    normalized_tier = _normalize_tier(tier)
    normalized_score = _normalize_score(score)
    if normalized_tier == "hot":
        return max(HOT_THRESHOLD, normalized_score)
    if normalized_tier == "cold":
        return min(COLD_THRESHOLD - 1, normalized_score)
    # warm band: [30, 69]
    if normalized_score < COLD_THRESHOLD or normalized_score >= HOT_THRESHOLD:
        return DEFAULT_SCORE
    return normalized_score


def _normalize_text(value: Any, *, max_chars: int) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value.strip())[:max_chars]


def _normalize_hash(section: str, content: str) -> str:
    return f"{section.strip().casefold()}::{content.strip().casefold()}"


def _extract_terms(text: str) -> set[str]:
    if not text:
        return set()
    lowered = text.casefold()
    tokens = set(re.findall(r"[a-z0-9_]{2,}", lowered))
    tokens.update(re.findall(r"[\u4e00-\u9fff]{2,}", text))
    return tokens


def _item_relevance_score(item: dict[str, Any], terms: set[str]) -> int:
    if not terms:
        return 0
    corpus = " ".join(
        [
            str(item.get("section", "")).casefold(),
            str(item.get("content", "")).casefold(),
            str(item.get("score_reason", "")).casefold(),
        ]
    )
    return sum(1 for term in terms if term and term in corpus)


def _select_rescoring_candidates(
    store: dict[str, Any] | None,
    *,
    conversation: str,
    max_items: int = MAX_RESCORING_CANDIDATES_PER_SCOPE,
) -> list[dict[str, Any]]:
    if store is None:
        return []
    items = [
        item
        for item in store.get("items", [])
        if isinstance(item, dict) and item.get("status") == "active"
    ]
    if not items:
        return []

    terms = _extract_terms(conversation)
    ranked = sorted(
        items,
        key=lambda item: (
            1
            if _normalize_tier(
                item.get("tier"),
                default=_derive_tier_from_score(_normalize_score(item.get("score"))),
            )
            == "hot"
            else 0,
            _item_relevance_score(item, terms),
            str(item.get("updated_at", "")),
            str(item.get("id", "")).casefold(),
        ),
        reverse=True,
    )
    selected = ranked[: max(0, max_items)]
    return [
        {
            "id": item.get("id"),
            "section": item.get("section"),
            "content": item.get("content"),
            "tier": _normalize_tier(
                item.get("tier"),
                default=_derive_tier_from_score(_normalize_score(item.get("score"))),
            ),
            "score": _normalize_score(item.get("score")),
            "updated_at": item.get("updated_at"),
        }
        for item in selected
        if isinstance(item.get("id"), str)
    ]


def _read_memory_store(path: Path, scope: str) -> dict[str, Any]:
    """Read memory store from JSON file or return a new validated store."""
    def _read_error_store(target_scope: str) -> dict[str, Any]:
        store = _new_store(target_scope)
        store["__read_error__"] = True
        return store

    if not path.exists():
        return _new_store(scope)

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("Memory store unreadable at %s; marking as read-error", path)
        return _read_error_store(scope)

    if not isinstance(data, dict):
        logger.warning("Memory store schema invalid at %s; marking as read-error", path)
        return _read_error_store(scope)

    normalized_scope = _normalize_scope(data.get("scope")) or scope
    if isinstance(data.get("scope"), str) and _normalize_scope(data.get("scope")) is None:
        logger.warning("Memory store scope invalid at %s; marking as read-error", path)
        return _read_error_store(scope)
    store = {
        "version": 1,
        "scope": normalized_scope,
        "items": [],
    }

    items = data.get("items", [])
    if not isinstance(items, list):
        logger.warning("Memory store items invalid at %s; marking as read-error", path)
        return _read_error_store(normalized_scope)

    for raw in items:
        if not isinstance(raw, dict):
            continue
        item_scope = _normalize_scope(raw.get("scope")) or normalized_scope
        if item_scope != normalized_scope:
            continue
        item_id = raw.get("id")
        if not isinstance(item_id, str):
            continue
        if not _ITEM_ID_PATTERNS[normalized_scope].match(item_id):
            continue
        section = _normalize_text(raw.get("section", ""), max_chars=MAX_SECTION_NAME_CHARS)
        content = _normalize_text(raw.get("content", ""), max_chars=MAX_ITEM_CONTENT_CHARS)
        if not section or not content:
            continue
        status = _normalize_status(raw.get("status"))
        created_at = raw.get("created_at") if isinstance(raw.get("created_at"), str) else _iso_now()
        updated_at = raw.get("updated_at") if isinstance(raw.get("updated_at"), str) else created_at
        archived_at = raw.get("archived_at") if isinstance(raw.get("archived_at"), str) else None
        source_thread_id = (
            raw.get("source_thread_id")
            if isinstance(raw.get("source_thread_id"), str)
            else "__default_thread__"
        )
        source_anchor = raw.get("source_anchor") if isinstance(raw.get("source_anchor"), str) else ""
        confidence = _normalize_confidence(raw.get("confidence"), default="medium")
        score = _normalize_score(raw.get("score"), default=DEFAULT_SCORE)
        tier = _normalize_tier(raw.get("tier"), default=_derive_tier_from_score(score))
        score_reason = _normalize_score_reason(raw.get("score_reason"))
        last_scored_at = (
            raw.get("last_scored_at")
            if isinstance(raw.get("last_scored_at"), str)
            else (updated_at if isinstance(updated_at, str) and updated_at else created_at)
        )

        store["items"].append(
            {
                "id": item_id,
                "scope": normalized_scope,
                "section": section,
                "content": content,
                "status": status,
                "created_at": created_at,
                "updated_at": updated_at,
                "archived_at": archived_at if status == "archived" else None,
                "source_thread_id": source_thread_id,
                "source_anchor": source_anchor,
                "confidence": confidence,
                "tier": tier,
                "score": score,
                "score_reason": score_reason,
                "last_scored_at": last_scored_at,
                "norm_hash": _normalize_hash(section, content),
            }
        )

    return store


def _write_memory_store(path: Path, store: dict[str, Any]) -> None:
    # Internal guard flags are runtime-only and must not be persisted.
    write_store = {k: v for k, v in store.items() if not str(k).startswith("__")}
    payload = json.dumps(write_store, ensure_ascii=False, indent=2) + "\n"
    _atomic_write_text(path, payload)


def _next_memory_id(store: dict[str, Any], scope: str) -> str:
    pattern = _ITEM_ID_PATTERNS[scope]
    max_index = 0
    for item in store.get("items", []):
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if not isinstance(item_id, str):
            continue
        match = pattern.match(item_id)
        if not match:
            continue
        max_index = max(max_index, int(match.group(1)))
    return f"{_ITEM_ID_PREFIX[scope]}{max_index + 1:06d}"


def _build_memory_snapshot(
    user_store: dict[str, Any] | None,
    project_store: dict[str, Any] | None,
    *,
    conversation: str,
) -> dict[str, Any]:
    def _scope_snapshot(store: dict[str, Any] | None) -> dict[str, Any]:
        if store is None:
            return {"items": [], "rescore_candidates": []}
        items: list[dict[str, Any]] = []
        for item in store.get("items", []):
            if not isinstance(item, dict):
                continue
            score = _normalize_score(item.get("score"), default=DEFAULT_SCORE)
            tier = _normalize_tier(
                item.get("tier"),
                default=_derive_tier_from_score(score),
            )
            items.append(
                {
                    "id": item.get("id"),
                    "section": item.get("section"),
                    "content": item.get("content"),
                    "status": item.get("status"),
                    "tier": tier,
                    "score": score,
                    "score_reason": _normalize_score_reason(item.get("score_reason")),
                    "last_scored_at": item.get("last_scored_at"),
                }
            )
        items.sort(
            key=lambda x: (
                str(x.get("status", "")) != "active",
                {"hot": 0, "warm": 1, "cold": 2}.get(str(x.get("tier", "warm")), 1),
                -_normalize_score(x.get("score")),
                str(x.get("section", "")).casefold(),
                str(x.get("id", "")),
            )
        )
        if len(items) > MAX_SNAPSHOT_ITEMS_PER_SCOPE:
            items = items[:MAX_SNAPSHOT_ITEMS_PER_SCOPE]
        candidates = _select_rescoring_candidates(
            store,
            conversation=conversation,
            max_items=MAX_RESCORING_CANDIDATES_PER_SCOPE,
        )
        return {"items": items, "rescore_candidates": candidates}

    return {
        "user": _scope_snapshot(user_store),
        "project": _scope_snapshot(project_store),
    }


def _normalize_and_validate_operations(
    payload: Any,
    *,
    rescoring_candidate_ids_by_scope: dict[str, set[str]] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw_ops = payload.get("operations", [])
    if not isinstance(raw_ops, list):
        return []
    if len(raw_ops) > MAX_OPERATIONS_PER_RUN:
        raw_ops = raw_ops[:MAX_OPERATIONS_PER_RUN]

    normalized: list[dict[str, Any]] = []
    for raw in raw_ops:
        if not isinstance(raw, dict):
            continue
        op = raw.get("op")
        if not isinstance(op, str):
            continue
        op = op.strip().lower()
        if op not in _ALLOWED_OPS:
            continue
        if op == "noop":
            normalized.append({"op": "noop"})
            continue

        scope = _normalize_scope(raw.get("scope"))
        if scope is None:
            continue

        if op == "create":
            if raw.get("id") not in (None, ""):
                continue
            section = _normalize_text(raw.get("section"), max_chars=MAX_SECTION_NAME_CHARS)
            content = _normalize_text(raw.get("content"), max_chars=MAX_ITEM_CONTENT_CHARS)
            if not section or not content:
                continue
            confidence = _normalize_confidence(raw.get("confidence"), default="high")
            if raw.get("tier") is not None and (
                not isinstance(raw.get("tier"), str)
                or str(raw.get("tier")).strip().lower() not in _ALLOWED_TIER
            ):
                continue
            score = _normalize_score(raw.get("score"), default=DEFAULT_SCORE)
            tier = _normalize_tier(raw.get("tier"), default=_derive_tier_from_score(score))
            score_reason = _normalize_score_reason(raw.get("score_reason"))
            normalized.append(
                {
                    "op": "create",
                    "scope": scope,
                    "section": section,
                    "content": content,
                    "confidence": confidence,
                    "tier": tier,
                    "score": score,
                    "score_reason": score_reason,
                }
            )
            continue

        if op == "update":
            item_id = raw.get("id")
            if not isinstance(item_id, str) or not item_id.strip():
                continue
            content = _normalize_text(raw.get("content"), max_chars=MAX_ITEM_CONTENT_CHARS)
            has_content = bool(content)
            has_confidence = raw.get("confidence") is not None
            has_tier = raw.get("tier") is not None
            has_score = raw.get("score") is not None
            has_score_reason = raw.get("score_reason") is not None
            if not any((has_content, has_confidence, has_tier, has_score, has_score_reason)):
                continue
            if has_tier and (
                not isinstance(raw.get("tier"), str)
                or str(raw.get("tier")).strip().lower() not in _ALLOWED_TIER
            ):
                continue
            score = _normalize_score(raw.get("score"), default=DEFAULT_SCORE)
            tier = _normalize_tier(raw.get("tier"), default=_derive_tier_from_score(score))
            score_reason = _normalize_score_reason(raw.get("score_reason"))
            confidence = _normalize_confidence(raw.get("confidence"), default="high")
            op_payload: dict[str, Any] = {
                "op": "update",
                "scope": scope,
                "id": item_id.strip(),
            }
            if has_content:
                op_payload["content"] = content
            if has_confidence:
                op_payload["confidence"] = confidence
            if has_tier:
                op_payload["tier"] = tier
            if has_score:
                op_payload["score"] = score
            if has_score_reason:
                op_payload["score_reason"] = score_reason
            normalized.append(op_payload)
            continue

        if op == "rescore":
            item_id = raw.get("id")
            if not isinstance(item_id, str) or not item_id.strip():
                continue
            if raw.get("score") is None:
                continue
            scope_candidates = (
                (rescoring_candidate_ids_by_scope or {}).get(scope)
                if rescoring_candidate_ids_by_scope is not None
                else None
            )
            if scope_candidates is not None and item_id.strip() not in scope_candidates:
                continue
            score = _normalize_score(raw.get("score"), default=DEFAULT_SCORE)
            normalized.append(
                {
                    "op": "rescore",
                    "scope": scope,
                    "id": item_id.strip(),
                    "score": score,
                    "score_reason": _normalize_score_reason(raw.get("score_reason")),
                }
            )
            continue

        if op == "retier":
            item_id = raw.get("id")
            if not isinstance(item_id, str) or not item_id.strip():
                continue
            if raw.get("tier") is None:
                continue
            tier_raw = raw.get("tier")
            if not isinstance(tier_raw, str) or tier_raw.strip().lower() not in _ALLOWED_TIER:
                continue
            scope_candidates = (
                (rescoring_candidate_ids_by_scope or {}).get(scope)
                if rescoring_candidate_ids_by_scope is not None
                else None
            )
            if scope_candidates is not None and item_id.strip() not in scope_candidates:
                continue
            normalized.append(
                {
                    "op": "retier",
                    "scope": scope,
                    "id": item_id.strip(),
                    "tier": _normalize_tier(tier_raw),
                    "score_reason": _normalize_score_reason(raw.get("score_reason")),
                }
            )
            continue

        if op == "archive":
            item_id = raw.get("id")
            if not isinstance(item_id, str) or not item_id.strip():
                continue
            reason = _normalize_text(raw.get("reason"), max_chars=120)
            normalized.append(
                {
                    "op": "archive",
                    "scope": scope,
                    "id": item_id.strip(),
                    "reason": reason or None,
                }
            )

    return normalized


def _find_item(store: dict[str, Any] | None, item_id: str) -> dict[str, Any] | None:
    if store is None:
        return None
    for item in store.get("items", []):
        if isinstance(item, dict) and item.get("id") == item_id:
            return item
    return None


def _active_items(store: dict[str, Any] | None) -> list[dict[str, Any]]:
    if store is None:
        return []
    return [
        item
        for item in store.get("items", [])
        if isinstance(item, dict) and item.get("status") == "active"
    ]


def _apply_operations(
    user_store: dict[str, Any] | None,
    project_store: dict[str, Any] | None,
    operations: list[dict[str, Any]],
    *,
    thread_id: str,
    source_anchor: str,
    now_iso: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    user_store = deepcopy(user_store) if user_store is not None else None
    project_store = deepcopy(project_store) if project_store is not None else None
    changed_scopes: set[str] = set()

    # Conflict guard: same id touched more than once in one batch.
    id_touch_count: dict[str, int] = {}
    for op in operations:
        item_id = op.get("id")
        if isinstance(item_id, str) and item_id:
            id_touch_count[item_id] = id_touch_count.get(item_id, 0) + 1
    conflicted_ids = {item_id for item_id, count in id_touch_count.items() if count > 1}

    # Archive ratio guard (per-scope).
    archive_target_count: dict[str, int] = {"user": 0, "project": 0}
    for op in operations:
        if op.get("op") != "archive":
            continue
        scope = op.get("scope")
        item_id = op.get("id")
        if not isinstance(scope, str) or not isinstance(item_id, str):
            continue
        if item_id in conflicted_ids:
            continue
        store = user_store if scope == "user" else project_store
        item = _find_item(store, item_id)
        if item is not None and item.get("status") == "active":
            archive_target_count[scope] += 1

    archive_blocked_scope: set[str] = set()
    for scope in ("user", "project"):
        store = user_store if scope == "user" else project_store
        active_total = len(_active_items(store))
        if active_total <= 0:
            continue
        allowed = max(1, math.floor(active_total * 0.2))
        if archive_target_count[scope] > allowed:
            archive_blocked_scope.add(scope)

    def _get_or_create_store(scope: str) -> dict[str, Any]:
        nonlocal user_store, project_store
        if scope == "user":
            if user_store is None:
                user_store = _new_store("user")
            return user_store
        if project_store is None:
            project_store = _new_store("project")
        return project_store

    for op in operations:
        op_name = op.get("op")
        if op_name == "noop":
            continue
        scope = op.get("scope")
        if not isinstance(scope, str) or scope not in _ALLOWED_SCOPE:
            continue
        if op_name == "archive" and scope in archive_blocked_scope:
            continue

        store = _get_or_create_store(scope)
        if op_name == "create":
            section = str(op["section"])
            content = str(op["content"])
            duplicate = any(
                item.get("status") == "active" and item.get("content", "").strip() == content
                for item in store.get("items", [])
                if isinstance(item, dict)
            )
            if duplicate:
                continue
            item_id = _next_memory_id(store, scope)
            score = _normalize_score(op.get("score"), default=DEFAULT_SCORE)
            tier = _normalize_tier(op.get("tier"), default=_derive_tier_from_score(score))
            score = _align_score_to_tier(score, tier)
            store["items"].append(
                {
                    "id": item_id,
                    "scope": scope,
                    "section": section,
                    "content": content,
                    "status": "active",
                    "created_at": now_iso,
                    "updated_at": now_iso,
                    "archived_at": None,
                    "source_thread_id": thread_id,
                    "source_anchor": source_anchor,
                    "confidence": _normalize_confidence(op.get("confidence"), default="high"),
                    "tier": tier,
                    "score": score,
                    "score_reason": _normalize_score_reason(op.get("score_reason")),
                    "last_scored_at": now_iso,
                    "norm_hash": _normalize_hash(section, content),
                }
            )
            changed_scopes.add(scope)
            continue

        item_id = op.get("id")
        if not isinstance(item_id, str) or item_id in conflicted_ids:
            continue
        item = _find_item(store, item_id)
        if item is None:
            continue
        if op_name == "update":
            if "content" in op:
                content = str(op.get("content", "")).strip()
                if not content:
                    continue
                item["content"] = content
                item["norm_hash"] = _normalize_hash(str(item.get("section", "")), content)
            item["updated_at"] = now_iso
            item["source_thread_id"] = thread_id
            item["source_anchor"] = source_anchor
            if "confidence" in op:
                item["confidence"] = _normalize_confidence(
                    op.get("confidence"),
                    default=str(item.get("confidence", "medium")),
                )
            has_score = "score" in op
            has_tier = "tier" in op
            if has_score:
                score = _normalize_score(op.get("score"), default=_normalize_score(item.get("score")))
                item["score"] = score
                item["tier"] = _derive_tier_from_score(score)
            if has_tier:
                default_tier = _normalize_tier(
                    item.get("tier"),
                    default=_derive_tier_from_score(_normalize_score(item.get("score"))),
                )
                item["tier"] = _normalize_tier(op.get("tier"), default=default_tier)
                item["score"] = _align_score_to_tier(
                    _normalize_score(item.get("score")),
                    str(item["tier"]),
                )
            if "score_reason" in op:
                item["score_reason"] = _normalize_score_reason(op.get("score_reason"))
            item["last_scored_at"] = now_iso
            if item.get("status") == "archived":
                item["status"] = "active"
                item["archived_at"] = None
            changed_scopes.add(scope)
        elif op_name == "rescore":
            score = _normalize_score(op.get("score"), default=_normalize_score(item.get("score")))
            item["score"] = score
            item["tier"] = _derive_tier_from_score(score)
            item["score_reason"] = _normalize_score_reason(op.get("score_reason"))
            item["last_scored_at"] = now_iso
            changed_scopes.add(scope)
        elif op_name == "retier":
            default_tier = _normalize_tier(
                item.get("tier"),
                default=_derive_tier_from_score(_normalize_score(item.get("score"))),
            )
            item["tier"] = _normalize_tier(op.get("tier"), default=default_tier)
            item["score"] = _align_score_to_tier(
                _normalize_score(item.get("score")),
                str(item["tier"]),
            )
            item["score_reason"] = _normalize_score_reason(op.get("score_reason"))
            item["last_scored_at"] = now_iso
            changed_scopes.add(scope)
        elif op_name == "archive":
            if item.get("status") == "archived":
                continue
            item["status"] = "archived"
            item["updated_at"] = now_iso
            item["archived_at"] = now_iso
            item["source_thread_id"] = thread_id
            item["source_anchor"] = source_anchor
            changed_scopes.add(scope)

    return user_store, project_store, sorted(changed_scopes)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        prefix=f".{path.name}.",
        suffix=".tmp",
    ) as tmp:
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_name = tmp.name
    os.replace(tmp_name, path)


def _backup_corrupt_store(path: Path) -> Path | None:
    """Best-effort backup for unreadable store files before auto-recovery."""
    if not path.exists():
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup_path = path.with_name(f"{path.name}.corrupt.{stamp}.bak")
    try:
        raw = path.read_bytes()
        # Preserve recoverability even when the original store has invalid UTF-8.
        _atomic_write_text(backup_path, raw.decode("utf-8", errors="replace"))
        return backup_path
    except (OSError, UnicodeDecodeError):
        logger.warning("Memory agent: failed to back up unreadable store %s", path, exc_info=True)
        return None


class MemoryAgentState(AgentState):
    """Private state fields for MemoryAgentMiddleware."""

    _auto_memory_updated_paths: Annotated[NotRequired[list[str]], PrivateStateAttr]


class MemoryAgentMiddleware(AgentMiddleware):
    """Dedicated memory agent that runs after every non-trivial conversation turn."""

    state_schema = MemoryAgentState

    def __init__(
        self,
        *,
        memory_paths: list[str],
        memory_store_paths: dict[str, str] | None = None,
        context_messages: int | None = None,
        min_turn_interval: int | None = None,
        min_seconds_between_runs: float | None = None,
        file_cooldown_seconds: float | None = None,
    ) -> None:
        if context_messages is None:
            context_messages = _env_int(
                "INVINCAT_MEMORY_CONTEXT_MESSAGES",
                default=0,
                minimum=0,
            )
        if min_turn_interval is None:
            min_turn_interval = _env_int(
                "INVINCAT_MEMORY_MIN_TURN_INTERVAL",
                default=1,
                minimum=1,
            )
        if min_seconds_between_runs is None:
            min_seconds_between_runs = _env_float(
                "INVINCAT_MEMORY_MIN_SECONDS_BETWEEN_RUNS",
                default=0.0,
                minimum=0.0,
            )
        if file_cooldown_seconds is None:
            file_cooldown_seconds = _env_float(
                "INVINCAT_MEMORY_FILE_COOLDOWN_SECONDS",
                default=0.0,
                minimum=0.0,
            )

        resolved_store_paths = (
            {
                scope: str(Path(p).expanduser().resolve())
                for scope, p in memory_store_paths.items()
                if scope in _ALLOWED_SCOPE and isinstance(p, str) and p.strip()
            }
            if memory_store_paths
            else {}
        )
        self._memory_store_paths: dict[str, str] = resolved_store_paths
        self._context_messages = max(0, context_messages)
        self._min_turn_interval = max(1, min_turn_interval)
        self._min_seconds_between_runs = max(0.0, min_seconds_between_runs)
        self._file_cooldown_seconds = max(0.0, file_cooldown_seconds)

        self._allowed_paths: frozenset[str] = frozenset(self._memory_store_paths.values())

        self._captured_model: Any = None
        self._turn_index = 0
        self._last_run_turn = 0
        self._last_run_at = 0.0
        self._cursor_by_thread: dict[str, int] = {}
        self._anchor_by_thread: dict[str, str] = {}

    def _is_authorized_path(self, path: Path) -> bool:
        return str(path.expanduser().resolve()) in self._allowed_paths

    def _memory_files_recently_updated(self) -> bool:
        if self._file_cooldown_seconds <= 0:
            return False
        now = time.time()
        for path in self._memory_store_paths.values():
            p = Path(path).expanduser()
            try:
                if p.exists():
                    age = now - p.stat().st_mtime
                    if age < self._file_cooldown_seconds:
                        return True
            except OSError:
                continue
        return False

    @staticmethod
    def _last_human_text(messages: list[Any]) -> str:
        return _last_human_text(messages)

    @staticmethod
    def _message_anchor(message: Any) -> str:
        msg_type = getattr(message, "type", "")
        content = getattr(message, "content", "")
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            )
        text = str(content)
        tool_calls = getattr(message, "tool_calls", None)
        return f"{msg_type}|{len(text)}|{text[:160]}|{bool(tool_calls)}"

    @staticmethod
    def _resolve_thread_id() -> str:
        try:
            cfg = get_config()
            configurable = cfg.get("configurable", {})
            thread_id = configurable.get("thread_id")
            if isinstance(thread_id, str) and thread_id.strip():
                return thread_id
        except Exception:
            logger.debug("Memory agent: failed to resolve thread_id", exc_info=True)
        return "__default_thread__"

    def _slice_incremental_messages(self, thread_id: str, messages: list[Any]) -> list[Any]:
        if not messages:
            return []

        cursor = self._cursor_by_thread.get(thread_id, 0)
        if cursor <= 0:
            return list(messages)
        if cursor > len(messages):
            logger.debug(
                "Memory agent: cursor reset for %s (cursor=%d > len=%d)",
                thread_id,
                cursor,
                len(messages),
            )
            return list(messages)

        anchor = self._anchor_by_thread.get(thread_id)
        if anchor and cursor - 1 >= 0:
            current_anchor = self._message_anchor(messages[cursor - 1])
            if current_anchor != anchor:
                logger.debug(
                    "Memory agent: cursor reset for %s (history changed before cursor)",
                    thread_id,
                )
                return list(messages)
        return list(messages[cursor:])

    def _advance_cursor(self, thread_id: str, messages: list[Any]) -> None:
        self._cursor_by_thread[thread_id] = len(messages)
        if messages:
            self._anchor_by_thread[thread_id] = self._message_anchor(messages[-1])
        else:
            self._anchor_by_thread.pop(thread_id, None)

    def _should_run_for_turn(self, messages: list[Any]) -> bool:
        self._turn_index += 1
        turns_since_last = self._turn_index - self._last_run_turn
        interval_due = turns_since_last >= self._min_turn_interval
        human_text = self._last_human_text(messages)
        signal_match = bool(_MEMORY_SIGNAL_RE.search(human_text))

        if self._min_seconds_between_runs > 0:
            elapsed = time.monotonic() - self._last_run_at
            if elapsed < self._min_seconds_between_runs and not signal_match:
                logger.debug(
                    "Memory agent: throttled by wall-clock cooldown (%.2fs < %.2fs)",
                    elapsed,
                    self._min_seconds_between_runs,
                )
                return False

        if self._memory_files_recently_updated() and not signal_match:
            logger.debug("Memory agent: throttled by file-update cooldown")
            return False

        if interval_due or signal_match:
            return True

        logger.debug(
            "Memory agent: throttled by turn interval (%d < %d)",
            turns_since_last,
            self._min_turn_interval,
        )
        return False

    @staticmethod
    def _format_messages(messages: list[Any]) -> str:
        lines: list[str] = []
        for msg in messages:
            role = getattr(msg, "type", "unknown")
            content = getattr(msg, "content", "")
            if isinstance(content, list):
                text_parts = [
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                    if not (
                        isinstance(p, dict)
                        and p.get("type") in ("tool_use", "tool_result")
                    )
                ]
                content = " ".join(filter(None, text_parts))
            if content:
                lines.append(f"{role}: {str(content)[:_MAX_CONVERSATION_CHARS]}")
        return "\n".join(lines)

    def _load_or_recover_store(
        self, scope: str, thread_id: str, source_anchor: str
    ) -> dict[str, Any] | None:
        del thread_id, source_anchor
        store_path_raw = self._memory_store_paths.get(scope)
        if not store_path_raw:
            return None
        store_path = Path(store_path_raw).expanduser().resolve()
        if store_path.exists():
            store = _read_memory_store(store_path, scope)
            if not store.get("__read_error__"):
                return store
            logger.warning("Memory agent: attempting auto-recovery for unreadable %s store", scope)
            backup = _backup_corrupt_store(store_path)
            if backup is not None:
                logger.warning(
                    "Memory agent: backed up unreadable store to %s before recovery",
                    backup,
                )
        store = _new_store(scope)
        if self._is_authorized_path(store_path):
            _write_memory_store(store_path, store)
        return store

    async def _extract_and_write(
        self,
        model: Any,
        messages: list[Any],
        *,
        thread_id: str,
        source_anchor: str,
    ) -> list[str] | None:
        written_store_paths: list[str] = []
        try:
            conversation = self._format_messages(messages)
            last_human = self._last_human_text(messages)
            explicit_memory_request = _is_explicit_memory_request(last_human)
            user_store = await asyncio.to_thread(
                self._load_or_recover_store, "user", thread_id, source_anchor
            )
            project_store = await asyncio.to_thread(
                self._load_or_recover_store, "project", thread_id, source_anchor
            )
            unreadable_scopes: list[str] = []
            if isinstance(user_store, dict) and user_store.get("__read_error__"):
                unreadable_scopes.append("user")
            if isinstance(project_store, dict) and project_store.get("__read_error__"):
                unreadable_scopes.append("project")
            if unreadable_scopes:
                logger.warning(
                    "Memory agent: skip write because store is unreadable (scopes=%s)",
                    ",".join(unreadable_scopes),
                )
                return []
            user_before = deepcopy(user_store)
            project_before = deepcopy(project_store)
            snapshot = _build_memory_snapshot(
                user_store,
                project_store,
                conversation=conversation,
            )

            response = await model.bind(max_tokens=_MAX_OUTPUT_TOKENS).ainvoke(
                [
                    SystemMessage(content=_SYSTEM_PROMPT),
                    HumanMessage(
                        content=_USER_TEMPLATE.format(
                            conversation=conversation,
                            snapshot=json.dumps(snapshot, ensure_ascii=False, indent=2),
                        )
                        + "\n"
                        + _USER_POLICY_TEMPLATE.format(
                            explicit_memory_request=str(explicit_memory_request).lower(),
                        )
                    ),
                ],
                config={"metadata": {"lc_source": "memory_agent"}},
            )

            raw: str = response.content
            if isinstance(raw, list):
                raw = " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in raw
                )
            raw = raw.lstrip()
            if not raw.startswith("{"):
                logger.debug("Memory agent: response does not start with JSON object")
                return []
            start = raw.find("{")
            if start == -1:
                logger.debug("Memory agent: no JSON found in response")
                return []
            data, _ = json.JSONDecoder().raw_decode(raw, start)
            rescoring_candidate_ids_by_scope = {
                scope: {
                    str(candidate.get("id"))
                    for candidate in (
                        ((snapshot.get(scope) or {}).get("rescore_candidates") or [])
                        if isinstance(snapshot.get(scope), dict)
                        else []
                    )
                    if isinstance(candidate, dict) and isinstance(candidate.get("id"), str)
                }
                for scope in ("user", "project")
            }
            operations = _normalize_and_validate_operations(
                data,
                rescoring_candidate_ids_by_scope=rescoring_candidate_ids_by_scope,
            )
            if not operations:
                return []

            now_iso = _iso_now()
            new_user, new_project, changed_scopes = _apply_operations(
                user_store,
                project_store,
                operations,
                thread_id=thread_id,
                source_anchor=source_anchor,
                now_iso=now_iso,
            )
            if not changed_scopes:
                return []

            for scope in changed_scopes:
                store = new_user if scope == "user" else new_project
                before_store = user_before if scope == "user" else project_before
                if store is None:
                    continue

                store_path_raw = self._memory_store_paths.get(scope)
                if not store_path_raw:
                    continue
                store_path = Path(store_path_raw).expanduser().resolve()
                if not self._is_authorized_path(store_path):
                    logger.warning("Memory agent: rejected unauthorized write for %s scope", scope)
                    continue

                before_items = (
                    before_store.get("items", [])
                    if isinstance(before_store, dict)
                    else []
                )
                if before_items and not _active_items(store):
                    logger.warning(
                        "Memory agent: refusing full-active wipe for non-empty %s store",
                        scope,
                    )
                    continue

                await asyncio.to_thread(_write_memory_store, store_path, store)
                written_store_paths.append(str(store_path))

            if written_store_paths:
                self._last_run_turn = self._turn_index
                self._last_run_at = time.monotonic()
            return written_store_paths

        except json.JSONDecodeError:
            logger.debug("Memory agent: model returned malformed JSON", exc_info=True)
            return []
        except Exception:
            logger.warning("Memory agent extraction failed unexpectedly", exc_info=True)
            return None

    @staticmethod
    def _emit_memory_status(runtime: Any, status: str) -> None:
        try:
            writer = getattr(runtime, "stream_writer", None)
            if callable(writer):
                writer({"event": "memory_agent", "status": status})
        except Exception:
            logger.debug("Memory agent: failed to emit status=%s", status, exc_info=True)

    def wrap_model_call(
        self, request: ModelRequest, handler: Any
    ) -> ModelResponse:
        self._captured_model = request.model
        return handler(request)

    async def awrap_model_call(
        self, request: ModelRequest, handler: Any
    ) -> ModelResponse:
        self._captured_model = request.model
        return await handler(request)

    async def aafter_agent(
        self, state: Any, runtime: Any
    ) -> dict[str, Any] | None:
        try:
            logger.debug("Memory agent: aafter_agent called")
            model = self._captured_model
            if model is None:
                return None
            if state.get("__interrupt__"):
                logger.debug("Memory agent: skipping extraction — pending interrupts")
                return None

            messages = state.get("messages", [])
            if not messages:
                return None
            if not _is_task_complete(messages):
                logger.debug("Memory agent: skipping extraction — task not complete")
                return None
            if _is_trivial_turn(messages):
                logger.debug("Memory agent: skipping trivial turn")
                return None
            if not self._should_run_for_turn(messages):
                return None

            thread_id = self._resolve_thread_id()
            incremental = self._slice_incremental_messages(thread_id, messages)
            if not incremental:
                return None

            if self._context_messages <= 0:
                recent = incremental
            else:
                recent = incremental[-self._context_messages :]

            if self._context_messages > 0:
                human_indices = [
                    i for i, m in enumerate(messages) if getattr(m, "type", "") == "human"
                ]
                if human_indices:
                    last_human_idx = human_indices[-1]
                    window_start = len(messages) - self._context_messages
                    if last_human_idx < window_start:
                        recent = [messages[last_human_idx]] + list(recent)

            source_anchor = self._message_anchor(recent[-1]) if recent else ""
            self._emit_memory_status(runtime, "running")
            try:
                written = await self._safe_extract_and_write(
                    model,
                    recent,
                    thread_id=thread_id,
                    source_anchor=source_anchor,
                )
            finally:
                self._emit_memory_status(runtime, "done")
            if written is None:
                logger.debug("Memory agent: extraction failed, cursor is not advanced")
                return None
            self._advance_cursor(thread_id, messages)
            if written:
                return {
                    "memory_contents": None,
                    "_auto_memory_updated_paths": written,
                }
            return None
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Memory agent: aafter_agent failed unexpectedly")
            return None

    async def _safe_extract_and_write(
        self,
        model: Any,
        messages: list[Any],
        *,
        thread_id: str,
        source_anchor: str,
    ) -> list[str] | None:
        try:
            return await self._extract_and_write(
                model,
                messages,
                thread_id=thread_id,
                source_anchor=source_anchor,
            )
        except asyncio.CancelledError:
            logger.debug(
                "Memory agent: extraction cancelled — re-scheduling task cancellation"
            )
            current = asyncio.current_task()
            if current is not None:
                current.cancel()
            return None
