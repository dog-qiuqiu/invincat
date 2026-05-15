"""Constants for scheduler management tools."""

SCHEDULE_CONTEXT_FLAG = "scheduled_run"
"""Set to True in agent runtime context during an automated scheduled run."""

SCHEDULE_CREATE_TYPE = "schedule_create"
SCHEDULE_LIST_TYPE = "schedule_list"
SCHEDULE_UPDATE_TYPE = "schedule_update"
SCHEDULE_CANCEL_TYPE = "schedule_cancel"
SCHEDULE_RUN_NOW_TYPE = "schedule_run_now"

MANAGEMENT_TOOLS = frozenset(
    {
        "create_scheduled_task",
        "list_scheduled_tasks",
        "update_scheduled_task",
        "cancel_scheduled_task",
        "delete_scheduled_task",
        "run_scheduled_task_now",
    }
)
