from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from invincat_cli.app import DeepAgentsApp, DeferredAction
from invincat_cli.widgets.messages import UserMessage


class _DummyMessages:
    async def mount(self, *widgets):  # noqa: ANN002, ANN003
        return None


def test_plan_mode_rejects_non_plan_interrupt_tools() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=True, auto_approve=False)
    app._active_turn_is_planner = True
    app.query_one = lambda *_args, **_kwargs: _DummyMessages()  # type: ignore[method-assign]
    app._mount_before_queued = lambda *_args, **_kwargs: asyncio.sleep(0)  # type: ignore[method-assign]

    async def _run() -> None:
        fut = await app._request_approval(
            [{"name": "write_file", "args": {"file_path": "index.html"}}],
            assistant_id="agent",
        )
        result = await fut
        assert result["type"] == "reject"

    asyncio.run(_run())


def test_plan_mode_reject_notice_lists_only_disallowed_tools() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=True, auto_approve=False)
    app._active_turn_is_planner = True
    app.query_one = lambda *_args, **_kwargs: _DummyMessages()  # type: ignore[method-assign]
    captured_widgets: list[object] = []

    async def _mount_before_queued(*_args, **_kwargs):  # noqa: ANN002, ANN003
        if len(_args) >= 2:
            captured_widgets.append(_args[1])

    app._mount_before_queued = _mount_before_queued  # type: ignore[method-assign]

    async def _run() -> None:
        fut = await app._request_approval(
            [
                {"name": "web_search", "args": {"query": "plan examples"}},
                {"name": "write_file", "args": {"file_path": "index.html"}},
            ],
            assistant_id="agent",
        )
        result = await fut
        assert result["type"] == "reject"
        assert captured_widgets, "expected reject notice to be mounted"
        message_text = str(getattr(captured_widgets[-1], "_content", ""))
        assert "write_file" in message_text
        assert "web_search" not in message_text

    asyncio.run(_run())


def test_after_planner_turn_ignores_stale_todos_without_write_todos() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._planner_agent = object()
    app._planner_thread_id = "planner-thread"
    approvals: list[list[dict[str, str]]] = []

    async def _fake_get_state(_agent, _thread_id):  # noqa: ANN001
        return {
            "messages": [
                HumanMessage(content="先讨论方案"),
                AIMessage(content="我先描述思路，不写清单"),
            ],
            "todos": [{"content": "old todo from earlier turn", "status": "pending"}],
        }

    async def _fake_process_todos(todos: list[dict[str, str]]) -> bool:
        approvals.append(todos)
        return True

    app._get_thread_state_values_for_agent = _fake_get_state  # type: ignore[method-assign]
    app._process_planner_todos_approval = _fake_process_todos  # type: ignore[method-assign]

    async def _run() -> None:
        await app._after_planner_turn()
        assert approvals == []

    asyncio.run(_run())


def test_plan_mode_does_not_reject_non_plan_interrupt_tools_for_main_turn() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=True, auto_approve=False)
    app._active_turn_is_planner = False
    app._is_user_typing = lambda: False  # type: ignore[method-assign]

    async def _fake_mount(menu, result_future):  # noqa: ANN001
        result_future.set_result({"type": "approve"})

    app._mount_approval_widget = _fake_mount  # type: ignore[method-assign]

    async def _run() -> None:
        fut = await app._request_approval(
            [{"name": "write_file", "args": {"file_path": "index.html"}}],
            assistant_id="agent",
        )
        result = await fut
        assert result["type"] == "approve"

    asyncio.run(_run())


