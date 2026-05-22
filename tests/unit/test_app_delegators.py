from __future__ import annotations

import asyncio
import builtins
import codecs
import importlib
from types import SimpleNamespace
from typing import Any

import pytest

from invincat_cli import app as app_module
from invincat_cli.app import DeepAgentsApp


def test_patch_textual_utf8_decoder_installs_tolerant_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import textual.drivers.linux_driver as linux_driver

    monkeypatch.setattr(
        linux_driver,
        "getincrementaldecoder",
        codecs.getincrementaldecoder,
    )

    app_module._patch_textual_utf8_decoder()

    decoder_cls = linux_driver.getincrementaldecoder("utf-8")
    decoder = decoder_cls()
    assert decoder.errors == "replace"
    assert linux_driver.getincrementaldecoder(
        "latin-1"
    ) is codecs.getincrementaldecoder("latin-1")


def test_patch_textual_utf8_decoder_ignores_patch_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_import = builtins.__import__

    def fake_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if name == "textual.drivers.linux_driver":
            raise RuntimeError("driver unavailable")
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    app_module._patch_textual_utf8_decoder()


def _app() -> DeepAgentsApp:
    app = DeepAgentsApp.__new__(DeepAgentsApp)
    app._status_bar = None
    app._chat_input = None
    app._pending_approval_widget = None
    app._pending_ask_user_widget = None
    return app


def test_server_messages_and_remote_agent_helper() -> None:
    from invincat_cli.remote.client import RemoteAgent

    ready = DeepAgentsApp.ServerReady(
        agent="agent",
        server_proc="proc",
        mcp_server_info=["server"],
        model="model",
    )
    failed = DeepAgentsApp.ServerStartFailed(RuntimeError("boom"))
    app = _app()
    remote_agent = RemoteAgent("http://127.0.0.1:2024")

    app._agent = remote_agent
    assert ready.agent == "agent"
    assert ready.server_proc == "proc"
    assert ready.mcp_server_info == ["server"]
    assert ready.model == "model"
    assert str(failed.error) == "boom"
    assert app._remote_agent() is remote_agent

    app._agent = object()
    assert app._remote_agent() is None


