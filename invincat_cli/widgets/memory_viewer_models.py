"""Data models for the memory viewer."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MemoryItemView:
    """Single memory item rendered in the viewer."""

    scope: str
    item_id: str
    section: str
    status: str
    content: str
    tier: str
    score: int
    reason: str
    last_scored_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class MemoryScopeView:
    """Snapshot for one memory scope."""

    scope: str
    path: str
    exists: bool
    valid: bool
    error: str | None
    total: int
    active: int
    archived: int
    latest_updated_at: str | None
    items: list[MemoryItemView]
