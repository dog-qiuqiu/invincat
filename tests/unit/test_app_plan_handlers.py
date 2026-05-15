from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from invincat_cli.app_runtime import plan_handlers
from invincat_cli.widgets.messages import AppMessage, UserMessage


class FakeStatusBar:
    def __init__(self) -> None:
        self.plan_mode_states: list[bool] = []

    def set_plan_mode(self, *, enabled: bool) -> None:
        self.plan_mode_states.append(enabled)


class FakeWorker:
    def __init__(self) -> None:
        self.cancelled = 0

    def cancel(self) -> None:
        self.cancelled += 1


class FakeApprovalWidget:
    def __init__(self) -> None:
        self.rejected = 0

    def action_select_reject(self) -> None:
        self.rejected += 1


class FakePlannerAgent:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}

    async def aget_state(self, _config: object) -> object:
        return SimpleNamespace(values=self.values)


class PlanApp:
    def __init__(self) -> None:
        self._session_state: SimpleNamespace | None = SimpleNamespace(
            thread_id="main-thread",
            plan_mode=False,
        )
        self._status_bar: FakeStatusBar | None = FakeStatusBar()
        self._agent: object | None = object()
        self._planner_agent: object | None = FakePlannerAgent()
        self._planner_thread_id: str | None = "planner-thread"
        self._model: object | None = None
        self._model_override: str | None = "model-override"
        self._assistant_id = "assistant"
        self._auto_approve = False
        self._mcp_server_info = None
        self._planner_last_todos_fingerprint: str | None = None
        self._planner_prompted_todos_fingerprint: str | None = None
        self._main_thread_before_plan: str | None = None
        self._pending_plan_handoff_prompt: str | None = None
        self._agent_running = False
        self._agent_worker: FakeWorker | None = None
        self._active_turn_is_planner = False
        self._pending_approval_widget: FakeApprovalWidget | None = None
        self._deferred_actions: list[SimpleNamespace] = []
        self._cwd = "/repo"
        self.messages: list[object] = []
        self.removed_approval_contexts: list[str] = []
        self.sent: list[tuple[str, dict[str, object]]] = []
        self.approval_results: list[dict[str, object]] = [{"type": "approved"}]
        self.finalized: list[tuple[list[dict[str, str]], dict[str, object] | None]] = []
        self.processed: list[list[dict[str, str]]] = []
        self.state_values: dict[str, object] = {}
        self.reset_called = 0

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    async def _ensure_planner_agent(self) -> object | None:
        return self._planner_agent

    async def _send_to_agent(self, prompt: str, **kwargs: object) -> bool:
        self.sent.append((prompt, kwargs))
        return True

    async def _after_planner_turn(self) -> None:
        await plan_handlers.after_planner_turn(self)

    async def _remove_approval_placeholder(self, *, context: str) -> None:
        self.removed_approval_contexts.append(context)

    def _reset_plan_mode_state(self) -> None:
        self.reset_called += 1
        plan_handlers.reset_plan_mode_state(self)

    async def _get_thread_state_values_for_agent(
        self,
        _agent: object,
        _thread_id: str,
    ) -> dict[str, object]:
        return self.state_values

    async def _process_planner_todos_approval(
        self,
        todos: list[dict[str, str]],
    ) -> bool:
        self.processed.append(todos)
        return True

    async def _finalize_planner_approval(
        self,
        todos: list[dict[str, str]],
        *,
        planner_state_values: dict[str, object] | None = None,
    ) -> None:
        self.finalized.append((todos, planner_state_values))

    async def _request_approve_plan(
        self,
        _todos: list[dict[str, str]],
    ) -> asyncio.Future:
        future: asyncio.Future = asyncio.Future()
        future.set_result(self.approval_results.pop(0))
        return future


def message_contents(app: PlanApp) -> list[str]:
    return [str(getattr(message, "_content", "")) for message in app.messages]


def todo(content: str = "Implement") -> dict[str, str]:
    return {"content": content, "status": "pending"}


def write_todos_messages() -> list[object]:
    return [
        HumanMessage(content="plan this"),
        ToolMessage("todos recorded", tool_call_id="write-1", name="write_todos"),
    ]


