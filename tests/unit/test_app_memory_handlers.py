from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

from langchain_core.messages import HumanMessage

from invincat_cli.app_runtime import memory_handlers
from invincat_cli.offload import (
    OffloadModelError,
    OffloadResult,
    OffloadThresholdNotMet,
)
from invincat_cli.widgets.messages import AppMessage, ErrorMessage


class FakeTimer:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class FakeAgent:
    def __init__(self, values: dict[str, object] | None = None) -> None:
        self.values = values or {}
        self.updated: list[tuple[object, dict[str, object]]] = []

    async def aget_state(self, _config: object) -> object:
        return SimpleNamespace(values=self.values)

    async def aupdate_state(self, config: object, values: dict[str, object]) -> None:
        self.updated.append((config, values))


class FakeRemote:
    def __init__(self) -> None:
        self.ensured: list[object] = []

    async def aensure_thread(self, config: object) -> None:
        self.ensured.append(config)


class MemoryApp:
    def __init__(self) -> None:
        self._agent: object | None = FakeAgent(
            {"messages": [HumanMessage(content="remember this")]}
        )
        self._lc_thread_id = "thread-1"
        self._tokens_approximate = False
        self._auto_offload_cooldown_until = 0.0
        self._context_tokens = 90
        self._memory_status_clear_timer: FakeTimer | None = None
        self._offload_budget_cache: tuple[object, str | None] | None = None
        self._profile_override = None
        self._agent_running = False
        self._backend = object()
        self._remote = FakeRemote()
        self.messages: list[object] = []
        self.statuses: list[str] = []
        self.timers: list[tuple[float, object]] = []
        self.spinners: list[str | None] = []
        self.token_updates: list[int] = []
        self.offload_calls = 0

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    async def _handle_offload(self) -> None:
        self.offload_calls += 1

    def _update_status(self, status: str) -> None:
        self.statuses.append(status)

    def set_timer(self, delay: float, callback: object) -> FakeTimer:
        self.timers.append((delay, callback))
        return FakeTimer()

    def _on_memory_update_done(self, msg: str) -> None:
        memory_handlers.on_memory_update_done(self, msg)

    def _clear_memory_status(self) -> None:
        memory_handlers.clear_memory_status(self)

    async def _set_spinner(self, value: str | None) -> None:
        self.spinners.append(value)

    def _remote_agent(self) -> FakeRemote | None:
        return self._remote

    def _on_tokens_update(self, tokens: int) -> None:
        self.token_updates.append(tokens)


def test_get_conversation_token_count_returns_none_without_agent() -> None:
    app = MemoryApp()
    app._agent = None

    assert asyncio.run(memory_handlers.get_conversation_token_count(app)) is None


def test_get_conversation_token_count_counts_agent_messages() -> None:
    app = MemoryApp()

    count = asyncio.run(memory_handlers.get_conversation_token_count(app))

    assert count is not None
    assert count > 0


def test_get_conversation_token_count_returns_none_for_empty_or_failed_state() -> None:
    app = MemoryApp()
    app._agent = FakeAgent({})

    assert asyncio.run(memory_handlers.get_conversation_token_count(app)) is None

    class FailingAgent:
        async def aget_state(self, _config: object) -> object:
            raise RuntimeError("state failed")

    app._agent = FailingAgent()

    assert asyncio.run(memory_handlers.get_conversation_token_count(app)) is None


def test_get_conversation_token_count_returns_none_for_empty_messages() -> None:
    app = MemoryApp()
    app._agent = FakeAgent({"messages": []})

    assert asyncio.run(memory_handlers.get_conversation_token_count(app)) is None


def test_maybe_auto_offload_mounts_message_and_sets_cooldown(monkeypatch) -> None:
    app = MemoryApp()
    monkeypatch.setattr("invincat_cli.config.settings.model_context_limit", 100)
    monkeypatch.setattr(memory_handlers.time, "monotonic", lambda: 10.0)

    asyncio.run(memory_handlers.maybe_auto_offload(app))

    assert isinstance(app.messages[-1], AppMessage)
    assert app.offload_calls == 1
    assert app._auto_offload_cooldown_until > 10.0


def test_maybe_auto_offload_skips_when_decision_absent(monkeypatch) -> None:
    app = MemoryApp()
    app._tokens_approximate = True
    monkeypatch.setattr("invincat_cli.config.settings.model_context_limit", 100)

    asyncio.run(memory_handlers.maybe_auto_offload(app))

    assert app.messages == []
    assert app.offload_calls == 0


