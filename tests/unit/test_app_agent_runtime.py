"""Tests for agent turn runtime helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from invincat_cli.app_runtime.agent import (
    AgentThreadOverrideContext,
    AgentTurnRequest,
    build_agent_cli_context,
    build_agent_error_detail,
    can_start_agent_turn,
    is_current_agent_generation,
    is_planner_agent_turn,
    next_agent_turn_start_state,
    queued_scheduled_run_state,
    resolve_agent_cleanup_start_state,
    resolve_agent_task_exception_decision,
    resolve_wecom_file_request_handler,
    scheduled_run_from_message,
    should_clear_scheduled_run_before_send,
    should_continue_after_deferred_actions,
    should_continue_queue_after_sync_message,
    should_process_next_from_queue,
    should_retry_scheduled_turn,
    should_route_message_to_planner,
)
from invincat_cli.app_runtime.state import QueuedMessage


def test_scheduled_run_from_message_requires_both_ids() -> None:
    assert scheduled_run_from_message(
        QueuedMessage(
            text="run",
            mode="normal",
            scheduled_run_id="run-1",
            scheduled_task_id="task-1",
        )
    ) == ("run-1", "task-1")
    assert (
        scheduled_run_from_message(
            QueuedMessage(text="run", mode="normal", scheduled_run_id="run-1")
        )
        is None
    )


def test_queued_scheduled_run_state() -> None:
    scheduled = queued_scheduled_run_state(
        QueuedMessage(
            text="run",
            mode="normal",
            scheduled_run_id="run-1",
            scheduled_task_id="task-1",
        ),
        message_offset=42,
    )

    assert scheduled.active_run == ("run-1", "task-1")
    assert scheduled.message_offset == 42
    assert scheduled.turn_status == "success"
    assert scheduled.turn_error is None
    assert scheduled.retry_used is False

    unscheduled = queued_scheduled_run_state(
        QueuedMessage(text="run", mode="normal"),
        message_offset=42,
    )
    assert unscheduled.active_run is None
    assert unscheduled.message_offset is None


def test_should_route_message_to_planner() -> None:
    class _State:
        plan_mode = True

    assert should_route_message_to_planner(_State()) is True
    assert should_route_message_to_planner(object()) is False
    assert should_route_message_to_planner(None) is False


def test_should_clear_scheduled_run_before_send() -> None:
    assert should_clear_scheduled_run_before_send(processing_pending=False) is True
    assert should_clear_scheduled_run_before_send(processing_pending=True) is False


def test_should_process_next_from_queue() -> None:
    assert should_process_next_from_queue(
        processing_pending=False,
        has_pending_messages=True,
        exiting=False,
    )
    assert not should_process_next_from_queue(
        processing_pending=True,
        has_pending_messages=True,
        exiting=False,
    )
    assert not should_process_next_from_queue(
        processing_pending=False,
        has_pending_messages=False,
        exiting=False,
    )
    assert not should_process_next_from_queue(
        processing_pending=False,
        has_pending_messages=True,
        exiting=True,
    )


def test_should_continue_queue_after_sync_message() -> None:
    assert should_continue_queue_after_sync_message(
        agent_running=False,
        shell_running=False,
        has_pending_messages=True,
    )
    assert not should_continue_queue_after_sync_message(
        agent_running=True,
        shell_running=False,
        has_pending_messages=True,
    )
    assert not should_continue_queue_after_sync_message(
        agent_running=False,
        shell_running=True,
        has_pending_messages=True,
    )
    assert not should_continue_queue_after_sync_message(
        agent_running=False,
        shell_running=False,
        has_pending_messages=False,
    )


def test_can_start_agent_turn_requires_runtime_pieces() -> None:
    assert (
        can_start_agent_turn(
            target_agent=object(),
            ui_adapter=object(),
            session_state=object(),
        )
        is True
    )
    assert (
        can_start_agent_turn(
            target_agent=None,
            ui_adapter=object(),
            session_state=object(),
        )
        is False
    )


def test_is_planner_agent_turn() -> None:
    planner = object()

    assert (
        is_planner_agent_turn(
            agent_override=planner,
            target_agent=planner,
            planner_agent=planner,
            thread_id_override="planner-thread",
            planner_thread_id="planner-thread",
        )
        is True
    )
    assert (
        is_planner_agent_turn(
            agent_override=None,
            target_agent=planner,
            planner_agent=planner,
            thread_id_override="planner-thread",
            planner_thread_id="planner-thread",
        )
        is False
    )


def test_next_agent_turn_start_state() -> None:
    planner = object()

    state = next_agent_turn_start_state(
        current_generation=4,
        agent_override=planner,
        target_agent=planner,
        planner_agent=planner,
        thread_id_override="planner-thread",
        planner_thread_id="planner-thread",
    )

    assert state.generation == 5
    assert state.active_turn_is_planner is True

    normal_state = next_agent_turn_start_state(
        current_generation=5,
        agent_override=None,
        target_agent=object(),
        planner_agent=planner,
        thread_id_override=None,
        planner_thread_id="planner-thread",
    )

    assert normal_state.generation == 6
    assert normal_state.active_turn_is_planner is False


def test_agent_cleanup_decisions() -> None:
    assert is_current_agent_generation(generation=2, current_generation=2) is True
    assert is_current_agent_generation(generation=1, current_generation=2) is False
    assert (
        should_continue_after_deferred_actions(
            agent_running=False,
            shell_running=False,
        )
        is True
    )
    assert (
        should_continue_after_deferred_actions(
            agent_running=True,
            shell_running=False,
        )
        is False
    )


def test_resolve_agent_cleanup_start_state() -> None:
    current = resolve_agent_cleanup_start_state(generation=2, current_generation=2)

    assert current.is_current_generation is True
    assert current.should_reset_running_state is True
    assert current.should_restore_input is True
    assert current.should_restore_tokens is True
    assert current.should_skip_post_cleanup is False

    stale = resolve_agent_cleanup_start_state(generation=1, current_generation=2)

    assert stale.is_current_generation is False
    assert stale.should_reset_running_state is False
    assert stale.should_restore_input is False
    assert stale.should_restore_tokens is False
    assert stale.should_skip_post_cleanup is True


def test_agent_thread_override_context_restores_thread_id() -> None:
    session_state = SimpleNamespace(thread_id="main-thread")
    context = AgentThreadOverrideContext(session_state, "planner-thread")

    context.enter()
    assert session_state.thread_id == "planner-thread"
    context.exit()
    assert session_state.thread_id == "main-thread"


def test_agent_thread_override_context_without_override_is_noop() -> None:
    session_state = SimpleNamespace(thread_id="main-thread")
    context = AgentThreadOverrideContext(session_state, None)

    context.enter()
    assert session_state.thread_id == "main-thread"
    context.exit()
    assert session_state.thread_id == "main-thread"


def test_agent_turn_request_keeps_retry_parameters_together() -> None:
    async def hook() -> None:
        return None

    async def delta(_message_id: str, _text: str) -> None:
        return None

    async def wecom(_payload: dict) -> None:
        return None

    request = AgentTurnRequest(
        message="hello",
        message_kwargs={"additional_kwargs": {"x": 1}},
        generation=3,
        agent_override="agent",
        thread_id_override="thread-2",
        post_turn_hook=hook,
        on_text_delta=delta,
        on_wecom_file_request=wecom,
    )

    assert request.message == "hello"
    assert request.generation == 3
    assert request.agent_override == "agent"
    assert request.thread_id_override == "thread-2"
    assert request.post_turn_hook is hook
    assert request.on_text_delta is delta
    assert request.on_wecom_file_request is wecom


def test_should_retry_scheduled_turn() -> None:
    assert should_retry_scheduled_turn(
        active_scheduled_run=("run-1", "task-1"),
        retry_used=False,
        exc=TimeoutError("timed out"),
    )
    assert not should_retry_scheduled_turn(
        active_scheduled_run=("run-1", "task-1"),
        retry_used=True,
        exc=TimeoutError("timed out"),
    )
    assert not should_retry_scheduled_turn(
        active_scheduled_run=None,
        retry_used=False,
        exc=TimeoutError("timed out"),
    )
    assert not should_retry_scheduled_turn(
        active_scheduled_run=("run-1", "task-1"),
        retry_used=False,
        exc=ValueError("bad input"),
    )


def test_resolve_agent_task_exception_decision_retries_scheduled_transient() -> None:
    decision = resolve_agent_task_exception_decision(
        active_scheduled_run=("run-1", "task-1"),
        retry_used=False,
        exc=TimeoutError("timed out"),
    )

    assert decision.retry is True
    assert decision.scheduled_turn_status is None
    assert decision.scheduled_turn_error is None
    assert decision.retry_notice == (
        "Scheduled task hit a transient model/network error; retrying once..."
    )


def test_resolve_agent_task_exception_decision_marks_failure() -> None:
    decision = resolve_agent_task_exception_decision(
        active_scheduled_run=("run-1", "task-1"),
        retry_used=True,
        exc=ValueError("bad input"),
    )

    assert decision.retry is False
    assert decision.scheduled_turn_status == "failed"
    assert decision.scheduled_turn_error == "ValueError: bad input"
    assert decision.retry_notice is None


def test_build_agent_error_detail_appends_server_tail_for_masked_error() -> None:
    detail = build_agent_error_detail(
        RuntimeError("An internal error occurred"),
        server_log_tail="provider stacktrace",
    )

    assert "RuntimeError: An internal error occurred" in detail
    assert "[server log tail]\nprovider stacktrace" in detail


def test_build_agent_error_detail_omits_tail_for_specific_error() -> None:
    detail = build_agent_error_detail(
        ValueError("bad input"),
        server_log_tail="provider stacktrace",
    )

    assert detail == "ValueError: bad input"


def test_build_agent_cli_context_defaults_param_dicts() -> None:
    context = build_agent_cli_context(
        model="primary:model",
        model_params=None,
        memory_model=None,
        memory_model_params=None,
        wecom_enabled=True,
        scheduled_run=True,
    )

    assert context["model"] == "primary:model"
    assert context["model_params"] == {}
    assert context["memory_model"] is None
    assert context["memory_model_params"] == {}
    assert context["wecom_enabled"] is True
    assert context["scheduled_run"] is True


def test_resolve_wecom_file_request_handler() -> None:
    async def explicit(_payload: dict) -> None:
        return None

    async def scheduled(_payload: dict) -> None:
        return None

    assert (
        resolve_wecom_file_request_handler(
            explicit_handler=explicit,
            active_scheduled_wecom_chat_id="chat-1",
            scheduled_handler=scheduled,
        )
        is explicit
    )
    assert (
        resolve_wecom_file_request_handler(
            explicit_handler=None,
            active_scheduled_wecom_chat_id="chat-1",
            scheduled_handler=scheduled,
        )
        is scheduled
    )
    assert (
        resolve_wecom_file_request_handler(
            explicit_handler=None,
            active_scheduled_wecom_chat_id=None,
            scheduled_handler=scheduled,
        )
        is None
    )

    asyncio.run(scheduled({}))
