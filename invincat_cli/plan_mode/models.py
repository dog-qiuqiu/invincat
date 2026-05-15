"""Structured plan-mode state and transition models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, NotRequired, TypedDict


class PlanModeStatus(str, Enum):
    """Lifecycle states for `/plan` mode."""

    OFF = "off"
    PLANNING = "planning"
    WAITING_APPROVAL = "waiting_approval"
    HANDOFF_PENDING = "handoff_pending"
    EXECUTING = "executing"
    DRIFTED = "drifted"
    CANCELLED = "cancelled"


class PlanStep(TypedDict):
    """Normalized plan item accepted by the planner and handoff layers."""

    id: str
    content: str
    status: Literal["pending", "in_progress", "completed"]
    rationale: NotRequired[str]
    target: NotRequired[list[str]]
    verification: NotRequired[str]
    risk: NotRequired[Literal["low", "medium", "high"]]


class PlanDrift(TypedDict):
    """Planner policy violation detected after a turn."""

    reason: Literal[
        "missing_todos",
        "final_answer",
        "disallowed_tool",
        "missing_approval",
        "todo_mismatch",
    ]
    message: str


class PlanSession(TypedDict, total=False):
    """Serializable high-level plan session data."""

    status: PlanModeStatus
    original_task: str
    refinement_notes: list[str]
    rejected_plan: list[PlanStep]


PlanTurnResolutionKind = Literal[
    "noop",
    "approved",
    "rejected",
    "drifted",
    "prompt_todos",
    "already_prompted",
    "approval_no_valid_todos",
    "ready_no_valid_todos",
]


@dataclass(frozen=True, slots=True)
class PlanTurnResolution:
    """Pure result of evaluating the latest planner checkpoint state."""

    kind: PlanTurnResolutionKind
    todos: list[dict[str, str]] | None = None
    drift: PlanDrift | None = None
    suppress_refine_prompt: bool = False