def test_maybe_notify_memory_update_schedules_status_transition(monkeypatch) -> None:
    app = MemoryApp()
    previous_timer = FakeTimer()
    app._memory_status_clear_timer = previous_timer

    async def state_values(_app: MemoryApp, _thread_id: str) -> dict[str, object]:
        return {"_auto_memory_updated_paths": ["/Users/example/memory.json"]}

    monkeypatch.setattr(memory_handlers, "get_thread_state_values", state_values)
    monkeypatch.setattr(
        memory_handlers.Path, "home", lambda: memory_handlers.Path("/Users/example")
    )

    asyncio.run(memory_handlers.maybe_notify_memory_update(app))

    assert app.statuses == ["Updating memory..."]
    assert previous_timer.stopped is True
    assert app.timers[-1][0] == 0.8

    callback = app.timers[-1][1]
    callback()

    assert "memory.json" in app.statuses[-1]


def test_maybe_notify_memory_update_ignores_absent_or_failed_state(monkeypatch) -> None:
    app = MemoryApp()

    async def no_updates(_app: MemoryApp, _thread_id: str) -> dict[str, object]:
        return {}

    monkeypatch.setattr(memory_handlers, "get_thread_state_values", no_updates)

    asyncio.run(memory_handlers.maybe_notify_memory_update(app))

    assert app.statuses == []
    assert app.timers == []

    async def fail_state(_app: MemoryApp, _thread_id: str) -> dict[str, object]:
        raise RuntimeError("state failed")

    monkeypatch.setattr(memory_handlers, "get_thread_state_values", fail_state)

    asyncio.run(memory_handlers.maybe_notify_memory_update(app))

    assert app.statuses == []


def test_memory_status_done_and_clear_replace_timers() -> None:
    app = MemoryApp()
    previous_timer = FakeTimer()
    app._memory_status_clear_timer = previous_timer

    memory_handlers.on_memory_update_done(app, "Updated memory")

    assert app.statuses == ["Updated memory"]
    assert previous_timer.stopped is True
    assert app.timers[-1][0] == 4.0

    memory_handlers.clear_memory_status(app)

    assert app._memory_status_clear_timer is None
    assert app.statuses[-1] == ""


def test_resolve_offload_budget_str_uses_cache_and_computes(monkeypatch) -> None:
    app = MemoryApp()
    app._profile_override = {"profile": "test"}
    model = object()

    monkeypatch.setattr("invincat_cli.config.settings.model_provider", "openai")
    monkeypatch.setattr("invincat_cli.config.settings.model_name", "gpt")
    monkeypatch.setattr("invincat_cli.config.settings.model_context_limit", 100)
    monkeypatch.setattr(
        "invincat_cli.config.create_model",
        lambda _spec, *, profile_overrides: SimpleNamespace(model=model),
    )
    monkeypatch.setattr(
        "deepagents.middleware.summarization.compute_summarization_defaults",
        lambda model_arg: {"keep": 25} if model_arg is model else {"keep": 0},
    )
    monkeypatch.setattr(
        "invincat_cli.offload.format_offload_limit",
        lambda keep, limit: f"{keep}/{limit}",
    )

    assert memory_handlers.resolve_offload_budget_str(app) == "25/100"

    monkeypatch.setattr(
        "invincat_cli.config.create_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("no cache")),
    )

    assert memory_handlers.resolve_offload_budget_str(app) == "25/100"


def test_resolve_offload_budget_str_caches_failure(monkeypatch) -> None:
    app = MemoryApp()
    monkeypatch.setattr("invincat_cli.config.settings.model_provider", "openai")
    monkeypatch.setattr("invincat_cli.config.settings.model_name", "gpt")
    monkeypatch.setattr("invincat_cli.config.settings.model_context_limit", 100)
    monkeypatch.setattr(
        "invincat_cli.config.create_model",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("model failed")),
    )

    assert memory_handlers.resolve_offload_budget_str(app) is None
    assert app._offload_budget_cache is not None
    assert memory_handlers.resolve_offload_budget_str(app) is None


def test_handle_offload_reports_nothing_without_agent_or_thread() -> None:
    app = MemoryApp()
    app._agent = None

    asyncio.run(memory_handlers.handle_offload(app))

    assert isinstance(app.messages[-1], AppMessage)


def test_handle_offload_reports_when_agent_is_running() -> None:
    app = MemoryApp()
    app._agent_running = True

    asyncio.run(memory_handlers.handle_offload(app))

    assert isinstance(app.messages[-1], AppMessage)
    assert app._agent_running is True