def test_handle_plan_task_enters_plan_mode_and_rejects_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = PlanApp()
    monkeypatch.setattr("invincat_cli.app_runtime.state.new_thread_id", lambda: "new")

    asyncio.run(plan_handlers.handle_plan_task(app))

    assert app._planner_thread_id == "new"
    assert app._main_thread_before_plan == "main-thread"
    assert app._session_state is not None
    assert app._session_state.plan_mode is True
    assert app._status_bar is not None
    assert app._status_bar.plan_mode_states == [True]
    assert isinstance(app.messages[-2], UserMessage)
    assert isinstance(app.messages[-1], AppMessage)

    asyncio.run(plan_handlers.handle_plan_task(app))

    assert isinstance(app.messages[-1], AppMessage)
    assert (
        "already" in message_contents(app)[-1].lower()
        or "已经" in message_contents(app)[-1]
    )


def test_reset_plan_mode_state_restores_main_thread_and_clears_state() -> None:
    app = PlanApp()
    app._session_state.plan_mode = True
    app._main_thread_before_plan = "main-before"
    app._pending_plan_handoff_prompt = "handoff"

    plan_handlers.reset_plan_mode_state(app)

    assert app._session_state.plan_mode is False
    assert app._session_state.thread_id == "main-before"
    assert app._planner_thread_id is None
    assert app._main_thread_before_plan is None
    assert app._pending_plan_handoff_prompt is None
    assert app._status_bar is not None
    assert app._status_bar.plan_mode_states == [False]


def test_ensure_planner_agent_uses_cache_and_initializes_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = PlanApp()
    cached = app._planner_agent

    assert asyncio.run(plan_handlers.ensure_planner_agent(app)) is cached

    app = PlanApp()
    app._planner_agent = None
    app._agent = SimpleNamespace(checkpointer="checkpointer")
    created: dict[str, object] = {}

    def create_agent(**kwargs: object) -> tuple[str, str]:
        created.update(kwargs)
        return ("planner", "backend")

    monkeypatch.setattr(
        "invincat_cli.config.settings",
        SimpleNamespace(has_tavily=False),
    )
    monkeypatch.setattr("invincat_cli.agent.create_cli_agent", create_agent)
    monkeypatch.setattr(
        "invincat_cli.project_utils.ProjectContext.from_user_cwd",
        lambda _path: "project-context",
    )

    assert asyncio.run(plan_handlers.ensure_planner_agent(app)) == "planner"
    assert app._planner_agent == "planner"
    assert created["model"] == "model-override"
    assert created["assistant_id"] == "assistant-planner"
    assert created["project_context"] == "project-context"
    assert created["checkpointer"] == "checkpointer"
    assert len(created["tools"]) == 1

    app = PlanApp()
    app._planner_agent = None
    app._agent = SimpleNamespace(checkpointer="checkpointer")
    created.clear()
    monkeypatch.setattr(
        "invincat_cli.config.settings",
        SimpleNamespace(has_tavily=True),
    )

    assert asyncio.run(plan_handlers.ensure_planner_agent(app)) == "planner"
    assert len(created["tools"]) == 2


def test_ensure_planner_agent_returns_none_on_initialization_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = PlanApp()
    app._planner_agent = None

    def fail_create(**_kwargs: object) -> tuple[str, str]:
        raise RuntimeError("planner failed")

    monkeypatch.setattr("invincat_cli.agent.create_cli_agent", fail_create)

    assert asyncio.run(plan_handlers.ensure_planner_agent(app)) is None


def test_run_planner_reports_unavailable_paths_and_sends_to_planner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = PlanApp()
    app._agent = None

    assert asyncio.run(plan_handlers.run_planner(app, "build")) is False
    assert isinstance(app.messages[-1], AppMessage)

    app = PlanApp()
    app._planner_agent = None

    assert asyncio.run(plan_handlers.run_planner(app, "build")) is False
    assert isinstance(app.messages[-1], AppMessage)

    app = PlanApp()
    app._planner_thread_id = None
    monkeypatch.setattr("invincat_cli.app_runtime.state.new_thread_id", lambda: "new")

    assert asyncio.run(plan_handlers.run_planner(app, " build ")) is True

    prompt, kwargs = app.sent[0]
    assert "[planner_runtime_context]" in prompt
    assert "build" in prompt
    assert kwargs["agent_override"] is app._planner_agent
    assert kwargs["thread_id_override"] == "new"
    assert kwargs["post_turn_hook"] == app._after_planner_turn


