"""Data models for `/goal` mode."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Literal

GoalStatus = Literal["active", "complete", "cancelled"]
GoalCommandKind = Literal["create", "status", "complete", "cancel", "clear", "error"]


def utc_now_iso() -> str:
    """Return an ISO timestamp suitable for durable goal state."""
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class GoalState:
    """Thread-scoped active goal state."""

    objective: str
    status: GoalStatus
    thread_id: str
    created_at: str
    updated_at: str
    token_budget: int | None = None
    tokens_used: int = 0
    completed_at: str | None = None
    summary: str | None = None

    @classmethod
    def create(
        cls,
        *,
        objective: str,
        thread_id: str,
        token_budget: int | None = None,
    ) -> GoalState:
        now = utc_now_iso()
        return cls(
            objective=objective.strip(),
            status="active",
            thread_id=thread_id,
            created_at=now,
            updated_at=now,
            token_budget=token_budget,
        )

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> GoalState:
        return cls(
            objective=str(raw["objective"]).strip(),
            status=_coerce_status(raw.get("status")),
            thread_id=str(raw["thread_id"]).strip(),
            created_at=str(raw["created_at"]).strip(),
            updated_at=str(raw["updated_at"]).strip(),
            token_budget=_coerce_optional_int(raw.get("token_budget")),
            tokens_used=_coerce_int(raw.get("tokens_used"), default=0),
            completed_at=_coerce_optional_str(raw.get("completed_at")),
            summary=_coerce_optional_str(raw.get("summary")),
        )

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def with_tokens_used(self, tokens_used: int) -> GoalState:
        return replace(
            self,
            tokens_used=max(0, tokens_used),
            updated_at=utc_now_iso(),
        )

    def complete(self, *, summary: str | None = None) -> GoalState:
        now = utc_now_iso()
        return replace(
            self,
            status="complete",
            updated_at=now,
            completed_at=now,
            summary=summary or self.summary,
        )

    def cancel(self, *, summary: str | None = None) -> GoalState:
        return replace(
            self,
            status="cancelled",
            updated_at=utc_now_iso(),
            summary=summary or self.summary,
        )

    @property
    def is_active(self) -> bool:
        return self.status == "active"


@dataclass(frozen=True, slots=True)
class GoalCommand:
    """Parsed `/goal` command intent."""

    kind: GoalCommandKind
    objective: str | None = None
    token_budget: int | None = None
    error: str | None = None


def _coerce_status(value: object) -> GoalStatus:
    if value in {"active", "complete", "cancelled"}:
        return value  # type: ignore[return-value]
    return "active"


def _coerce_optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _coerce_int(value: object, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed


def _coerce_optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