def test_handle_offload_reports_state_read_failure(monkeypatch) -> None:
    app = MemoryApp()

    async def fail_state(_app: MemoryApp, _thread_id: str) -> dict[str, object]:
        raise RuntimeError("state failed")

    monkeypatch.setattr(memory_handlers, "get_thread_state_values", fail_state)

    asyncio.run(memory_handlers.handle_offload(app))

    assert isinstance(app.messages[-1], ErrorMessage)
    assert "state failed" in app.messages[-1]._content


def test_handle_offload_reports_empty_state(monkeypatch) -> None:
    app = MemoryApp()

    async def empty_state(_app: MemoryApp, _thread_id: str) -> dict[str, object]:
        return {}

    monkeypatch.setattr(memory_handlers, "get_thread_state_values", empty_state)

    asyncio.run(memory_handlers.handle_offload(app))

    assert isinstance(app.messages[-1], AppMessage)
    assert app._agent_running is False
    assert app.spinners == []


def test_handle_offload_reports_threshold_not_met(monkeypatch) -> None:
    app = MemoryApp()

    async def state_values(_app: MemoryApp, _thread_id: str) -> dict[str, object]:
        return {"messages": [HumanMessage(content="short")]}

    async def dispatch_hook(_name: str, _payload: dict[str, object]) -> None:
        return None

    async def perform_offload(**_kwargs: object) -> OffloadThresholdNotMet:
        return OffloadThresholdNotMet(
            conversation_tokens=10,
            total_context_tokens=20,
            context_limit=100,
            budget_str="50 tokens",
        )

    monkeypatch.setattr(memory_handlers, "get_thread_state_values", state_values)
    monkeypatch.setattr("invincat_cli.hooks.dispatch_hook", dispatch_hook)
    monkeypatch.setattr("invincat_cli.offload.perform_offload", perform_offload)

    asyncio.run(memory_handlers.handle_offload(app))

    assert isinstance(app.messages[-1], AppMessage)
    assert "retention budget" in app.messages[-1]._content
    assert app._agent_running is False
    assert app.spinners[-1] is None


def test_handle_offload_logs_spinner_dismiss_failure(monkeypatch, caplog) -> None:
    app = MemoryApp()

    async def state_values(_app: MemoryApp, _thread_id: str) -> dict[str, object]:
        return {"messages": [HumanMessage(content="short")]}

    async def dispatch_hook(_name: str, _payload: dict[str, object]) -> None:
        return None

    async def perform_offload(**_kwargs: object) -> OffloadThresholdNotMet:
        return OffloadThresholdNotMet(
            conversation_tokens=10,
            total_context_tokens=20,
            context_limit=100,
            budget_str="50 tokens",
        )

    async def fail_on_dismiss(value: str | None) -> None:
        app.spinners.append(value)
        if value is None:
            raise RuntimeError("spinner gone")

    app._set_spinner = fail_on_dismiss  # type: ignore[method-assign]
    caplog.set_level(logging.ERROR, logger=memory_handlers.__name__)
    monkeypatch.setattr(memory_handlers, "get_thread_state_values", state_values)
    monkeypatch.setattr("invincat_cli.hooks.dispatch_hook", dispatch_hook)
    monkeypatch.setattr("invincat_cli.offload.perform_offload", perform_offload)

    asyncio.run(memory_handlers.handle_offload(app))

    assert app.spinners[-1] is None
    assert "Failed to dismiss spinner after offload" in caplog.text


def test_handle_offload_updates_agent_state_and_tokens(monkeypatch) -> None:
    app = MemoryApp()
    agent = FakeAgent()
    app._agent = agent
    persisted: list[int] = []

    async def state_values(_app: MemoryApp, _thread_id: str) -> dict[str, object]:
        return {"messages": [HumanMessage(content="long history")]}

    async def dispatch_hook(_name: str, _payload: dict[str, object]) -> None:
        return None

    async def perform_offload(**_kwargs: object) -> OffloadResult:
        return OffloadResult(
            new_event={"summary": "done"},
            messages_offloaded=4,
            messages_kept=2,
            tokens_before=100,
            tokens_after=40,
            pct_decrease=60,
            offload_warning="backend write skipped",
        )

    async def persist(_agent: object, _config: object, tokens: int) -> None:
        persisted.append(tokens)

    monkeypatch.setattr(memory_handlers, "get_thread_state_values", state_values)
    monkeypatch.setattr("invincat_cli.hooks.dispatch_hook", dispatch_hook)
    monkeypatch.setattr("invincat_cli.offload.perform_offload", perform_offload)
    monkeypatch.setattr("invincat_cli.textual_adapter._persist_context_tokens", persist)

    asyncio.run(memory_handlers.handle_offload(app))

    assert app._remote.ensured
    assert agent.updated[0][1] == {"_summarization_event": {"summary": "done"}}
    assert isinstance(app.messages[-2], ErrorMessage)
    assert isinstance(app.messages[-1], AppMessage)
    assert app.token_updates == [40]
    assert persisted == [40]
    assert app._agent_running is False
    assert app.spinners[0]
    assert app.spinners[-1] is None