def test_exit_plan_mode_handles_not_on_and_cancels_running_planner() -> None:
    app = PlanApp()

    asyncio.run(plan_handlers.exit_plan_mode(app))

    assert isinstance(app.messages[-1], AppMessage)

    app = PlanApp()
    app._session_state.plan_mode = True
    worker = FakeWorker()
    approval = FakeApprovalWidget()
    app._agent_running = True
    app._agent_worker = worker
    app._active_turn_is_planner = True
    app._pending_approval_widget = approval
    app._deferred_actions = [
        SimpleNamespace(kind="plan_handoff"),
        SimpleNamespace(kind="other"),
    ]

    asyncio.run(plan_handlers.exit_plan_mode(app))

    assert approval.rejected == 1
    assert app.removed_approval_contexts == ["plan exit"]
    assert worker.cancelled == 1
    assert app._agent_running is False
    assert app._agent_worker is None
    assert app._active_turn_is_planner is False
    assert [action.kind for action in app._deferred_actions] == ["other"]
    assert app.reset_called == 1
    assert isinstance(app.messages[-1], AppMessage)


def test_get_thread_state_values_for_agent_returns_values_or_empty() -> None:
    agent = FakePlannerAgent({"x": 1})

    assert asyncio.run(
        plan_handlers.get_thread_state_values_for_agent(agent, "thread")
    ) == {"x": 1}

    agent = FakePlannerAgent({})

    assert (
        asyncio.run(plan_handlers.get_thread_state_values_for_agent(agent, "thread"))
        == {}
    )


def test_after_planner_turn_early_returns_and_reports_no_valid_todos() -> None:
    app = PlanApp()
    app._planner_agent = None

    asyncio.run(plan_handlers.after_planner_turn(app))

    assert app.messages == []

    app = PlanApp()
    app.state_values = {}

    asyncio.run(plan_handlers.after_planner_turn(app))

    assert app.messages == []

    app = PlanApp()
    app.state_values = {"messages": write_todos_messages()}

    asyncio.run(plan_handlers.after_planner_turn(app))

    assert isinstance(app.messages[-1], AppMessage)
    assert app.processed == []

    app = PlanApp()
    app.state_values = {"messages": [HumanMessage(content="plan only")]}

    asyncio.run(plan_handlers.after_planner_turn(app))

    assert app.messages == []


def test_after_planner_turn_processes_todos_and_finalizes_approved_tool() -> None:
    app = PlanApp()
    app.state_values = {
        "messages": write_todos_messages(),
        "todos": [todo("Implement")],
    }

    asyncio.run(plan_handlers.after_planner_turn(app))

    assert app.processed == [[todo("Implement")]]

    app = PlanApp()
    app.state_values = {
        "messages": write_todos_messages(),
        "todos": [todo("Already prompted")],
    }
    app._planner_prompted_todos_fingerprint = plan_handlers.plan_todos_fingerprint(
        [todo("Already prompted")]
    )

    asyncio.run(plan_handlers.after_planner_turn(app))

    assert app.processed == []

    app = PlanApp()
    app.state_values = {
        "messages": [
            HumanMessage(content="plan this"),
            ToolMessage("approved", tool_call_id="approve-1", name="approve_plan"),
        ],
        "todos": [todo("Ship")],
    }

    asyncio.run(plan_handlers.after_planner_turn(app))

    assert app.finalized == [([todo("Ship")], app.state_values)]


def test_after_planner_turn_falls_back_to_ai_text_todos_for_approval() -> None:
    app = PlanApp()
    app.state_values = {
        "messages": [
            HumanMessage(content="plan this"),
            ToolMessage("approved", tool_call_id="approve-1", name="approve_plan"),
            AIMessage(content="1. Fallback approval"),
        ],
    }

    asyncio.run(plan_handlers.after_planner_turn(app))

    assert app.finalized == [
        ([{"content": "Fallback approval", "status": "in_progress"}], app.state_values)
    ]

    app = PlanApp()
    app.state_values = {
        "messages": [
            HumanMessage(content="plan this"),
            ToolMessage("approved", tool_call_id="approve-1", name="approve_plan"),
            AIMessage(content="no todos here"),
        ],
    }

    asyncio.run(plan_handlers.after_planner_turn(app))

    assert isinstance(app.messages[-1], AppMessage)


