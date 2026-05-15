"""Validation for structured memory operation payloads."""

from __future__ import annotations

from typing import Any

from invincat_cli.memory.store_core import (
    _ALLOWED_OPS,
    _ALLOWED_TIER,
    COLD_THRESHOLD,
    DEFAULT_SCORE,
    MAX_ITEM_CONTENT_CHARS,
    MAX_REASON_CHARS,
    MAX_SECTION_NAME_CHARS,
    _derive_tier_from_score,
    _normalize_confidence,
    _normalize_reason,
    _normalize_scope,
    _normalize_score,
    _normalize_text,
    _normalize_tier,
    _raw_reason,
    _reason_implies_invalid_fact,
)


def _normalize_and_validate_operations(
    payload: Any,
) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    raw_ops = payload.get("operations", [])
    if not isinstance(raw_ops, list):
        return []

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
            section = _normalize_text(
                raw.get("section"), max_chars=MAX_SECTION_NAME_CHARS
            )
            content = _normalize_text(
                raw.get("content"), max_chars=MAX_ITEM_CONTENT_CHARS
            )
            if not section or not content:
                continue
            confidence = _normalize_confidence(raw.get("confidence"), default="high")
            if raw.get("tier") is not None and (
                not isinstance(raw.get("tier"), str)
                or str(raw.get("tier")).strip().lower() not in _ALLOWED_TIER
            ):
                continue
            score = _normalize_score(raw.get("score"), default=DEFAULT_SCORE)
            tier = _normalize_tier(
                raw.get("tier"), default=_derive_tier_from_score(score)
            )
            reason = _normalize_reason(_raw_reason(raw))
            normalized.append(
                {
                    "op": "create",
                    "scope": scope,
                    "section": section,
                    "content": content,
                    "confidence": confidence,
                    "tier": tier,
                    "score": score,
                    "reason": reason,
                }
            )
            continue

        if op == "update":
            item_id = raw.get("id")
            if not isinstance(item_id, str) or not item_id.strip():
                continue
            content = _normalize_text(
                raw.get("content"), max_chars=MAX_ITEM_CONTENT_CHARS
            )
            has_content = bool(content)
            has_confidence = raw.get("confidence") is not None
            has_tier = raw.get("tier") is not None
            has_score = raw.get("score") is not None
            has_reason = (
                raw.get("reason") is not None or raw.get("score_reason") is not None
            )
            if not any((has_content, has_confidence, has_tier, has_score, has_reason)):
                continue
            if has_tier and (
                not isinstance(raw.get("tier"), str)
                or str(raw.get("tier")).strip().lower() not in _ALLOWED_TIER
            ):
                continue
            score = _normalize_score(raw.get("score"), default=DEFAULT_SCORE)
            tier = _normalize_tier(
                raw.get("tier"), default=_derive_tier_from_score(score)
            )
            reason = _normalize_reason(_raw_reason(raw))
            confidence = _normalize_confidence(raw.get("confidence"), default="high")
            if (
                not has_content
                and _reason_implies_invalid_fact(reason)
                and (
                    (has_score and score < COLD_THRESHOLD)
                    or (has_tier and tier == "cold")
                )
            ):
                normalized.append(
                    {
                        "op": "delete",
                        "scope": scope,
                        "id": item_id.strip(),
                        "reason": reason or "Existing memory is no longer valid.",
                    }
                )
                continue
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
            if has_reason:
                op_payload["reason"] = reason
            normalized.append(op_payload)
            continue

        if op == "rescore":
            item_id = raw.get("id")
            if not isinstance(item_id, str) or not item_id.strip():
                continue
            if raw.get("score") is None:
                continue
            score = _normalize_score(raw.get("score"), default=DEFAULT_SCORE)
            reason = _normalize_reason(_raw_reason(raw))
            if score < COLD_THRESHOLD and _reason_implies_invalid_fact(reason):
                normalized.append(
                    {
                        "op": "delete",
                        "scope": scope,
                        "id": item_id.strip(),
                        "reason": reason or "Existing memory is no longer valid.",
                    }
                )
                continue
            normalized.append(
                {
                    "op": "rescore",
                    "scope": scope,
                    "id": item_id.strip(),
                    "score": score,
                    "reason": reason,
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
            if (
                not isinstance(tier_raw, str)
                or tier_raw.strip().lower() not in _ALLOWED_TIER
            ):
                continue
            reason = _normalize_reason(_raw_reason(raw))
            tier = _normalize_tier(tier_raw)
            if tier == "cold" and _reason_implies_invalid_fact(reason):
                normalized.append(
                    {
                        "op": "delete",
                        "scope": scope,
                        "id": item_id.strip(),
                        "reason": reason or "Existing memory is no longer valid.",
                    }
                )
                continue
            normalized.append(
                {
                    "op": "retier",
                    "scope": scope,
                    "id": item_id.strip(),
                    "tier": tier,
                    "reason": reason,
                }
            )
            continue

        if op in {"archive", "delete"}:
            item_id = raw.get("id")
            if not isinstance(item_id, str) or not item_id.strip():
                continue
            reason = _normalize_text(_raw_reason(raw), max_chars=MAX_REASON_CHARS)
            normalized.append(
                {
                    "op": op,
                    "scope": scope,
                    "id": item_id.strip(),
                    "reason": reason or None,
                }
            )

    return normalized