def test_after_planner_turn_finalizes_when_approve_plan_approved() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._planner_agent = object()
    app._planner_thread_id = "planner-thread"
    approvals: list[list[dict[str, str]]] = []
    finalized: list[list[dict[str, str]]] = []

    async def _fake_get_state(_agent, _thread_id):  # noqa: ANN001
        return {
            "messages": [
                HumanMessage(content="做计划"),
                ToolMessage("todos recorded", tool_call_id="tc-write", name="write_todos"),
                ToolMessage("approved", tool_call_id="tc-approve", name="approve_plan"),
            ],
            "todos": [{"content": "final todo", "status": "in_progress"}],
        }

    async def _fake_process_todos(todos: list[dict[str, str]]) -> bool:
        approvals.append(todos)
        return True

    async def _fake_finalize(
        todos: list[dict[str, str]],
        *,
        planner_state_values=None,  # noqa: ANN001
    ) -> None:
        finalized.append(todos)

    app._get_thread_state_values_for_agent = _fake_get_state  # type: ignore[method-assign]
    app._process_planner_todos_approval = _fake_process_todos  # type: ignore[method-assign]
    app._finalize_planner_approval = _fake_finalize  # type: ignore[method-assign]

    async def _run() -> None:
        await app._after_planner_turn()
        assert approvals == []
        assert finalized == [[{"content": "final todo", "status": "in_progress"}]]

    asyncio.run(_run())


def test_after_planner_turn_skips_second_prompt_after_rejected_approve_plan() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._planner_agent = object()
    app._planner_thread_id = "planner-thread"
    app._planner_prompted_todos_fingerprint = (
        '[{"content": "final todo", "status": "in_progress"}]'
    )
    approvals: list[list[dict[str, str]]] = []

    async def _fake_get_state(_agent, _thread_id):  # noqa: ANN001
        return {
            "messages": [
                HumanMessage(content="做计划"),
                ToolMessage("todos recorded", tool_call_id="tc-write", name="write_todos"),
            ],
            "todos": [{"content": "final todo", "status": "in_progress"}],
        }

    async def _fake_process_todos(todos: list[dict[str, str]]) -> bool:
        approvals.append(todos)
        return True

    app._get_thread_state_values_for_agent = _fake_get_state  # type: ignore[method-assign]
    app._process_planner_todos_approval = _fake_process_todos  # type: ignore[method-assign]

    async def _run() -> None:
        await app._after_planner_turn()
        assert approvals == []

    asyncio.run(_run())


def test_after_planner_turn_prompts_for_refinement_when_approve_plan_rejected() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._planner_agent = object()
    app._planner_thread_id = "planner-thread"
    mounted: list[str] = []

    async def _fake_get_state(_agent, _thread_id):  # noqa: ANN001
        return {
            "messages": [
                HumanMessage(content="做计划"),
                ToolMessage("todos recorded", tool_call_id="tc-write", name="write_todos"),
                ToolMessage("rejected", tool_call_id="tc-approve", name="approve_plan"),
            ],
            "todos": [{"content": "final todo", "status": "in_progress"}],
        }

    async def _fake_mount_message(widget):  # noqa: ANN001
        mounted.append(str(getattr(widget, "_content", "")))

    app._get_thread_state_values_for_agent = _fake_get_state  # type: ignore[method-assign]
    app._mount_message = _fake_mount_message  # type: ignore[method-assign]

    async def _run() -> None:
        await app._after_planner_turn()

    asyncio.run(_run())
    assert any(
        "Plan not approved" in msg or "计划未通过" in msg for msg in mounted
    )


def test_after_planner_turn_does_not_duplicate_refine_prompt_after_rejected_ai_reply() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._planner_agent = object()
    app._planner_thread_id = "planner-thread"
    mounted: list[str] = []

    async def _fake_get_state(_agent, _thread_id):  # noqa: ANN001
        return {
            "messages": [
                HumanMessage(content="做计划"),
                ToolMessage("todos recorded", tool_call_id="tc-write", name="write_todos"),
                ToolMessage("rejected", tool_call_id="tc-approve", name="approve_plan"),
                AIMessage(content="请告诉我需要调整哪些任务。"),
            ],
            "todos": [{"content": "final todo", "status": "in_progress"}],
        }

    async def _fake_mount_message(widget):  # noqa: ANN001
        mounted.append(str(getattr(widget, "_content", "")))

    app._get_thread_state_values_for_agent = _fake_get_state  # type: ignore[method-assign]
    app._mount_message = _fake_mount_message  # type: ignore[method-assign]

    async def _run() -> None:
        await app._after_planner_turn()

    asyncio.run(_run())
    assert mounted == []


