"""Agent turn runtime helpers for the Textual app."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from invincat_cli.app_errors import (
    format_exception_details,
    is_scheduled_retryable_error,
    looks_like_masked_internal_error,
)
from invincat_cli.app_state import QueuedMessage
from invincat_cli.core.cli_context import CLIContext

WeComFileRequestHandler = Callable[[dict[str, Any]], Awaitable[None]]
TextDeltaHandler = Callable[[str, str], Awaitable[None]]
PostTurnHook = Callable[[], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AgentTurnRequest:
    """Immutable parameters for one background agent turn."""

    message: str
    message_kwargs: dict[str, Any] | None = None
    generation: int = 0
    agent_override: Any | None = None
    thread_id_override: str | None = None
    post_turn_hook: PostTurnHook | None = None
    on_text_delta: TextDeltaHandler | None = None
    on_wecom_file_request: WeComFileRequestHandler | None = None


@dataclass(frozen=True, slots=True)
class QueuedScheduledRunState:
    """State to apply when a queued message starts a scheduled run."""

    active_run: tuple[str, str] | None
    message_offset: int | None
    turn_status: str
    turn_error: str | None
    retry_used: bool


class AgentThreadOverrideContext:
    """Temporarily apply a thread override to a session state object."""

    def __init__(self, session_state: Any, thread_id_override: str | None) -> None:
        self._session_state = session_state
        self._thread_id_override = thread_id_override
        self._original_thread_id: str | None = None

    def enter(self) -> None:
        self._original_thread_id = getattr(self._session_state, "thread_id", None)
        if self._thread_id_override:
            self._session_state.thread_id = self._thread_id_override

    def exit(self) -> None:
        if self._thread_id_override and self._original_thread_id is not None:
            self._session_state.thread_id = self._original_thread_id


def scheduled_run_from_message(message: QueuedMessage) -> tuple[str, str] | None:
    """Return scheduled run identity encoded in a queued message, if present."""
    if message.scheduled_run_id and message.scheduled_task_id:
        return (message.scheduled_run_id, message.scheduled_task_id)
    return None


def queued_scheduled_run_state(
    message: QueuedMessage,
    *,
    message_offset: int,
) -> QueuedScheduledRunState:
    """Build scheduled-run state for a queued message about to run."""
    scheduled_run = scheduled_run_from_message(message)
    if scheduled_run is None:
        return QueuedScheduledRunState(
            active_run=None,
            message_offset=None,
            turn_status="success",
            turn_error=None,
            retry_used=False,
        )
    return QueuedScheduledRunState(
        active_run=scheduled_run,
        message_offset=message_offset,
        turn_status="success",
        turn_error=None,
        retry_used=False,
    )


def should_route_message_to_planner(session_state: object | None) -> bool:
    """Return whether a user message should be routed to the planner."""
    return bool(session_state is not None and getattr(session_state, "plan_mode", False))


def should_clear_scheduled_run_before_send(*, processing_pending: bool) -> bool:
    """Return whether direct sends should clear stale scheduled-run context."""
    return not processing_pending


def can_start_agent_turn(
    *,
    target_agent: object | None,
    ui_adapter: object | None,
    session_state: object | None,
) -> bool:
    """Return whether all runtime pieces are present for an agent turn."""
    return target_agent is not None and ui_adapter is not None and session_state is not None


def is_planner_agent_turn(
    *,
    agent_override: object | None,
    target_agent: object | None,
    planner_agent: object | None,
    thread_id_override: str | None,
    planner_thread_id: str | None,
) -> bool:
    """Return whether this agent turn targets the planner peer-agent."""
    return bool(
        agent_override is not None
        and target_agent is planner_agent
        and thread_id_override == planner_thread_id
    )


def is_current_agent_generation(*, generation: int, current_generation: int) -> bool:
    """Return whether cleanup belongs to the currently active agent worker."""
    return generation == current_generation


def should_continue_after_deferred_actions(
    *,
    agent_running: bool,
    shell_running: bool,
) -> bool:
    """Return whether post-cleanup side effects may continue after deferred actions."""
    return not (agent_running or shell_running)


def should_retry_scheduled_turn(
    *,
    active_scheduled_run: tuple[str, str] | None,
    retry_used: bool,
    exc: BaseException,
) -> bool:
    """Return whether a scheduled turn should retry once after an exception."""
    return (
        active_scheduled_run is not None
        and not retry_used
        and is_scheduled_retryable_error(exc)
    )


def build_agent_error_detail(
    exc: BaseException,
    *,
    server_log_tail: str | None = None,
) -> str:
    """Build the UI error detail for a failed agent turn."""
    error_detail = format_exception_details(exc)
    if (
        server_log_tail
        and server_log_tail.strip()
        and looks_like_masked_internal_error(exc)
    ):
        return f"{error_detail}\n\n[server log tail]\n{server_log_tail}"
    return error_detail


def build_agent_cli_context(
    *,
    model: str | None,
    model_params: dict[str, Any] | None,
    memory_model: str | None,
    memory_model_params: dict[str, Any] | None,
    wecom_enabled: bool,
    scheduled_run: bool,
) -> CLIContext:
    """Build the CLIContext passed into a single agent turn."""
    return CLIContext(
        model=model,
        model_params=model_params or {},
        memory_model=memory_model,
        memory_model_params=memory_model_params or {},
        wecom_enabled=wecom_enabled,
        scheduled_run=scheduled_run,
    )


def resolve_wecom_file_request_handler(
    *,
    explicit_handler: WeComFileRequestHandler | None,
    active_scheduled_wecom_chat_id: str | None,
    scheduled_handler: WeComFileRequestHandler,
) -> WeComFileRequestHandler | None:
    """Choose the WeCom file request handler for a turn."""
    if explicit_handler is not None:
        return explicit_handler
    if active_scheduled_wecom_chat_id is not None:
        return scheduled_handler
    return None
