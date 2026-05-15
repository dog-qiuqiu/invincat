"""Sorting helpers for memory viewer items."""

from __future__ import annotations

from invincat_cli.widgets.memory_viewer_models import MemoryItemView

SORT_MODES: tuple[str, ...] = (
    "score_desc",
    "score_asc",
    "last_scored_desc",
    "last_scored_asc",
)
TIER_RANK: dict[str, int] = {"hot": 0, "warm": 1, "cold": 2}


def apply_sort(items: list[MemoryItemView], sort_mode: str) -> list[MemoryItemView]:
    """Sort items keeping active before archived, then apply the chosen sort mode."""
    active = [i for i in items if i.status == "active"]
    archived = [i for i in items if i.status == "archived"]

    def key_score_asc(i: MemoryItemView) -> tuple:
        return (i.score, TIER_RANK.get(i.tier, 1), i.section.casefold(), i.item_id)

    def key_last_scored(i: MemoryItemView) -> tuple:
        return (i.last_scored_at or "", i.section.casefold(), i.item_id)

    def key_score_desc(i: MemoryItemView) -> tuple:
        return (-i.score, TIER_RANK.get(i.tier, 1), i.section.casefold(), i.item_id)

    if sort_mode == "score_asc":
        key, reverse = key_score_asc, False
    elif sort_mode == "last_scored_desc":
        key, reverse = key_last_scored, True
    elif sort_mode == "last_scored_asc":
        key, reverse = key_last_scored, False
    else:
        key, reverse = key_score_desc, False

    return sorted(active, key=key, reverse=reverse) + sorted(
        archived, key=key, reverse=reverse
    )