def test_maybe_approve_current_planner_todos_ignores_stale_state() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._planner_agent = object()
    app._planner_thread_id = "planner-thread"
    approvals: list[list[dict[str, str]]] = []

    async def _fake_get_state(_agent, _thread_id):  # noqa: ANN001
        return {
            "messages": [
                HumanMessage(content="继续优化"),
                AIMessage(content="我先解释，不生成清单"),
            ],
            "todos": [{"content": "stale todo", "status": "pending"}],
        }

    async def _fake_process_todos(todos: list[dict[str, str]]) -> bool:
        approvals.append(todos)
        return True

    app._get_thread_state_values_for_agent = _fake_get_state  # type: ignore[method-assign]
    app._process_planner_todos_approval = _fake_process_todos  # type: ignore[method-assign]

    async def _run() -> None:
        approved = await app._maybe_approve_current_planner_todos()
        assert approved is False
        assert approvals == []

    asyncio.run(_run())


def test_run_planner_resets_todos_fingerprint_each_turn() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._agent = object()
    app._session_state = SimpleNamespace(thread_id="main-thread", plan_mode=True)
    app._planner_thread_id = "planner-thread"
    app._planner_last_todos_fingerprint = "stale-fingerprint"
    fingerprint_at_send: list[str | None] = []
    sent_messages: list[str] = []

    async def _fake_ensure_planner():
        return object()

    async def _fake_send_to_agent(_message, **_kwargs):  # noqa: ANN001
        sent_messages.append(_message)
        fingerprint_at_send.append(app._planner_last_todos_fingerprint)
        return True

    app._ensure_planner_agent = _fake_ensure_planner  # type: ignore[method-assign]
    app._send_to_agent = _fake_send_to_agent  # type: ignore[method-assign]

    async def _run() -> None:
        started = await app._run_planner("生成执行计划")
        assert started is True
        assert fingerprint_at_send == [None]
        assert sent_messages
        assert "[planner_runtime_context]" in sent_messages[0]
        assert "cwd:" in sent_messages[0]
        assert str(app._cwd) in sent_messages[0]
        assert "[user_task]" in sent_messages[0]
        assert "生成执行计划" in sent_messages[0]

    asyncio.run(_run())


def test_switch_model_invalidates_planner_cache(monkeypatch) -> None:
    import invincat_cli.config as config_mod
    import invincat_cli.model_config as model_config_mod

    class _DummyModelResult:
        provider = "anthropic"
        model_name = "claude-test"

        def apply_to_settings(self) -> None:
            return None

    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._planner_agent = object()
    app._remote_agent = lambda: True  # type: ignore[method-assign]
    app._mount_message = lambda *_args, **_kwargs: asyncio.sleep(0)  # type: ignore[method-assign]

    monkeypatch.setattr(config_mod.settings, "model_name", "claude-old", raising=False)
    monkeypatch.setattr(config_mod.settings, "model_provider", "anthropic", raising=False)
    monkeypatch.setattr(config_mod, "detect_provider", lambda _spec: "anthropic")
    monkeypatch.setattr(
        config_mod,
        "create_model",
        lambda *_args, **_kwargs: _DummyModelResult(),
    )
    monkeypatch.setattr(model_config_mod, "clear_caches", lambda: None)
    monkeypatch.setattr(
        model_config_mod,
        "has_provider_credentials",
        lambda _provider: True,
    )
    monkeypatch.setattr(
        model_config_mod,
        "get_credential_env_var",
        lambda _provider: "ANTHROPIC_API_KEY",
    )
    monkeypatch.setattr(model_config_mod, "save_recent_model", lambda _spec: True)

    async def _run() -> None:
        await app._switch_model("anthropic:claude-test")

    asyncio.run(_run())
    assert app._planner_agent is None


