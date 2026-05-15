"""Focused tests for schedule management tools and middleware wrappers."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from invincat_cli.scheduler.models import DeliverySpec, ReportSpec, ScheduledTask
from invincat_cli.scheduler.tool import (
    SCHEDULE_CONTEXT_FLAG,
    SCHEDULE_CREATE_TYPE,
    SCHEDULE_RUN_NOW_TYPE,
    ScheduleMiddleware,
    _is_scheduled_run,
    _tool_name,
    parse_once_at,
    parse_schedule_tool_result,
    validate_schedule_create_options,
    validate_timezone_name,
)


class FakeStore:
    def __init__(self, task: ScheduledTask | None = None) -> None:
        self.task = task

    def load_task(self, task_id: str) -> ScheduledTask | None:
        if self.task is not None and self.task.id == task_id:
            return self.task
        return None

    def list_tasks(self) -> list[ScheduledTask]:
        return [self.task] if self.task is not None else []


def _task(*, schedule_type: str = "recurring") -> ScheduledTask:
    now = datetime.now(UTC).isoformat()
    return ScheduledTask(
        id="task-1",
        title="Task",
        enabled=True,
        prompt="do it",
        cron="0 8 * * *",
        timezone="UTC",
        cwd="/tmp",
        delivery=DeliverySpec(),
        report=ReportSpec(),
        created_at=now,
        updated_at=now,
        next_run_at=now,
        last_run_at=None,
        last_status="never",
        last_error=None,
        run_count=0,
        failure_count=0,
        schedule_type=schedule_type,
        run_at=now if schedule_type == "once" else None,
    )


def _invoke_tool(tool_obj, args: dict, tool_call_id: str = "call-1") -> dict:
    result = tool_obj.invoke(
        {
            "args": args,
            "name": tool_obj.name,
            "type": "tool_call",
            "id": tool_call_id,
        }
    )
    return json.loads(result.content if hasattr(result, "content") else str(result))


def test_helpers_handle_runtime_and_tool_name_shapes() -> None:
    assert _is_scheduled_run(SimpleNamespace(context={SCHEDULE_CONTEXT_FLAG: True}))
    assert not _is_scheduled_run(SimpleNamespace(context=[]))
    assert not _is_scheduled_run(SimpleNamespace())
    assert _tool_name(SimpleNamespace(name="tool")) == "tool"
    assert _tool_name({"name": "dict-tool"}) == "dict-tool"
    assert _tool_name(object()) == ""


def test_validation_helpers_reject_empty_and_invalid_values() -> None:
    with pytest.raises(ValueError, match="timezone"):
        validate_timezone_name(" ")
    with pytest.raises(ValueError, match="once_at"):
        parse_once_at(" ", "UTC")
    with pytest.raises(ValueError, match="ISO datetime"):
        parse_once_at("tomorrow", "UTC")
    assert parse_once_at("2026-05-10T20:00:00", "Asia/Shanghai") == (
        "2026-05-10T12:00:00+00:00"
    )

    with pytest.raises(ValueError, match="output_mode"):
        validate_schedule_create_options(
            output_mode="bad",
            report_format="markdown",
            misfire_policy="run_once",
            timeout_seconds=600,
        )
    with pytest.raises(ValueError, match="report_format"):
        validate_schedule_create_options(
            output_mode="message",
            report_format="html",
            misfire_policy="run_once",
            timeout_seconds=600,
        )
    with pytest.raises(ValueError, match="misfire_policy"):
        validate_schedule_create_options(
            output_mode="message",
            report_format="markdown",
            misfire_policy="later",
            timeout_seconds=600,
        )
    with pytest.raises(ValueError, match="timeout_seconds"):
        validate_schedule_create_options(
            output_mode="message",
            report_format="markdown",
            misfire_policy="run_once",
            timeout_seconds=-1,
        )


def test_parse_schedule_tool_result_handles_content_blocks_and_bad_shapes() -> None:
    payload = {"type": SCHEDULE_CREATE_TYPE, "task_id": "task-1"}

    assert parse_schedule_tool_result([{"type": "text", "text": json.dumps(payload)}])
    assert parse_schedule_tool_result(json.dumps(["not", "dict"])) is None
    assert parse_schedule_tool_result("") is None


def test_create_tool_reports_parse_and_option_errors() -> None:
    middleware = ScheduleMiddleware(store=FakeStore())
    create_tool = next(t for t in middleware.tools if t.name == "create_scheduled_task")

    bad_schedule = _invoke_tool(
        create_tool,
        {"title": "Task", "schedule": "bad schedule", "prompt": "do it"},
    )
    bad_once = _invoke_tool(
        create_tool,
        {
            "title": "Task",
            "schedule": "once",
            "prompt": "do it",
            "once_at": "tomorrow",
        },
    )
    bad_options = _invoke_tool(
        create_tool,
        {
            "title": "Task",
            "schedule": "daily 08:00",
            "prompt": "do it",
            "output_mode": "bad",
        },
    )

    assert "Unrecognised schedule" in bad_schedule["error"]
    assert "ISO datetime" in bad_once["error"]
    assert "output_mode" in bad_options["error"]


def test_update_cancel_delete_and_run_now_tool_error_branches() -> None:
    missing_middleware = ScheduleMiddleware(store=FakeStore())
    update_tool = next(
        t for t in missing_middleware.tools if t.name == "update_scheduled_task"
    )
    cancel_tool = next(
        t for t in missing_middleware.tools if t.name == "cancel_scheduled_task"
    )
    delete_tool = next(
        t for t in missing_middleware.tools if t.name == "delete_scheduled_task"
    )
    run_now_tool = next(
        t for t in missing_middleware.tools if t.name == "run_scheduled_task_now"
    )

    assert "not found" in _invoke_tool(update_tool, {"task_id": "missing"})["error"]
    assert "not found" in _invoke_tool(cancel_tool, {"task_id": "missing"})["error"]
    assert "not found" in _invoke_tool(delete_tool, {"task_id": "missing"})["error"]
    assert "not found" in _invoke_tool(run_now_tool, {"task_id": "missing"})["error"]

    once_middleware = ScheduleMiddleware(store=FakeStore(_task(schedule_type="once")))
    once_update_tool = next(
        t for t in once_middleware.tools if t.name == "update_scheduled_task"
    )
    once_error = _invoke_tool(
        once_update_tool,
        {"task_id": "task-1", "schedule": "daily 09:00"},
    )
    assert "one-shot task" in once_error["error"]

    recurring_middleware = ScheduleMiddleware(store=FakeStore(_task()))
    recurring_update_tool = next(
        t for t in recurring_middleware.tools if t.name == "update_scheduled_task"
    )
    bad_timezone = _invoke_tool(
        recurring_update_tool,
        {"task_id": "task-1", "timezone": "Bad/Zone"},
    )
    bad_schedule = _invoke_tool(
        recurring_update_tool,
        {"task_id": "task-1", "schedule": "bad schedule"},
    )
    run_now = _invoke_tool(
        next(
            t for t in recurring_middleware.tools if t.name == "run_scheduled_task_now"
        ),
        {"task_id": "task-1"},
    )
    successful_update = _invoke_tool(
        recurring_update_tool,
        {
            "task_id": "task-1",
            "title": "Updated",
            "schedule": "daily 10:00",
            "prompt": "new prompt",
            "enabled": False,
            "timezone": "UTC",
        },
    )
    cancel_payload = _invoke_tool(
        next(
            t for t in recurring_middleware.tools if t.name == "cancel_scheduled_task"
        ),
        {"task_id": "task-1"},
    )

    assert "Invalid timezone" in bad_timezone["error"]
    assert "Unrecognised schedule" in bad_schedule["error"]
    assert successful_update["updates"] == {
        "title": "Updated",
        "cron": "0 10 * * *",
        "schedule_input": "daily 10:00",
        "prompt": "new prompt",
        "enabled": False,
        "timezone": "UTC",
    }
    assert cancel_payload["task_id"] == "task-1"
    assert run_now["type"] == SCHEDULE_RUN_NOW_TYPE
    assert run_now["title"] == "Task"


def test_wrap_model_and_tool_calls_filter_or_pass_through() -> None:
    middleware = ScheduleMiddleware(store=FakeStore())
    scheduled_runtime = SimpleNamespace(context={SCHEDULE_CONTEXT_FLAG: True})
    normal_runtime = SimpleNamespace(context={})
    create_tool = SimpleNamespace(name="create_scheduled_task")
    other_tool = {"name": "safe_tool"}
    overrides: list[list] = []

    class Request:
        def __init__(self, runtime) -> None:
            self.runtime = runtime
            self.tools = [create_tool, other_tool]

        def override(self, *, tools: list) -> Request:
            overrides.append(tools)
            return SimpleNamespace(runtime=self.runtime, tools=tools)

    assert middleware.wrap_model_call(
        Request(scheduled_runtime),
        lambda request: request.tools,
    ) == [other_tool]
    assert middleware.wrap_model_call(
        Request(normal_runtime),
        lambda request: request.tools,
    ) == [create_tool, other_tool]

    tool_request = SimpleNamespace(
        tool_call={"name": "safe_tool", "id": "call-1"},
        runtime=scheduled_runtime,
    )
    assert middleware.wrap_tool_call(tool_request, lambda request: "ok") == "ok"  # type: ignore[arg-type]
    assert overrides[0] == [other_tool]


def test_async_wrappers_filter_and_reject_management_calls() -> None:
    middleware = ScheduleMiddleware(store=FakeStore())
    runtime = SimpleNamespace(context={SCHEDULE_CONTEXT_FLAG: True})

    class Request:
        def __init__(self) -> None:
            self.runtime = runtime
            self.tools = [
                SimpleNamespace(name="create_scheduled_task"),
                {"name": "safe_tool"},
            ]

        def override(self, *, tools: list) -> Request:
            return SimpleNamespace(runtime=runtime, tools=tools)

    async def run() -> None:
        async def model_handler(request):
            return request.tools

        assert await middleware.awrap_model_call(Request(), model_handler) == [
            {"name": "safe_tool"}
        ]

        async def tool_handler(_request):
            raise AssertionError("handler should not run")

        result = await middleware.awrap_tool_call(
            SimpleNamespace(
                tool_call={"name": "create_scheduled_task", "id": "call-1"},
                runtime=runtime,
            ),
            tool_handler,
        )
        assert result.status == "error"

        async def pass_through_handler(_request):
            return "async-ok"

        assert (
            await middleware.awrap_tool_call(
                SimpleNamespace(
                    tool_call={"name": "create_scheduled_task", "id": "call-2"},
                    runtime=SimpleNamespace(context={}),
                ),
                pass_through_handler,
            )
            == "async-ok"
        )

    asyncio.run(run())


def test_reject_management_tool_allows_normal_runtime() -> None:
    middleware = ScheduleMiddleware(store=FakeStore())
    request = SimpleNamespace(
        tool_call={"name": "create_scheduled_task", "id": "call-1"},
        runtime=SimpleNamespace(context={}),
    )

    assert middleware._reject_management_tool_during_scheduled_run(request) is None