def _patch_sync(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    function_name: str,
    *,
    result: Any = None,
) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    module = importlib.import_module(module_name)

    def fake(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(module, function_name, fake)
    return calls


def _patch_async(
    monkeypatch: pytest.MonkeyPatch,
    module_name: str,
    function_name: str,
    *,
    result: Any = None,
) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    module = importlib.import_module(module_name)

    async def fake(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(module, function_name, fake)
    return calls


@pytest.mark.parametrize(
    ("method_name", "module_name", "function_name", "args", "kwargs", "result"),
    [
        (
            "get_theme_variable_defaults",
            "invincat_cli.app_runtime.layout",
            "get_theme_variable_defaults",
            (),
            {},
            {"primary": "#fff"},
        ),
        (
            "_discover_skills_and_roots",
            "invincat_cli.app_runtime.startup_handlers",
            "discover_skills_and_roots",
            (),
            {},
            (["skill"], ["root"]),
        ),
        (
            "on_deep_agents_app_server_ready",
            "invincat_cli.app_runtime.server_handlers",
            "handle_server_ready",
            ("event",),
            {},
            None,
        ),
        (
            "on_deep_agents_app_server_start_failed",
            "invincat_cli.app_runtime.server_handlers",
            "handle_server_start_failed",
            ("event",),
            {},
            None,
        ),
        (
            "_check_hydration_needed",
            "invincat_cli.app_runtime.message_flow",
            "check_hydration_needed",
            (),
            {},
            None,
        ),
        (
            "_is_spinner_at_correct_position",
            "invincat_cli.app_runtime.message_flow",
            "is_spinner_at_correct_position",
            ("container",),
            {},
            True,
        ),
        (
            "_on_auto_approve_enabled",
            "invincat_cli.app_runtime.approval_handlers",
            "enable_auto_approve",
            (),
            {},
            None,
        ),
        (
            "_reset_plan_mode_state",
            "invincat_cli.app_runtime.plan_handlers",
            "reset_plan_mode_state",
            (),
            {},
            None,
        ),
        (
            "_can_bypass_queue",
            "invincat_cli.app_runtime.input_handlers",
            "can_bypass_queue",
            ("/model",),
            {},
            True,
        ),
        (
            "_start_scheduler",
            "invincat_cli.app_runtime.scheduled_delivery",
            "start_scheduler",
            (),
            {},
            None,
        ),
        (
            "_cancel_timed_out_scheduled_turn",
            "invincat_cli.app_runtime.scheduled_delivery",
            "cancel_timed_out_scheduled_turn",
            ("run-1", "task-1"),
            {},
            None,
        ),
        (
            "_wecom_enqueue",
            "invincat_cli.app_runtime.wecom_handlers",
            "wecom_enqueue",
            ({"type": "text"},),
            {},
            None,
        ),
        (
            "_on_memory_update_done",
            "invincat_cli.app_runtime.memory_handlers",
            "on_memory_update_done",
            ("done",),
            {},
            None,
        ),
        (
            "_clear_memory_status",
            "invincat_cli.app_runtime.memory_handlers",
            "clear_memory_status",
            (),
            {},
            None,
        ),
        (
            "_resolve_offload_budget_str",
            "invincat_cli.app_runtime.memory_handlers",
            "resolve_offload_budget_str",
            (),
            {},
            "20K",
        ),
        (
            "_finish_active_scheduled_run_as_failed",
            "invincat_cli.app_runtime.agent_handlers",
            "finish_active_scheduled_run_as_failed",
            ("boom",),
            {},
            None,
        ),
        (
            "_agent_error_detail_with_server_log",
            "invincat_cli.app_runtime.agent_handlers",
            "agent_error_detail_with_server_log",
            (RuntimeError("boom"),),
            {},
            "detail",
        ),
        (
            "_handle_stale_agent_cleanup",
            "invincat_cli.app_runtime.agent_handlers",
            "handle_stale_agent_cleanup",
            (),
            {"generation": 3},
            None,
        ),
        (
            "_pop_last_queued_message",
            "invincat_cli.app_runtime.queue_handlers",
            "pop_last_queued_message",
            (),
            {},
            None,
        ),
        (
            "_discard_queue",
            "invincat_cli.app_runtime.queue_handlers",
            "discard_queue",
            (),
            {},
            None,
        ),
        (
            "_defer_action",
            "invincat_cli.app_runtime.deferred_handlers",
            "defer_action",
            ("action",),
            {},
            None,
        ),
        (
            "action_quit_or_interrupt",
            "invincat_cli.app_runtime.action_handlers",
            "quit_or_interrupt",
            (),
            {},
            None,
        ),
        (
            "action_interrupt",
            "invincat_cli.app_runtime.action_handlers",
            "interrupt",
            (),
            {},
            None,
        ),
        (
            "action_quit_app",
            "invincat_cli.app_runtime.action_handlers",
            "quit_app",
            (),
            {},
            None,
        ),
        (
            "action_toggle_auto_approve",
            "invincat_cli.app_runtime.action_handlers",
            "toggle_auto_approve",
            (),
            {},
            None,
        ),
        (
            "action_toggle_tool_output",
            "invincat_cli.app_runtime.action_handlers",
            "toggle_tool_output",
            (),
            {},
            None,
        ),
        (
            "_register_custom_themes",
            "invincat_cli.app_runtime.layout",
            "register_custom_themes",
            (),
            {},
            None,
        ),
        (
            "_refresh_all_ui_text",
            "invincat_cli.app_runtime.ui_handlers",
            "refresh_all_ui_text",
            (),
            {},
            None,
        ),
        (
            "_resolve_memory_store_paths",
            "invincat_cli.app_runtime.ui_handlers",
            "resolve_memory_store_paths",
            (),
            {},
            {"user": "path"},
        ),
        (
            "_apply_thread_switch_ids",
            "invincat_cli.app_runtime.thread_handlers",
            "apply_thread_switch_ids",
            ("thread-1",),
            {},
            None,
        ),
        (
            "_rollback_thread_switch_ids",
            "invincat_cli.app_runtime.thread_handlers",
            "rollback_thread_switch_ids",
            ("snapshot",),
            {},
            None,
        ),
        (
            "_start_server_after_primary_model_switch",
            "invincat_cli.app_runtime.model_handlers",
            "start_server_after_primary_model_switch",
            (),
            {"resolved": "resolved", "target_kwargs": {"temperature": 0}},
            None,
        ),
        (
            "_apply_primary_model_status",
            "invincat_cli.app_runtime.model_handlers",
            "apply_primary_model_status",
            (),
            {"model_result": "model"},
            None,
        ),
    ],
)
def test_sync_app_methods_delegate_to_runtime_modules(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    module_name: str,
    function_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    result: Any,
) -> None:
    app = _app()
    calls = _patch_sync(monkeypatch, module_name, function_name, result=result)

    actual = getattr(app, method_name)(*args, **kwargs)

    assert actual == result
    assert calls == [((app, *args), kwargs)]


@pytest.mark.parametrize(
    ("method_name", "module_name", "function_name", "args", "kwargs", "result"),
    [
        (
            "on_mount",
            "invincat_cli.app_runtime.startup_handlers",
            "handle_mount",
            (),
            {},
            None,
        ),
        (
            "_resolve_git_branch_and_continue",
            "invincat_cli.app_runtime.startup_handlers",
            "resolve_git_branch_and_continue",
            (),
            {},
            None,
        ),
        (
            "_post_paint_init",
            "invincat_cli.app_runtime.startup_handlers",
            "post_paint_init",
            (),
            {},
            None,
        ),
        (
            "_check_optional_tools_background",
            "invincat_cli.app_runtime.startup_handlers",
            "check_optional_tools_background",
            (),
            {},
            None,
        ),
        (
            "_discover_skills",
            "invincat_cli.app_runtime.startup_handlers",
            "discover_skills",
            (),
            {},
            None,
        ),
        (
            "_resolve_resume_thread",
            "invincat_cli.app_runtime.server_handlers",
            "resolve_resume_thread",
            (),
            {},
            None,
        ),
        (
            "_start_server_background",
            "invincat_cli.app_runtime.server_handlers",
            "start_server_background",
            (),
            {},
            None,
        ),
        (
            "_prewarm_threads_cache",
            "invincat_cli.app_runtime.startup_handlers",
            "prewarm_threads_cache",
            (),
            {},
            None,
        ),
        (
            "_prewarm_model_caches",
            "invincat_cli.app_runtime.startup_handlers",
            "prewarm_model_caches",
            (),
            {},
            None,
        ),
        (
            "_check_for_updates",
            "invincat_cli.app_runtime.update_handlers",
            "check_for_updates",
            (),
            {},
            None,
        ),
        (
            "_show_whats_new",
            "invincat_cli.app_runtime.update_handlers",
            "show_whats_new",
            (),
            {},
            None,
        ),
        (
            "_handle_update_command",
            "invincat_cli.app_runtime.update_handlers",
            "handle_update_command",
            (),
            {},
            None,
        ),
        (
            "_handle_auto_update_toggle",
            "invincat_cli.app_runtime.update_handlers",
            "handle_auto_update_toggle",
            (),
            {},
            None,
        ),
        (
            "_hydrate_messages_above",
            "invincat_cli.app_runtime.message_flow",
            "hydrate_messages_above",
            (),
            {},
            None,
        ),
        (
            "_mount_before_queued",
            "invincat_cli.app_runtime.message_flow",
            "mount_before_queued",
            ("container", "widget"),
            {},
            None,
        ),
        (
            "_set_spinner",
            "invincat_cli.app_runtime.message_flow",
            "set_spinner",
            ("thinking",),
            {},
            None,
        ),
        (
            "_request_approval",
            "invincat_cli.app_runtime.approval_handlers",
            "request_approval",
            ([{"name": "tool"}], "agent"),
            {"bypass_plan_guard": True, "allow_auto_approve": False},
            "future",
        ),
        (
            "_handle_plan_guard_auto_reject",
            "invincat_cli.app_runtime.approval_handlers",
            "handle_plan_guard_auto_reject",
            (["shell"],),
            {},
            None,
        ),
        (
            "_mount_auto_approval_messages",
            "invincat_cli.app_runtime.approval_handlers",
            "mount_auto_approval_messages",
            (["ls"],),
            {},
            None,
        ),
        (
            "_wait_for_pending_approval_widget",
            "invincat_cli.app_runtime.approval_handlers",
            "wait_for_pending_approval_widget",
            (),
            {},
            None,
        ),
        (
            "_mount_approval_widget",
            "invincat_cli.app_runtime.approval_handlers",
            "mount_approval_widget",
            ("menu", "future"),
            {},
            None,
        ),
        (
            "_deferred_show_approval",
            "invincat_cli.app_runtime.approval_handlers",
            "deferred_show_approval",
            ("placeholder", "menu", "future"),
            {},
            None,
        ),
        (
            "_remove_approval_placeholder",
            "invincat_cli.app_runtime.approval_handlers",
            "remove_approval_placeholder",
            (),
            {"context": "cleanup"},
            None,
        ),
        (
            "_handle_plan_task",
            "invincat_cli.app_runtime.plan_handlers",
            "handle_plan_task",
            (),
            {},
            None,
        ),
        (
            "_exit_plan_mode",
            "invincat_cli.app_runtime.plan_handlers",
            "exit_plan_mode",
            (),
            {},
            None,
        ),
        (
            "_run_planner",
            "invincat_cli.app_runtime.plan_handlers",
            "run_planner",
            ("task",),
            {},
            True,
        ),
        (
            "_ensure_planner_agent",
            "invincat_cli.app_runtime.plan_handlers",
            "ensure_planner_agent",
            (),
            {},
            "planner",
        ),
        (
            "_get_thread_state_values_for_agent",
            "invincat_cli.app_runtime.plan_handlers",
            "get_thread_state_values_for_agent",
            ("agent", "thread"),
            {},
            {"messages": []},
        ),
        (
            "_after_planner_turn",
            "invincat_cli.app_runtime.plan_handlers",
            "after_planner_turn",
            (),
            {},
            None,
        ),
        (
            "_process_planner_todos_approval",
            "invincat_cli.app_runtime.plan_handlers",
            "process_planner_todos_approval",
            ([{"content": "x"}],),
            {},
            True,
        ),
        (
            "_maybe_approve_current_planner_todos",
            "invincat_cli.app_runtime.plan_handlers",
            "maybe_approve_current_planner_todos",
            (),
            {},
            False,
        ),
        (
            "_finalize_planner_approval",
            "invincat_cli.app_runtime.plan_handlers",
            "finalize_planner_approval",
            ([{"content": "x"}],),
            {"planner_state_values": {"todos": []}},
            None,
        ),
        (
            "_execute_plan_handoff",
            "invincat_cli.app_runtime.plan_handlers",
            "execute_plan_handoff",
            ("prompt",),
            {},
            None,
        ),
        (
            "_remove_ask_user_widget",
            "invincat_cli.app_runtime.approval_handlers",
            "remove_ask_user_widget",
            ("widget",),
            {"context": "ask"},
            None,
        ),
        (
            "_request_ask_user",
            "invincat_cli.app_runtime.approval_handlers",
            "request_ask_user",
            ([{"question": "q"}],),
            {},
            "ask-future",
        ),
        (
            "_wait_for_pending_ask_user_widget",
            "invincat_cli.app_runtime.approval_handlers",
            "wait_for_pending_ask_user_widget",
            (),
            {},
            None,
        ),
        (
            "_mount_ask_user_widget",
            "invincat_cli.app_runtime.approval_handlers",
            "mount_ask_user_widget",
            ("menu", "future"),
            {},
            None,
        ),
        (
            "on_ask_user_menu_answered",
            "invincat_cli.app_runtime.approval_handlers",
            "handle_ask_user_menu_answered",
            ("event",),
            {},
            None,
        ),
        (
            "on_ask_user_menu_cancelled",
            "invincat_cli.app_runtime.approval_handlers",
            "handle_ask_user_menu_cancelled",
            ("event",),
            {},
            None,
        ),
        (
            "_request_approve_plan",
            "invincat_cli.app_runtime.approval_handlers",
            "request_approve_plan",
            ([{"content": "todo"}],),
            {},
            {"type": "approved"},
        ),
        (
            "on_approve_widget_approved",
            "invincat_cli.app_runtime.approval_handlers",
            "handle_approve_widget_approved",
            ("event",),
            {},
            None,
        ),
        (
            "on_approve_widget_rejected",
            "invincat_cli.app_runtime.approval_handlers",
            "handle_approve_widget_rejected",
            ("event",),
            {},
            None,
        ),
        (
            "_process_message",
            "invincat_cli.app_runtime.input_handlers",
            "process_message",
            ("hello", "normal"),
            {},
            None,
        ),
        (
            "on_chat_input_submitted",
            "invincat_cli.app_runtime.input_handlers",
            "handle_chat_input_submitted",
            ("event",),
            {},
            None,
        ),
        (
            "_handle_shell_command",
            "invincat_cli.app_runtime.shell_handlers",
            "handle_shell_command",
            ("ls",),
            {},
            None,
        ),
        (
            "_run_interactive_shell_task",
            "invincat_cli.app_runtime.shell_handlers",
            "run_interactive_shell_task",
            ("vim",),
            {},
            None,
        ),
        (
            "_run_shell_task",
            "invincat_cli.app_runtime.shell_handlers",
            "run_shell_task",
            ("ls",),
            {},
            None,
        ),
        (
            "_cleanup_shell_task",
            "invincat_cli.app_runtime.shell_handlers",
            "cleanup_shell_task",
            (),
            {},
            None,
        ),
        (
            "_kill_shell_process",
            "invincat_cli.app_runtime.shell_handlers",
            "kill_shell_process",
            (),
            {},
            None,
        ),
        (
            "_open_url_command",
            "invincat_cli.app_runtime.command_handlers",
            "handle_url_command",
            ("/url", "url"),
            {},
            None,
        ),
        (
            "_handle_trace_command",
            "invincat_cli.app_runtime.command_handlers",
            "handle_trace_command",
            ("/trace",),
            {},
            None,
        ),
        (
            "_handle_command",
            "invincat_cli.app_runtime.command_handlers",
            "handle_app_command",
            ("/help",),
            {},
            None,
        ),
        (
            "_scheduler_tick",
            "invincat_cli.app_runtime.scheduled_delivery",
            "scheduler_tick",
            (),
            {},
            None,
        ),
        (
            "_handle_scheduled_timeout",
            "invincat_cli.app_runtime.scheduled_delivery",
            "handle_scheduled_timeout",
            ("run", "task"),
            {},
            None,
        ),
        (
            "_handle_schedule_tool_payload",
            "invincat_cli.app_runtime.schedule_handlers",
            "handle_schedule_tool_payload",
            ({"type": "created"},),
            {},
            None,
        ),
        (
            "_show_schedule_manager",
            "invincat_cli.app_runtime.schedule_handlers",
            "show_schedule_manager",
            (),
            {},
            None,
        ),
        (
            "_execute_schedule_action",
            "invincat_cli.app_runtime.schedule_handlers",
            "execute_schedule_action",
            ("action",),
            {},
            None,
        ),
        (
            "_handle_wecombot_command",
            "invincat_cli.app_runtime.wecom_handlers",
            "handle_wecombot_command",
            ("/wecombot-start",),
            {"action": "start"},
            None,
        ),
        (
            "_run_wecombot_bridge",
            "invincat_cli.app_runtime.wecom_handlers",
            "run_wecombot_bridge",
            (),
            {},
            None,
        ),
        (
            "_wecom_handle_inbound_message",
            "invincat_cli.app_runtime.wecom_handlers",
            "wecom_handle_inbound_message",
            (),
            {"frame": {"type": "text"}},
            None,
        ),
        (
            "_wecom_flush_outbox",
            "invincat_cli.app_runtime.wecom_handlers",
            "wecom_flush_outbox",
            (),
            {},
            True,
        ),
        (
            "_wecom_send_request",
            "invincat_cli.app_runtime.wecom_handlers",
            "wecom_send_request",
            ({"op": "status"},),
            {"timeout": 1.0},
            {"ok": True},
        ),
        (
            "_handle_skill_command",
            "invincat_cli.app_runtime.skill_handlers",
            "handle_skill_command",
            ("/skill:test",),
            {},
            None,
        ),
        (
            "_get_conversation_token_count",
            "invincat_cli.app_runtime.memory_handlers",
            "get_conversation_token_count",
            (),
            {},
            123,
        ),
        (
            "_maybe_auto_offload",
            "invincat_cli.app_runtime.memory_handlers",
            "maybe_auto_offload",
            (),
            {},
            None,
        ),
        (
            "_maybe_notify_memory_update",
            "invincat_cli.app_runtime.memory_handlers",
            "maybe_notify_memory_update",
            (),
            {},
            None,
        ),
        (
            "_handle_offload",
            "invincat_cli.app_runtime.memory_handlers",
            "handle_offload",
            (),
            {},
            None,
        ),
        (
            "_send_to_agent",
            "invincat_cli.app_runtime.agent_handlers",
            "send_to_agent",
            ("message",),
            {"message_kwargs": {"x": 1}, "thread_id_override": "thread"},
            True,
        ),
        (
            "_run_agent_task",
            "invincat_cli.app_runtime.agent_handlers",
            "run_agent_task",
            ("request",),
            {},
            None,
        ),
        (
            "_handle_agent_task_exception",
            "invincat_cli.app_runtime.agent_handlers",
            "handle_agent_task_exception",
            (RuntimeError("boom"),),
            {},
            True,
        ),
        (
            "_process_next_from_queue",
            "invincat_cli.app_runtime.queue_handlers",
            "process_next_from_queue",
            (),
            {},
            None,
        ),
        (
            "_cleanup_agent_task",
            "invincat_cli.app_runtime.agent_handlers",
            "cleanup_agent_task",
            (),
            {"generation": 2},
            None,
        ),
        (
            "_run_post_agent_cleanup_side_effects",
            "invincat_cli.app_runtime.agent_handlers",
            "run_post_agent_cleanup_side_effects",
            (),
            {},
            None,
        ),
        (
            "_mount_message",
            "invincat_cli.app_runtime.message_flow",
            "mount_message",
            ("widget",),
            {},
            None,
        ),
        (
            "_prune_old_messages",
            "invincat_cli.app_runtime.message_flow",
            "prune_old_messages",
            (),
            {},
            None,
        ),
        (
            "_clear_messages",
            "invincat_cli.app_runtime.message_flow",
            "clear_messages",
            (),
            {},
            None,
        ),
        (
            "_maybe_drain_deferred",
            "invincat_cli.app_runtime.deferred_handlers",
            "maybe_drain_deferred",
            (),
            {},
            None,
        ),
        (
            "_drain_deferred_actions",
            "invincat_cli.app_runtime.deferred_handlers",
            "drain_deferred_actions",
            (),
            {},
            None,
        ),
        (
            "action_open_editor",
            "invincat_cli.app_runtime.action_handlers",
            "open_editor",
            (),
            {},
            None,
        ),
        (
            "_show_model_selector",
            "invincat_cli.app_runtime.model_handlers",
            "show_model_selector",
            (),
            {"target": "memory", "extra_kwargs": {"temperature": 0}},
            None,
        ),
        (
            "_show_theme_selector",
            "invincat_cli.app_runtime.ui_handlers",
            "show_theme_selector",
            (),
            {},
            None,
        ),
        (
            "_show_language_selector",
            "invincat_cli.app_runtime.ui_handlers",
            "show_language_selector",
            (),
            {},
            None,
        ),
        (
            "_show_mcp_viewer",
            "invincat_cli.app_runtime.ui_handlers",
            "show_mcp_viewer",
            (),
            {},
            None,
        ),
        (
            "_show_memory_viewer",
            "invincat_cli.app_runtime.ui_handlers",
            "show_memory_viewer",
            (),
            {},
            None,
        ),
        (
            "_show_thread_selector",
            "invincat_cli.app_runtime.ui_handlers",
            "show_thread_selector",
            (),
            {},
            None,
        ),
        (
            "_reset_thread_conversation_view",
            "invincat_cli.app_runtime.thread_handlers",
            "reset_thread_conversation_view",
            (),
            {},
            None,
        ),
        (
            "_restore_previous_thread_after_failed_switch",
            "invincat_cli.app_runtime.thread_handlers",
            "restore_previous_thread_after_failed_switch",
            (),
            {"snapshot": "snap", "failed_thread_id": "thread"},
            True,
        ),
        (
            "_apply_primary_model_switch",
            "invincat_cli.app_runtime.model_handlers",
            "apply_primary_model_switch",
            (),
            {
                "resolved": "resolved",
                "model_result": "model",
                "target_kwargs": None,
                "remote_agent": None,
                "save_recent_model": lambda _value: True,
            },
            None,
        ),
        (
            "_apply_memory_model_switch",
            "invincat_cli.app_runtime.model_handlers",
            "apply_memory_model_switch",
            (),
            {"resolved": "resolved", "model_result": "model", "target_kwargs": None},
            None,
        ),
        (
            "_resume_thread",
            "invincat_cli.app_runtime.thread_handlers",
            "resume_thread",
            ("thread",),
            {},
            None,
        ),
        (
            "_switch_model",
            "invincat_cli.app_runtime.model_handlers",
            "switch_model",
            ("openai:gpt",),
            {"target": "memory", "extra_kwargs": {}, "persist_as_default": True},
            None,
        ),
        (
            "_set_default_model",
            "invincat_cli.app_runtime.model_handlers",
            "set_default_model",
            ("openai:gpt",),
            {"target": "memory", "announce": False, "apply_to_session": True},
            True,
        ),
        (
            "_clear_default_model",
            "invincat_cli.app_runtime.model_handlers",
            "clear_default_model",
            (),
            {"target": "memory"},
            None,
        ),
    ],
)
def test_async_app_methods_delegate_to_runtime_modules(
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    module_name: str,
    function_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    result: Any,
) -> None:
    app = _app()
    calls = _patch_async(monkeypatch, module_name, function_name, result=result)

    actual = asyncio.run(getattr(app, method_name)(*args, **kwargs))

    if result is not None:
        assert actual == result
    expected_args = (app, *args)
    if method_name in {"_prewarm_threads_cache", "_get_thread_state_values_for_agent"}:
        expected_args = args
    elif method_name == "_remove_ask_user_widget":
        expected_args = args
    elif method_name in {
        "on_ask_user_menu_answered",
        "on_ask_user_menu_cancelled",
        "on_approve_widget_approved",
        "on_approve_widget_rejected",
    }:
        expected_args = (app,)
    assert len(calls) == 1
    actual_args, actual_kwargs = calls[0]
    assert actual_args == expected_args
    for key, value in kwargs.items():
        assert actual_kwargs[key] == value


def test_compose_delegates_layout_generator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    calls = _patch_sync(
        monkeypatch,
        "invincat_cli.app_runtime.layout",
        "compose_layout",
        result=iter(["one", "two"]),
    )

    assert list(app.compose()) == ["one", "two"]
    assert calls == [((app,), {})]


def test_static_prewarm_delegates_to_startup_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_sync(
        monkeypatch,
        "invincat_cli.app_runtime.startup_handlers",
        "prewarm_deferred_imports",
    )

    DeepAgentsApp._prewarm_deferred_imports()

    assert calls == [((), {})]


def test_init_session_state_sets_state_and_reports_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    app._auto_approve = True
    app._lc_thread_id = "thread-1"
    notifications: list[dict[str, Any]] = []
    app.notify = lambda message, **kwargs: notifications.append(  # type: ignore[method-assign]
        {"message": message, **kwargs}
    )
    monkeypatch.setattr(
        app_module,
        "create_startup_session_state",
        lambda **kwargs: {"state": kwargs},
    )

    asyncio.run(app._init_session_state())

    assert app._session_state == {
        "state": {"auto_approve": True, "thread_id": "thread-1"}
    }

    monkeypatch.setattr(
        app_module,
        "create_startup_session_state",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    asyncio.run(app._init_session_state())

    assert notifications[-1]["severity"] == "error"


def test_status_token_and_message_store_helpers() -> None:
    app = _app()
    status_calls: list[Any] = []
    app._status_bar = SimpleNamespace(
        set_status_message=lambda message: status_calls.append(("status", message)),
        set_tokens=lambda count, **kwargs: status_calls.append(
            ("tokens", count, kwargs)
        ),
        hide_tokens=lambda: status_calls.append(("hide",)),
    )
    app._context_tokens = 42
    app._tokens_approximate = False
    store_calls: list[Any] = []
    app._message_store = SimpleNamespace(
        set_active_message=lambda message_id: store_calls.append(
            ("active", message_id)
        ),
        update_message=lambda message_id, **kwargs: store_calls.append(
            ("update", message_id, kwargs)
        ),
    )

    app._update_status("ready")
    app._update_tokens(5, approximate=True)
    app._on_tokens_update(10)
    app._show_tokens(approximate=True)
    app._hide_tokens()
    app._set_active_message("msg-1")
    app._sync_message_content("msg-1", "final")

    assert status_calls == [
        ("status", "ready"),
        ("tokens", 5, {"approximate": True}),
        ("tokens", 10, {"approximate": False}),
        ("tokens", 10, {"approximate": True}),
        ("hide",),
    ]
    assert store_calls == [
        ("active", "msg-1"),
        ("update", "msg-1", {"content": "final", "is_streaming": False}),
    ]


def test_scroll_helpers_reanchor_only_near_bottom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    events: list[str] = []
    chat = SimpleNamespace(
        is_anchored=False,
        max_scroll_y=10,
        scroll_y=9,
        anchor=lambda: events.append("anchor"),
    )
    app.query_one = lambda *_args: chat  # type: ignore[method-assign]

    app._maybe_reanchor()

    assert events == ["anchor"]

    app.query_one = lambda *_args: (_ for _ in ()).throw(app_module.NoMatches())  # type: ignore[method-assign]
    app._maybe_reanchor()

    calls = _patch_sync(
        monkeypatch,
        "invincat_cli.app_runtime.message_flow",
        "check_hydration_needed",
    )
    app.on_scroll_up("event")
    app.on_mouse_scroll_up("event")
    app.on_scroll_to("event")

    assert calls == [((app,), {}), ((app,), {}), ((app,), {})]


def test_local_input_focus_approval_paste_and_cancel_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    approval_calls: list[str] = []
    app._pending_approval_widget = SimpleNamespace(
        action_move_up=lambda: approval_calls.append("up"),
        action_move_down=lambda: approval_calls.append("down"),
        action_select=lambda: approval_calls.append("select"),
        action_select_approve=lambda: approval_calls.append("yes"),
        action_select_auto=lambda: approval_calls.append("auto"),
        action_select_reject=lambda: approval_calls.append("no"),
    )
    app._is_input_focused = lambda: False  # type: ignore[method-assign]

    app.action_approval_up()
    app.action_approval_down()
    app.action_approval_select()
    app.action_approval_yes()
    app.action_approval_auto()
    app.action_approval_no()
    app.action_approval_escape()

    assert approval_calls == ["up", "down", "select", "yes", "auto", "no", "no"]
    delattr(app, "_is_input_focused")

    child = SimpleNamespace(id="child")
    focus_holder = SimpleNamespace(value=child)
    monkeypatch.setattr(
        DeepAgentsApp,
        "focused",
        property(lambda _self: focus_holder.value),
    )
    app._chat_input = SimpleNamespace(
        walk_children=lambda: [child],
        handle_external_paste=lambda text: text == "paste",
        focus_input=lambda: approval_calls.append("focus"),
    )
    assert app._is_input_focused() is True
    focus_holder.value = SimpleNamespace(id="other")
    assert app._is_input_focused() is False

    prevented: list[str] = []
    event = SimpleNamespace(
        text="paste",
        prevent_default=lambda: prevented.append("prevent"),
        stop=lambda: prevented.append("stop"),
    )
    app._pending_approval_widget = None
    app.on_paste(event)
    assert prevented == ["prevent", "stop"]

    refresh_calls: list[Any] = []
    app.call_after_refresh = lambda callback: refresh_calls.append(callback)  # type: ignore[method-assign]
    app.on_click("event")
    assert refresh_calls == [app._chat_input.focus_input]

    worker_calls: list[str] = []
    app._discard_queue = lambda: worker_calls.append("discard")  # type: ignore[method-assign]
    app._agent_running = True
    app._agent_worker = object()
    app._active_turn_is_planner = True
    worker = SimpleNamespace(cancel=lambda: worker_calls.append("cancel"))
    app._cancel_worker(worker)
    assert worker_calls == ["discard", "cancel"]
    assert app._agent_running is False
    assert app._agent_worker is None
    assert app._active_turn_is_planner is False


def test_remaining_local_app_state_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    app._planner_agent = object()
    app._planner_last_todos_fingerprint = "last"
    app._planner_prompted_todos_fingerprint = "prompted"

    app._invalidate_planner_agent_cache()

    assert app._planner_agent is None
    assert app._planner_last_todos_fingerprint is None
    assert app._planner_prompted_todos_fingerprint is None

    status_calls: list[Any] = []
    app._status_bar = SimpleNamespace(
        set_mode=lambda mode: status_calls.append(("mode", mode))
    )
    app.on_chat_input_mode_changed(SimpleNamespace(mode="shell"))
    assert status_calls == [("mode", "shell")]

    monkeypatch.setattr(app_module, "_monotonic", lambda: 100.0)
    app.on_chat_input_typing(SimpleNamespace())
    assert app._last_typed_at == 100.0
    assert app._is_user_typing() is True

    shown: list[str] = []

    async def fake_show_schedule_manager() -> None:
        shown.append("schedule")

    app._show_schedule_manager = fake_show_schedule_manager  # type: ignore[method-assign]
    asyncio.run(app._handle_schedule_command("/schedule"))
    assert shown == ["schedule"]


def test_scheduler_drain_and_quit_pending_helpers() -> None:
    app = _app()
    drained: list[str] = []

    async def fake_drain_pending_now() -> None:
        drained.append("drain")

    app._scheduler_runner = SimpleNamespace(drain_pending_now=fake_drain_pending_now)
    app._agent_running = False
    app._shell_running = False

    asyncio.run(app._drain_scheduler_if_idle())

    app._agent_running = True
    asyncio.run(app._drain_scheduler_if_idle())

    assert drained == ["drain"]

    notifications: list[dict[str, Any]] = []
    timers: list[tuple[int, Any]] = []
    app.notify = lambda message, **kwargs: notifications.append(  # type: ignore[method-assign]
        {"message": message, **kwargs}
    )
    app.set_timer = lambda timeout, callback: timers.append((timeout, callback))  # type: ignore[method-assign]

    app._arm_quit_pending("Ctrl+C")

    assert app._quit_pending is True
    assert notifications[-1]["markup"] is False
    assert timers[0][0] == 3
    timers[0][1]()
    assert app._quit_pending is False


def test_approval_cleanup_and_focus_edge_cases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    events: list[str] = []

    async def fake_remove_placeholder(*, context: str) -> None:
        events.append(context)

    class PendingApproval:
        async def remove(self) -> None:
            events.append("removed")

    app._remove_approval_placeholder = fake_remove_placeholder  # type: ignore[method-assign]
    app._pending_approval_widget = PendingApproval()
    app._chat_input = SimpleNamespace(focus_input=lambda: events.append("focus"))
    app.call_after_refresh = lambda callback: callback()  # type: ignore[method-assign]

    asyncio.run(app.on_approval_menu_decided(SimpleNamespace()))

    assert events == ["approval cleanup", "removed", "focus"]
    assert app._pending_approval_widget is None

    focus_holder = SimpleNamespace(value=None)
    monkeypatch.setattr(
        DeepAgentsApp,
        "focused",
        property(lambda _self: focus_holder.value),
    )
    app._chat_input = None
    assert app._is_input_focused() is False
    app._chat_input = SimpleNamespace(walk_children=lambda: [])
    assert app._is_input_focused() is False
    focus_holder.value = SimpleNamespace(id="chat-input")
    assert app._is_input_focused() is True


def test_paste_focus_click_and_mouse_helpers_cover_early_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    events: list[str] = []
    monkeypatch.setattr(DeepAgentsApp, "screen", property(lambda _self: object()))
    app._chat_input = None
    event = SimpleNamespace(
        text="paste",
        prevent_default=lambda: events.append("prevent"),
        stop=lambda: events.append("stop"),
    )
    app.on_paste(event)
    app.on_app_focus()
    app.on_click(SimpleNamespace())

    app._chat_input = SimpleNamespace(
        handle_external_paste=lambda _text: False,
        focus_input=lambda: events.append("focus"),
    )
    app._pending_approval_widget = object()
    app.on_paste(event)
    app.on_app_focus()
    app.on_click(SimpleNamespace())
    app._pending_approval_widget = None
    app._pending_ask_user_widget = None

    class FakeModalScreen:
        pass

    monkeypatch.setattr(app_module, "ModalScreen", FakeModalScreen)
    monkeypatch.setattr(
        DeepAgentsApp, "screen", property(lambda _self: FakeModalScreen())
    )
    app.on_app_focus()

    monkeypatch.setattr(DeepAgentsApp, "screen", property(lambda _self: object()))
    app.on_app_focus()
    app.call_after_refresh = lambda callback: callback()  # type: ignore[method-assign]
    app.on_click(SimpleNamespace())

    app._chat_input = SimpleNamespace(
        handle_external_paste=lambda _text: True,
        focus_input=lambda: events.append("focus"),
    )
    monkeypatch.setattr(
        DeepAgentsApp,
        "_is_input_focused",
        lambda _self: False,
    )
    app.on_paste(event)

    calls = _patch_sync(
        monkeypatch,
        "invincat_cli.io.clipboard",
        "copy_selection_to_clipboard",
    )
    app.on_mouse_up(SimpleNamespace())

    assert events == ["focus", "focus", "prevent", "stop"]
    assert calls == [((app,), {})]


def test_run_textual_app_delegates_to_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_async(
        monkeypatch,
        "invincat_cli.app_runtime.runner",
        "run_textual_app",
        result="result",
    )

    result = asyncio.run(
        app_module.run_textual_app(
            agent="agent",
            assistant_id="assistant",
            auto_approve=True,
            cwd="/tmp",
            thread_id="thread",
            server_kwargs={"x": 1},
            defer_server_start=True,
        )
    )

    assert result == "result"
    assert calls[0][1]["app_cls"] is DeepAgentsApp
    assert calls[0][1]["result_cls"] is app_module.AppResult
    assert calls[0][1]["app_kwargs"]["agent"] == "agent"
    assert calls[0][1]["app_kwargs"]["assistant_id"] == "assistant"
    assert calls[0][1]["app_kwargs"]["auto_approve"] is True
    assert calls[0][1]["app_kwargs"]["server_kwargs"] == {"x": 1}
    assert calls[0][1]["app_kwargs"]["defer_server_start"] is True


def test_exit_prepares_runtime_before_super_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = _app()
    events: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def fake_prepare_exit(
        app_arg: DeepAgentsApp,
        *,
        restore_cursor_guide: Any,
    ) -> None:
        events.append(("prepare", (app_arg,), {"restore": restore_cursor_guide}))

    def fake_super_exit(self: DeepAgentsApp, *args: Any, **kwargs: Any) -> None:
        events.append(("super", (self, *args), kwargs))

    monkeypatch.setattr(
        "invincat_cli.app_runtime.exit_handlers.prepare_exit", fake_prepare_exit
    )
    monkeypatch.setattr(app_module.App, "exit", fake_super_exit)

    app.exit(result="done", return_code=3, message="bye")

    assert events[0][0] == "prepare"
    assert events[0][1] == (app,)
    assert events[1] == (
        "super",
        (app,),
        {"result": "done", "return_code": 3, "message": "bye"},
    )