def test_ensure_planner_agent_uses_planner_approve_prompt_and_disables_shell(
    monkeypatch,
) -> None:
    from invincat_cli.plan_agent import PLANNER_APPROVE_PLAN_SYSTEM_PROMPT

    app = DeepAgentsApp(agent=SimpleNamespace(checkpointer=None), assistant_id="agent", backend=None)
    captured: dict[str, object] = {}
    planner_runtime = object()

    def _fake_create_cli_agent(*_args, **kwargs):  # noqa: ANN002, ANN003
        captured.update(kwargs)
        return planner_runtime, None

    monkeypatch.setattr("invincat_cli.agent.create_cli_agent", _fake_create_cli_agent)

    async def _run() -> None:
        planner = await app._ensure_planner_agent()
        assert planner is planner_runtime

    asyncio.run(_run())
    assert captured.get("enable_shell") is False
    assert captured.get("approve_plan_system_prompt") == PLANNER_APPROVE_PLAN_SYSTEM_PROMPT
    planner_system_prompt = str(captured.get("system_prompt", ""))
    assert "root_context_dir" in planner_system_prompt
    assert str(app._cwd) in planner_system_prompt
    assert "tools" in captured
    tool_names = {getattr(tool, "name", getattr(tool, "__name__", "")) for tool in captured["tools"]}  # type: ignore[index]
    assert "fetch_url" in tool_names


def test_run_planner_reports_send_failure() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._agent = object()
    app._session_state = SimpleNamespace(thread_id="main-thread", plan_mode=True)
    app._planner_thread_id = "planner-thread"

    async def _fake_ensure_planner():
        return object()

    async def _fake_send_to_agent(_message, **_kwargs):  # noqa: ANN001
        return False

    app._ensure_planner_agent = _fake_ensure_planner  # type: ignore[method-assign]
    app._send_to_agent = _fake_send_to_agent  # type: ignore[method-assign]

    async def _run() -> None:
        started = await app._run_planner("生成执行计划")
        assert started is False

    asyncio.run(_run())


def test_request_approve_plan_in_plan_mode_does_not_queue_handoff() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=True)
    queued: list[object] = []
    captured_kwargs: dict[str, object] = {}

    async def _fake_request_approval(*_args, **_kwargs):  # noqa: ANN002, ANN003
        captured_kwargs.update(_kwargs)
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        fut.set_result({"type": "approve"})
        return fut

    def _fake_defer_action(action):  # noqa: ANN001
        queued.append(action)

    app._request_approval = _fake_request_approval  # type: ignore[method-assign]
    app._defer_action = _fake_defer_action  # type: ignore[method-assign]

    async def _run() -> None:
        fut = await app._request_approve_plan(
            [{"content": "Implement feature", "status": "in_progress"}]
        )
        result = await fut
        assert result == {"type": "approved"}

    asyncio.run(_run())
    assert queued == []
    assert captured_kwargs.get("allow_auto_approve") is False


def test_finalize_planner_approval_queues_plan_handoff_to_main_agent() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=True, thread_id="planner-thread")
    app._main_thread_before_plan = "main-thread"
    app._planner_thread_id = "planner-thread"
    app._planner_last_todos_fingerprint = "fp1"
    app._planner_prompted_todos_fingerprint = "fp2"
    app._mount_message = lambda *_args, **_kwargs: asyncio.sleep(0)  # type: ignore[method-assign]

    captured_user_messages: list[str] = []
    captured_send_prompts: list[str] = []

    async def _fake_mount_message(widget):  # noqa: ANN001
        captured_user_messages.append(str(getattr(widget, "_content", "")))

    async def _fake_send_to_agent(prompt: str, **_kwargs):  # noqa: ANN001
        captured_send_prompts.append(prompt)
        return True

    app._mount_message = _fake_mount_message  # type: ignore[method-assign]
    app._send_to_agent = _fake_send_to_agent  # type: ignore[method-assign]

    todos = [
        {"content": "Implement API endpoint", "status": "in_progress"},
        {"content": "Add tests", "status": "pending"},
    ]

    async def _run() -> None:
        await app._finalize_planner_approval(todos)
        assert app._session_state.plan_mode is False
        assert app._session_state.thread_id == "main-thread"
        assert app._planner_thread_id is None
        assert app._main_thread_before_plan is None
        assert app._planner_last_todos_fingerprint is None
        assert app._planner_prompted_todos_fingerprint is None
        assert app._pending_plan_handoff_prompt is not None
        await app._maybe_drain_deferred()
        assert app._session_state.plan_mode is False
        assert len(captured_send_prompts) == 1
        assert (
            "Execute the following approved plan now." in captured_send_prompts[0]
            or "请立即执行以下已批准计划。" in captured_send_prompts[0]
        )
        assert "1. Implement API endpoint" in captured_send_prompts[0]
        assert "2. Add tests" in captured_send_prompts[0]
        assert captured_user_messages

    asyncio.run(_run())


