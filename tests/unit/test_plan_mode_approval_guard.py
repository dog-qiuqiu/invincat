from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from invincat_cli.app import DeepAgentsApp, DeferredAction


class _DummyMessages:
    async def mount(self, *widgets):  # noqa: ANN002, ANN003
        return None


def test_plan_mode_rejects_non_plan_interrupt_tools() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=True, auto_approve=False)
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


def test_after_planner_turn_skips_manual_approval_when_approve_plan_was_used() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._planner_agent = object()
    app._planner_thread_id = "planner-thread"
    approvals: list[list[dict[str, str]]] = []

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

    app._get_thread_state_values_for_agent = _fake_get_state  # type: ignore[method-assign]
    app._process_planner_todos_approval = _fake_process_todos  # type: ignore[method-assign]

    async def _run() -> None:
        await app._after_planner_turn()
        assert approvals == []

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

    async def _fake_ensure_planner():
        return object()

    async def _fake_send_to_agent(_message, **_kwargs):  # noqa: ANN001
        fingerprint_at_send.append(app._planner_last_todos_fingerprint)

    app._ensure_planner_agent = _fake_ensure_planner  # type: ignore[method-assign]
    app._send_to_agent = _fake_send_to_agent  # type: ignore[method-assign]

    async def _run() -> None:
        await app._run_planner("生成执行计划")
        assert fingerprint_at_send == [None]

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


def test_request_approve_plan_in_plan_mode_queues_handoff() -> None:
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
    assert queued, "expected plan approval to queue handoff"
    assert captured_kwargs.get("allow_auto_approve") is False


def test_handle_plan_task_does_not_reset_when_already_in_plan_mode() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=True, thread_id="main-thread")
    app._planner_thread_id = "planner-thread-existing"

    mounted: list[str] = []

    async def _fake_mount_message(widget):  # noqa: ANN001
        mounted.append(str(getattr(widget, "_content", "")))

    app._mount_message = _fake_mount_message  # type: ignore[method-assign]

    async def _run() -> None:
        await app._handle_plan_task("")

    asyncio.run(_run())
    assert app._planner_thread_id == "planner-thread-existing"
    assert any("already ON" in msg or "已开启" in msg for msg in mounted)


def test_handle_plan_task_with_task_keeps_existing_planner_thread() -> None:
    app = DeepAgentsApp(agent=None, assistant_id="agent", backend=None)
    app._session_state = SimpleNamespace(plan_mode=True, thread_id="planner-thread")
    app._planner_thread_id = "planner-thread-existing"
    called: list[str] = []

    async def _fake_run_planner(task: str) -> None:
        called.append(task)

    app._run_planner = _fake_run_planner  # type: ignore[method-assign]

    async def _run() -> None:
        await app._handle_plan_task("继续细化")

    asyncio.run(_run())
    assert app._planner_thread_id == "planner-thread-existing"
    assert called == ["继续细化"]


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