def test_after_planner_turn_mounts_refine_prompt_for_rejected_approve_plan() -> None:
    app = PlanApp()
    app.state_values = {
        "messages": [
            HumanMessage(content="plan this"),
            ToolMessage("rejected", tool_call_id="approve-1", name="approve_plan"),
        ]
    }

    asyncio.run(plan_handlers.after_planner_turn(app))

    assert isinstance(app.messages[-1], AppMessage)

    app = PlanApp()
    app.state_values = {
        "messages": [
            HumanMessage(content="plan this"),
            ToolMessage("rejected", tool_call_id="approve-1", name="approve_plan"),
            AIMessage(content="I will refine it"),
        ]
    }

    asyncio.run(plan_handlers.after_planner_turn(app))

    assert app.messages == []


def test_process_planner_todos_approval_handles_duplicate_reject_and_approve() -> None:
    app = PlanApp()
    todos = [todo("Implement")]
    app._planner_last_todos_fingerprint = plan_handlers.plan_todos_fingerprint(todos)

    assert (
        asyncio.run(plan_handlers.process_planner_todos_approval(app, todos)) is False
    )

    app = PlanApp()
    app.approval_results = [{"type": "rejected"}]

    assert (
        asyncio.run(plan_handlers.process_planner_todos_approval(app, todos)) is False
    )
    assert isinstance(app.messages[-1], AppMessage)
    assert app.finalized == []

    app = PlanApp()
    app.approval_results = [{"type": "approved"}]

    assert asyncio.run(plan_handlers.process_planner_todos_approval(app, todos)) is True
    assert app.finalized == [(todos, None)]


def test_maybe_approve_current_planner_todos_checks_preconditions() -> None:
    app = PlanApp()
    app._planner_agent = None

    assert asyncio.run(plan_handlers.maybe_approve_current_planner_todos(app)) is False

    app = PlanApp()
    app.state_values = {"messages": [HumanMessage(content="plan")]}

    assert asyncio.run(plan_handlers.maybe_approve_current_planner_todos(app)) is False

    app = PlanApp()
    app.state_values = {"messages": write_todos_messages(), "todos": [todo("Do")]}

    assert asyncio.run(plan_handlers.maybe_approve_current_planner_todos(app)) is True
    assert app.processed == [[todo("Do")]]

    app = PlanApp()
    app.state_values = {
        "messages": write_todos_messages() + [AIMessage(content="1. Fallback current")]
    }

    assert asyncio.run(plan_handlers.maybe_approve_current_planner_todos(app)) is True
    assert app.processed == [[{"content": "Fallback current", "status": "in_progress"}]]

    app = PlanApp()
    app.state_values = {
        "messages": write_todos_messages() + [AIMessage(content="no todos")]
    }

    assert asyncio.run(plan_handlers.maybe_approve_current_planner_todos(app)) is False


def test_finalize_planner_approval_builds_handoff_and_handles_state_fetch_failure() -> (
    None
):
    app = PlanApp()
    app.state_values = {"messages": [HumanMessage(content="original request")]}

    asyncio.run(plan_handlers.finalize_planner_approval(app, [todo("Do")]))

    assert app._pending_plan_handoff_prompt is not None
    assert "Do" in app._pending_plan_handoff_prompt
    assert app.reset_called == 1
    assert isinstance(app.messages[-1], AppMessage)

    class FailingStateApp(PlanApp):
        async def _get_thread_state_values_for_agent(
            self,
            _agent: object,
            _thread_id: str,
        ) -> dict[str, object]:
            raise RuntimeError("state failed")

    app = FailingStateApp()

    asyncio.run(plan_handlers.finalize_planner_approval(app, [todo("Fallback")]))

    assert app._pending_plan_handoff_prompt is not None
    assert "Fallback" in app._pending_plan_handoff_prompt


def test_execute_plan_handoff_disables_plan_and_restores_prompt_on_send_failure() -> (
    None
):
    app = PlanApp()
    app._session_state.plan_mode = True
    app._status_bar = FakeStatusBar()

    async def fail_send(_prompt: str, **_kwargs: object) -> bool:
        return False

    app._send_to_agent = fail_send  # type: ignore[method-assign]

    asyncio.run(plan_handlers.execute_plan_handoff(app, "approved prompt"))

    assert app._session_state.plan_mode is False
    assert app._status_bar.plan_mode_states == [False]
    assert app._pending_plan_handoff_prompt == "approved prompt"
    assert len(app.messages) == 2

    app = PlanApp()
    app._session_state = None

    asyncio.run(plan_handlers.execute_plan_handoff(app, "ignored"))

    assert app.messages == []
