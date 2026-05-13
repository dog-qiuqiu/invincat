"""Memory and offload runtime helpers for the Textual app."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from invincat_cli.core.session_stats import format_token_count


AUTO_OFFLOAD_THRESHOLD = 0.8
AUTO_OFFLOAD_COOLDOWN_SECONDS = 300


@dataclass(frozen=True, slots=True)
class AutoOffloadDecision:
    """Decision metadata for an automatic offload attempt."""

    usage_ratio: float
    cooldown_until: float

    @property
    def usage_percent(self) -> int:
        return round(self.usage_ratio * 100)


@dataclass(frozen=True, slots=True)
class MemoryUpdateNotification:
    """Display data for a memory update status notification."""

    count: int
    short_path: str | None = None


def resolve_auto_offload_decision(
    *,
    tokens_approximate: bool,
    now: float,
    cooldown_until: float,
    context_tokens: int,
    context_limit: int | None,
    threshold: float = AUTO_OFFLOAD_THRESHOLD,
    cooldown_seconds: float = AUTO_OFFLOAD_COOLDOWN_SECONDS,
) -> AutoOffloadDecision | None:
    """Return decision metadata when an automatic offload should run."""
    if tokens_approximate:
        return None
    if now < cooldown_until:
        return None
    if not context_limit or not context_tokens:
        return None

    usage_ratio = context_tokens / context_limit
    if usage_ratio < threshold:
        return None
    return AutoOffloadDecision(
        usage_ratio=usage_ratio,
        cooldown_until=now + cooldown_seconds,
    )


def build_auto_offload_message(decision: AutoOffloadDecision) -> str:
    """Build the status message shown before an automatic offload."""
    return (
        f"Context window is {decision.usage_percent}% full \u2014 "
        "automatically offloading older messages\u2026"
    )


def resolve_memory_update_notification(
    updated_paths: object,
    *,
    home: Path,
) -> MemoryUpdateNotification | None:
    """Return display metadata for memory files updated during a turn."""
    if not updated_paths:
        return None
    if isinstance(updated_paths, str):
        paths = [updated_paths]
    elif isinstance(updated_paths, Sequence):
        paths = [str(path) for path in updated_paths]
    else:
        return None

    if not paths:
        return None
    if len(paths) > 1:
        return MemoryUpdateNotification(count=len(paths))

    path = paths[0]
    try:
        short = "~/" + str(Path(path).relative_to(home))
    except ValueError:
        short = path
    return MemoryUpdateNotification(count=1, short_path=short)


def format_memory_update_success(
    notification: MemoryUpdateNotification,
    *,
    single_template: str,
    multiple_template: str,
) -> str:
    """Render the success status message for a memory update notification."""
    if notification.count == 1 and notification.short_path is not None:
        return single_template.format(path=notification.short_path)
    return multiple_template.format(n=notification.count)


def build_offload_budget_cache_key(
    *,
    model_provider: str,
    model_name: str,
    model_context_limit: int | None,
    profile_override: Mapping[str, Any] | None,
) -> tuple[Any, ...]:
    """Build the stable cache key for the resolved offload budget string."""
    return (
        model_provider,
        model_name,
        model_context_limit,
        tuple(sorted(profile_override.items())) if profile_override else (),
    )


def build_offload_threshold_not_met_message(
    *,
    conversation_tokens: int,
    total_context_tokens: int,
    context_limit: int | None,
    budget_str: str,
) -> str:
    """Build the user-facing message for an offload skipped by threshold."""
    conv_str = format_token_count(conversation_tokens)
    if (
        total_context_tokens > 0
        and context_limit is not None
        and total_context_tokens > context_limit
    ):
        total_str = format_token_count(total_context_tokens)
        return (
            "Offload threshold not met \u2014 conversation "
            f"is only ~{conv_str} tokens.\n\n"
            "The remaining context "
            f"({total_str} tokens) is system overhead "
            "that can't be offloaded.\n\n"
            "Use /tokens for a full breakdown."
        )
    return (
        "Offload threshold not met \u2014 conversation "
        f"(~{conv_str} tokens) is within the "
        "retention budget "
        f"({budget_str}).\n\n"
        "Use /tokens for a full breakdown."
    )


def build_offload_success_message(
    *,
    messages_offloaded: int,
    tokens_before: int,
    tokens_after: int,
    pct_decrease: int,
    messages_kept: int,
) -> str:
    """Build the user-facing message for a successful offload."""
    before = format_token_count(tokens_before)
    after = format_token_count(tokens_after)
    return (
        f"Offloaded {messages_offloaded} older messages, "
        "freeing up context window space.\n"
        f"Context: {before} \u2192 {after} tokens "
        f"({pct_decrease}% decrease), "
        f"{messages_kept} messages kept."
    )