def test_handle_offload_converts_serialized_messages_and_prior_event(
    monkeypatch,
) -> None:
    app = MemoryApp()
    agent = FakeAgent()
    app._agent = agent
    persisted: list[int] = []

    async def state_values(_app: MemoryApp, _thread_id: str) -> dict[str, object]:
        return {
            "messages": [{"type": "human", "content": "long history"}],
            "_summarization_event": {
                "summary_message": {"type": "ai", "content": "old summary"}
            },
        }

    async def dispatch_hook(_name: str, _payload: dict[str, object]) -> None:
        return None

    async def perform_offload(**kwargs: object) -> OffloadResult:
        messages = kwargs["messages"]
        prior_event = kwargs["prior_event"]
        assert not isinstance(messages[0], dict)
        assert isinstance(prior_event, dict)
        assert not isinstance(prior_event["summary_message"], dict)
        return OffloadResult(
            new_event={"summary": "done"},
            messages_offloaded=2,
            messages_kept=1,
            tokens_before=80,
            tokens_after=30,
            pct_decrease=62.5,
            offload_warning=None,
        )

    async def persist(_agent: object, _config: object, tokens: int) -> None:
        persisted.append(tokens)

    monkeypatch.setattr(memory_handlers, "get_thread_state_values", state_values)
    monkeypatch.setattr("invincat_cli.hooks.dispatch_hook", dispatch_hook)
    monkeypatch.setattr("invincat_cli.offload.perform_offload", perform_offload)
    monkeypatch.setattr("invincat_cli.textual_adapter._persist_context_tokens", persist)

    asyncio.run(memory_handlers.handle_offload(app))

    assert agent.updated[0][1] == {"_summarization_event": {"summary": "done"}}
    assert persisted == [30]
    assert app.token_updates == [30]


def test_handle_offload_reports_model_error(monkeypatch) -> None:
    app = MemoryApp()

    async def state_values(_app: MemoryApp, _thread_id: str) -> dict[str, object]:
        return {"messages": [HumanMessage(content="long history")]}

    async def dispatch_hook(_name: str, _payload: dict[str, object]) -> None:
        return None

    async def perform_offload(**_kwargs: object) -> OffloadResult:
        raise OffloadModelError("bad model")

    monkeypatch.setattr(memory_handlers, "get_thread_state_values", state_values)
    monkeypatch.setattr("invincat_cli.hooks.dispatch_hook", dispatch_hook)
    monkeypatch.setattr("invincat_cli.offload.perform_offload", perform_offload)

    asyncio.run(memory_handlers.handle_offload(app))

    assert isinstance(app.messages[-1], ErrorMessage)
    assert "bad model" in app.messages[-1]._content
    assert app._agent_running is False


def test_handle_offload_reports_generic_failure(monkeypatch) -> None:
    app = MemoryApp()

    async def state_values(_app: MemoryApp, _thread_id: str) -> dict[str, object]:
        return {"messages": [HumanMessage(content="long history")]}

    async def dispatch_hook(_name: str, _payload: dict[str, object]) -> None:
        return None

    async def perform_offload(**_kwargs: object) -> OffloadResult:
        raise RuntimeError("unexpected")

    monkeypatch.setattr(memory_handlers, "get_thread_state_values", state_values)
    monkeypatch.setattr("invincat_cli.hooks.dispatch_hook", dispatch_hook)
    monkeypatch.setattr("invincat_cli.offload.perform_offload", perform_offload)

    asyncio.run(memory_handlers.handle_offload(app))

    assert isinstance(app.messages[-1], ErrorMessage)
    assert "unexpected" in app.messages[-1]._content
    assert app._agent_running is False
    assert app.spinners[-1] is None