def test_finalize_planner_approval_handoff_uses_user_context_only() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=True, thread_id="planner-thread")
    app._main_thread_before_plan = "main-thread"
    app._planner_agent = object()
    app._planner_thread_id = "planner-thread"

    async def _fake_get_state(_agent, _thread_id):  # noqa: ANN001
        return {
            "messages": [
                HumanMessage(content="请按性能优先，不要引入新依赖"),
                AIMessage(content="我会先做最小变更并补测试。"),
            ],
        }

    captured_send_prompts: list[str] = []
    captured_mounted_widgets: list[object] = []

    async def _fake_send_to_agent(prompt: str, **_kwargs):  # noqa: ANN001
        captured_send_prompts.append(prompt)
        return True

    async def _fake_mount_message(widget):  # noqa: ANN001
        captured_mounted_widgets.append(widget)

    app._get_thread_state_values_for_agent = _fake_get_state  # type: ignore[method-assign]
    app._mount_message = _fake_mount_message  # type: ignore[method-assign]
    app._send_to_agent = _fake_send_to_agent  # type: ignore[method-assign]

    todos = [{"content": "实现接口", "status": "in_progress"}]

    async def _run() -> None:
        await app._finalize_planner_approval(todos)
        await app._maybe_drain_deferred()

    asyncio.run(_run())
    assert len(captured_send_prompts) == 1
    handoff_prompt = captured_send_prompts[0]
    assert "请立即执行以下已批准计划" in handoff_prompt
    assert "规划阶段关键上下文" in handoff_prompt
    assert "请按性能优先，不要引入新依赖" in handoff_prompt
    assert "我会先做最小变更并补测试" not in handoff_prompt
    assert all(not isinstance(w, UserMessage) for w in captured_mounted_widgets)


def test_maybe_drain_deferred_keeps_pending_handoff_when_execution_fails() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._pending_plan_handoff_prompt = "Execute approved plan"

    async def _fake_execute_plan_handoff(_prompt: str) -> None:
        raise RuntimeError("boom")

    app._execute_plan_handoff = _fake_execute_plan_handoff  # type: ignore[method-assign]

    async def _run() -> None:
        try:
            await app._maybe_drain_deferred()
        except RuntimeError:
            pass

    asyncio.run(_run())
    assert app._pending_plan_handoff_prompt == "Execute approved plan"


def test_execute_plan_handoff_keeps_pending_when_send_fails() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=False, thread_id="main-thread")
    app._pending_plan_handoff_prompt = None
    app._mount_message = lambda *_args, **_kwargs: asyncio.sleep(0)  # type: ignore[method-assign]

    async def _fake_send_to_agent(_prompt: str, **_kwargs):  # noqa: ANN001
        return False

    app._send_to_agent = _fake_send_to_agent  # type: ignore[method-assign]

    async def _run() -> None:
        await app._execute_plan_handoff("Execute approved plan")

    asyncio.run(_run())
    assert app._pending_plan_handoff_prompt == "Execute approved plan"


