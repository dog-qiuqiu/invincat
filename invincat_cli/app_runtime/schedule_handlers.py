"""Schedule command and action handlers for the Textual app."""

from __future__ import annotations

from typing import Any

from invincat_cli.app_runtime.scheduler import wecom_daemon_claims_scheduled_task
from invincat_cli.widgets.messages import AppMessage, ErrorMessage


async def handle_schedule_tool_payload(app: Any, payload: dict) -> None:  # noqa: ANN401
    """Handle a structured schedule tool payload from the agent."""
    from invincat_cli.i18n import t
    from invincat_cli.scheduler.payloads import (
        apply_schedule_update_payload,
        build_schedule_create_payload_result,
        format_schedule_list_item,
    )

    ptype = payload.get("type")

    if ptype == "schedule_create":
        try:
            result = build_schedule_create_payload_result(
                payload,
                cwd=app._cwd,
                active_wecom_frame=app._current_wecom_inbound_frame,
            )
        except ValueError as exc:
            await app._mount_message(ErrorMessage(str(exc)))
            return
        app._scheduler_store.save_task(result.task)
        await app._mount_message(
            AppMessage(
                t("schedule.created").format(
                    title=result.task.title,
                    schedule=result.schedule_description,
                    timezone=result.task.timezone,
                    next_run=result.next_run_display,
                    report_path=result.report_path_display,
                )
            )
        )

    elif ptype == "schedule_update":
        task_id = payload.get("task_id", "")
        task = app._scheduler_store.load_task(task_id)
        if task is None:
            await app._mount_message(
                AppMessage(t("schedule.not_found").format(task_id=task_id))
            )
            return
        updates = payload.get("updates", {})
        try:
            task = apply_schedule_update_payload(task, updates)
        except ValueError as exc:
            await app._mount_message(ErrorMessage(str(exc)))
            return
        app._scheduler_store.save_task(task)
        await app._mount_message(
            AppMessage(t("schedule.updated").format(title=task.title))
        )

    elif ptype == "schedule_cancel":
        task_id = payload.get("task_id", "")
        task = app._scheduler_store.load_task(task_id)
        title = task.title if task else task_id
        app._scheduler_store.delete_task(task_id)
        await app._mount_message(AppMessage(t("schedule.deleted").format(title=title)))

    elif ptype == "schedule_run_now":
        task_id = payload.get("task_id", "")
        title = payload.get("title", task_id)
        task = app._scheduler_store.load_task(task_id)
        if task is None:
            await app._mount_message(
                AppMessage(t("schedule.not_found").format(task_id=task_id))
            )
            return
        if wecom_daemon_claims_scheduled_task(task, app._cwd):
            await app._mount_message(
                AppMessage(
                    "WeCom daemon is running; this scheduled task is handled by the daemon."
                )
            )
            return
        await app._mount_message(
            AppMessage(t("schedule.run_queued").format(title=title))
        )
        if app._scheduler_runner is not None:
            await app._scheduler_runner.fire_now(task)

    elif ptype == "schedule_list":
        tasks = payload.get("tasks", [])
        if not tasks:
            await app._mount_message(AppMessage(t("schedule.list_empty")))
        else:
            lines = [t("schedule.list_header").format(count=len(tasks))]
            for task_info in tasks:
                lines.append(format_schedule_list_item(task_info))
            await app._mount_message(AppMessage("\n".join(lines)))


async def show_schedule_manager(app: Any) -> None:  # noqa: ANN401
    """Push the ScheduleManagerScreen modal."""
    from invincat_cli.widgets.schedule_manager import (
        ScheduleAction,
        ScheduleManagerScreen,
    )

    screen = ScheduleManagerScreen(store=app._scheduler_store)

    def handle_result(result: ScheduleAction | None) -> None:
        if app._chat_input:
            app._chat_input.focus_input()
        if result is None:
            return
        app.call_later(app._execute_schedule_action, result)

    app.push_screen(screen, handle_result)


async def execute_schedule_action(app: Any, action: Any) -> None:  # noqa: ANN401
    """Execute a schedule action returned by the manager modal."""
    from invincat_cli.i18n import t

    task = app._scheduler_store.load_task(action.task_id)
    if task is None:
        await app._mount_message(
            AppMessage(t("schedule.not_found").format(task_id=action.task_id))
        )
        return

    if action.kind == "run_now":
        if wecom_daemon_claims_scheduled_task(task, app._cwd):
            await app._mount_message(
                AppMessage(
                    "WeCom daemon is running; this scheduled task is handled by the daemon."
                )
            )
            return
        await app._mount_message(
            AppMessage(t("schedule.run_queued").format(title=task.title))
        )
        if app._scheduler_runner is not None:
            await app._scheduler_runner.fire_now(task)

    elif action.kind == "pause":
        app._scheduler_store.set_task_enabled(task.id, False)
        await app._mount_message(AppMessage(t("schedule.paused").format(title=task.title)))

    elif action.kind == "resume":
        app._scheduler_store.set_task_enabled(task.id, True)
        await app._mount_message(AppMessage(t("schedule.resumed").format(title=task.title)))

    elif action.kind == "delete":
        app._scheduler_store.delete_task(task.id)
        await app._mount_message(AppMessage(t("schedule.deleted").format(title=task.title)))
