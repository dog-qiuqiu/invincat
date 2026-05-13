"""UI action helpers for the Textual app."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from invincat_cli.model_config import ModelSpec
from invincat_cli.project_utils import find_project_root


@dataclass(frozen=True, slots=True)
class ChatScrollSnapshot:
    """Captured scroll state for a modal overlay."""

    y: float
    was_anchored: bool


@dataclass(frozen=True, slots=True)
class ModelSelectorState:
    """Current primary and memory model fields for the selector screen."""

    current_provider: str | None
    current_model: str | None
    memory_provider: str | None
    memory_model: str | None


class ChatScroll(Protocol):
    """Small protocol for the chat scroll operations used around modals."""

    scroll_y: float
    is_anchored: bool

    def release_anchor(self) -> None: ...

    def scroll_to(self, *, y: float, animate: bool = False) -> None: ...

    def anchor(self) -> None: ...


def capture_chat_scroll_state(chat: ChatScroll) -> ChatScrollSnapshot:
    """Capture current chat scroll state and release bottom anchoring."""
    snapshot = ChatScrollSnapshot(y=chat.scroll_y, was_anchored=chat.is_anchored)
    chat.release_anchor()
    return snapshot


def restore_chat_scroll_state(
    chat: ChatScroll,
    snapshot: ChatScrollSnapshot,
) -> None:
    """Restore chat scroll position and re-anchor when needed."""
    chat.scroll_to(y=snapshot.y, animate=False)
    if snapshot.was_anchored:
        chat.anchor()


def should_defer_modal_action(
    *,
    agent_running: bool,
    shell_running: bool,
    connecting: bool,
) -> bool:
    """Return whether a modal result should be deferred until idle."""
    return agent_running or shell_running or connecting


def parse_selector_model_spec(
    spec: str | None,
) -> tuple[str | None, str | None]:
    """Parse a selector model spec into provider/model fields."""
    if not spec:
        return None, None
    parsed = ModelSpec.try_parse(spec)
    if parsed:
        return parsed.provider, parsed.model
    return None, spec


def primary_model_spec(
    provider: str | None,
    model_name: str | None,
) -> str | None:
    """Return the current primary model spec if both fields are set."""
    if provider and model_name:
        return f"{provider}:{model_name}"
    return None


def resolve_model_selector_state(
    *,
    settings_model_provider: str | None,
    settings_model_name: str | None,
    memory_model_override: str | None,
) -> ModelSelectorState:
    """Resolve current selector fields for primary and memory targets."""
    current_primary_spec = primary_model_spec(
        settings_model_provider,
        settings_model_name,
    )
    current_memory_spec = memory_model_override or current_primary_spec

    current_provider, current_model = parse_selector_model_spec(current_primary_spec)
    memory_provider, memory_model = parse_selector_model_spec(current_memory_spec)
    return ModelSelectorState(
        current_provider=current_provider,
        current_model=current_model,
        memory_provider=memory_provider,
        memory_model=memory_model,
    )


def resolve_memory_store_paths(
    *,
    cwd: str | Path,
    assistant_id: str | None,
    get_agent_dir: Callable[[str], Path],
    project_root_finder: Callable[[Path], Path | None] = find_project_root,
) -> dict[str, str]:
    """Resolve user/project memory store paths for the current session."""
    resolved_assistant_id = assistant_id or "agent"
    user_store = get_agent_dir(resolved_assistant_id) / "memory_user.json"

    resolved_cwd = Path(cwd).expanduser().resolve()
    project_root = project_root_finder(resolved_cwd)
    project_store_dir = (
        project_root / ".invincat"
        if project_root is not None
        else resolved_cwd / ".invincat"
    )

    return {
        "user": str(user_store.expanduser().resolve()),
        "project": str(
            (project_store_dir / "memory_project.json").expanduser().resolve()
        ),
    }