def test_cancel_worker_does_not_drop_pending_plan_handoff() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._pending_plan_handoff_prompt = "Execute approved plan"

    class _Worker:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    worker = _Worker()
    app._cancel_worker(worker)  # noqa: SLF001
    assert worker.cancelled is True
    assert app._pending_plan_handoff_prompt == "Execute approved plan"


def test_discard_queue_clears_deferred_actions() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)

    async def _noop() -> None:
        return None

    app._pending_messages.append(SimpleNamespace(text="queued", mode="normal"))
    app._queued_widgets.append(SimpleNamespace(remove=lambda: None))
    app._deferred_actions = [
        DeferredAction(kind="chat_output", execute=_noop),
        DeferredAction(kind="plan_handoff", execute=_noop),
    ]

    app._discard_queue()

    assert len(app._pending_messages) == 0
    assert len(app._queued_widgets) == 0
    assert app._deferred_actions == []


def test_handle_plan_task_does_not_reset_when_already_in_plan_mode() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=True, thread_id="main-thread")
    app._planner_thread_id = "planner-thread-existing"

    mounted: list[str] = []

    async def _fake_mount_message(widget):  # noqa: ANN001
        mounted.append(str(getattr(widget, "_content", "")))

    app._mount_message = _fake_mount_message  # type: ignore[method-assign]

    async def _run() -> None:
        await app._handle_plan_task()

    asyncio.run(_run())
    assert app._planner_thread_id == "planner-thread-existing"
    assert any("already ON" in msg or "已开启" in msg for msg in mounted)


def test_handle_plan_task_enters_plan_mode_without_starting_planner() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=False, thread_id="main-thread")
    called: list[str] = []

    class _Status:
        def __init__(self) -> None:
            self.flags: list[bool] = []

        def set_plan_mode(self, *, enabled: bool) -> None:
            self.flags.append(enabled)

    status = _Status()
    app._status_bar = status  # type: ignore[assignment]
    app._mount_message = lambda *_args, **_kwargs: asyncio.sleep(0)  # type: ignore[method-assign]

    async def _fake_run_planner(_task: str) -> bool:
        called.append(_task)
        return False

    app._run_planner = _fake_run_planner  # type: ignore[method-assign]

    async def _run() -> None:
        await app._handle_plan_task()

    asyncio.run(_run())
    assert app._session_state.plan_mode is True
    assert app._session_state.thread_id == "main-thread"
    assert app._planner_thread_id is not None
    assert app._main_thread_before_plan == "main-thread"
    assert called == []
    assert status.flags == [True]


def test_plan_command_with_inline_task_is_not_supported() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=False, thread_id="main-thread")
    called: list[str] = []
    mounted: list[str] = []

    async def _fake_run_planner(task: str) -> bool:
        called.append(task)
        return True

    async def _fake_mount_message(widget):  # noqa: ANN001
        mounted.append(str(getattr(widget, "_content", "")))

    app._run_planner = _fake_run_planner  # type: ignore[method-assign]
    app._mount_message = _fake_mount_message  # type: ignore[method-assign]

    async def _run() -> None:
        await app._handle_command("/plan 生成计划")

    asyncio.run(_run())
    assert called == []
    assert app._session_state.plan_mode is False
    assert any("/plan 生成计划" in msg for msg in mounted)


def test_handle_user_message_rolls_back_plan_mode_when_planner_fails() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=True, thread_id="planner-thread")
    app._main_thread_before_plan = "main-thread"
    app._planner_thread_id = "planner-thread"
    app._planner_last_todos_fingerprint = "fp1"
    app._planner_prompted_todos_fingerprint = "fp2"
    mounted: list[object] = []

    class _Status:
        def __init__(self) -> None:
            self.flags: list[bool] = []

        def set_plan_mode(self, *, enabled: bool) -> None:
            self.flags.append(enabled)

    status = _Status()
    app._status_bar = status  # type: ignore[assignment]

    async def _fake_run_planner(_task: str) -> bool:
        return False

    async def _fake_mount_message(widget):  # noqa: ANN001
        mounted.append(widget)

    app._run_planner = _fake_run_planner  # type: ignore[method-assign]
    app._mount_message = _fake_mount_message  # type: ignore[method-assign]

    async def _run() -> None:
        await app._handle_user_message("生成计划")

    asyncio.run(_run())
    assert app._session_state.plan_mode is False
    assert app._session_state.thread_id == "main-thread"
    assert app._planner_thread_id is None
    assert app._main_thread_before_plan is None
    assert app._planner_last_todos_fingerprint is None
    assert app._planner_prompted_todos_fingerprint is None
    assert status.flags == [False]
    assert any(isinstance(widget, UserMessage) for widget in mounted)


def test_exit_plan_mode_restores_main_thread() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=True, thread_id="planner-thread")
    app._main_thread_before_plan = "main-thread"
    app._planner_last_todos_fingerprint = "fp1"
    app._planner_prompted_todos_fingerprint = "fp2"

    mounted: list[str] = []

    async def _fake_mount_message(widget):  # noqa: ANN001
        mounted.append(str(getattr(widget, "_content", "")))

    app._mount_message = _fake_mount_message  # type: ignore[method-assign]

    async def _run() -> None:
        await app._exit_plan_mode()

    asyncio.run(_run())
    assert app._session_state.plan_mode is False
    assert app._session_state.thread_id == "main-thread"
    assert app._planner_last_todos_fingerprint is None
    assert app._planner_prompted_todos_fingerprint is None
    assert any("Plan mode OFF" in msg or "计划模式已关闭" in msg for msg in mounted)


def test_exit_plan_mode_cancels_running_planner_and_drops_plan_handoff() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=True, thread_id="planner-thread")
    app._main_thread_before_plan = "main-thread"
    app._planner_thread_id = "planner-thread"
    app._agent_running = True
    app._active_turn_is_planner = True

    class _Worker:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    worker = _Worker()
    app._agent_worker = worker  # type: ignore[assignment]
    app._mount_message = lambda *_args, **_kwargs: asyncio.sleep(0)  # type: ignore[method-assign]

    async def _noop() -> None:
        return None

    app._deferred_actions = [
        DeferredAction(kind="plan_handoff", execute=_noop),
        DeferredAction(kind="model_switch", execute=_noop),
    ]

    async def _run() -> None:
        await app._exit_plan_mode()

    asyncio.run(_run())
    assert worker.cancelled is True
    assert app._agent_running is False
    assert app._agent_worker is None
    assert app._active_turn_is_planner is False
    assert app._planner_thread_id is None
    assert app._main_thread_before_plan is None
    assert [a.kind for a in app._deferred_actions] == ["model_switch"]


def test_exit_plan_mode_rejects_pending_approval_widget() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=True, thread_id="planner-thread")
    app._main_thread_before_plan = "main-thread"
    app._planner_thread_id = "planner-thread"
    app._agent_running = True
    app._active_turn_is_planner = True

    class _Worker:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    class _Approval:
        def __init__(self) -> None:
            self.rejected = False

        def action_select_reject(self) -> None:
            self.rejected = True

    worker = _Worker()
    approval = _Approval()
    app._agent_worker = worker  # type: ignore[assignment]
    app._pending_approval_widget = approval  # type: ignore[assignment]
    app._mount_message = lambda *_args, **_kwargs: asyncio.sleep(0)  # type: ignore[method-assign]

    async def _run() -> None:
        await app._exit_plan_mode()

    asyncio.run(_run())
    assert worker.cancelled is True
    assert approval.rejected is True
    assert app._pending_approval_widget is None
    assert app._session_state.plan_mode is False


def test_help_lists_plan_and_exit_plan_commands() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    mounted: list[str] = []

    async def _fake_mount_message(widget):  # noqa: ANN001
        mounted.append(str(getattr(widget, "_content", "")))

    app._mount_message = _fake_mount_message  # type: ignore[method-assign]

    async def _run() -> None:
        await app._handle_command("/help")

    asyncio.run(_run())
    joined = "\n".join(mounted)
    assert "/plan" in joined
    assert "/exit-plan" in joined
