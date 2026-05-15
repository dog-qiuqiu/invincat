"""App-bound plan-mode handlers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from invincat_cli.app_runtime.plan import (
    build_plan_handoff_prompt,
    build_plan_text,
    build_planner_system_prompt,
    build_planner_turn_input,
    normalize_state_messages,
)
from invincat_cli.i18n import t
from invincat_cli.plan_mode.policy import (
    extract_todos_from_state,
    plan_todos_fingerprint,
    turn_has_tool,
)
from invincat_cli.plan_mode.runtime import resolve_planner_turn
from invincat_cli.widgets.messages import AppMessage, UserMessage

logger = logging.getLogger(__name__)


async def handle_plan_task(
    app: Any,  # noqa: ANN401
    task: str | None = None,
    *,
    command: str = "/plan",
) -> None:
    """Enter plan mode and optionally start planning an inline task."""
    from invincat_cli.app_runtime.state import new_thread_id

    if app._session_state and app._session_state.plan_mode:
        await app._mount_message(AppMessage(t("plan.already_on")))
        return
    app._planner_thread_id = new_thread_id()
    app._planner_last_todos_fingerprint = None
    app._planner_prompted_todos_fingerprint = None
    app._planner_original_task = None
    app._planner_refinement_notes = []
    app._planner_rejected_todos = []
    if app._session_state:
        app._main_thread_before_plan = app._session_state.thread_id
        app._session_state.plan_mode = True
    if app._status_bar:
        app._status_bar.set_plan_mode(enabled=True)
    await app._mount_message(UserMessage(command))
    await app._mount_message(AppMessage(t("plan.entered")))
    normalized_task = (task or "").strip()
    if normalized_task:
        planner_started = await app._run_planner(normalized_task)
        if not planner_started:
            app._reset_plan_mode_state()


def reset_plan_mode_state(app: Any) -> None:  # noqa: ANN401
    """Restore main-thread state and clear planner-only bookkeeping."""
    if app._session_state:
        app._session_state.plan_mode = False
        if app._main_thread_before_plan:
            app._session_state.thread_id = app._main_thread_before_plan
    if app._status_bar:
        app._status_bar.set_plan_mode(enabled=False)
    app._planner_thread_id = None
    app._main_thread_before_plan = None
    app._planner_last_todos_fingerprint = None
    app._planner_prompted_todos_fingerprint = None
    app._pending_plan_handoff_prompt = None
    app._planner_original_task = None
    app._planner_refinement_notes = []
    app._planner_rejected_todos = []


async def ensure_planner_agent(app: Any) -> Any | None:  # noqa: ANN401
    """Lazily create and cache a planner peer-agent."""
    if app._planner_agent is not None:
        return app._planner_agent
    try:
        from langgraph.checkpoint.memory import InMemorySaver

        from invincat_cli.agent import create_cli_agent
        from invincat_cli.config import settings
        from invincat_cli.middleware.plan_agent import (
            PLANNER_ALLOWED_TOOLS,
            PLANNER_APPROVE_PLAN_SYSTEM_PROMPT,
            PLANNER_SYSTEM_PROMPT,
            PlannerToolAllowListMiddleware,
            PlannerVisibleToolsMiddleware,
        )
        from invincat_cli.project_utils import ProjectContext
        from invincat_cli.tools import fetch_url, web_search

        model = (
            app._model
            if app._model is not None
            else (app._model_override or "claude-sonnet-4-6")
        )
        planner_assistant_id = f"{app._assistant_id or 'agent'}-planner"
        planner_tools: list[Any] = [fetch_url]
        planner_allowed_tools = set(PLANNER_ALLOWED_TOOLS)
        if settings.has_tavily:
            planner_tools.append(web_search)
        else:
            planner_allowed_tools.discard("web_search")
        project_context = ProjectContext.from_user_cwd(Path(app._cwd))
        planner_system_prompt = build_planner_system_prompt(
            base_prompt=PLANNER_SYSTEM_PROMPT,
            cwd=app._cwd,
        )
        planner_checkpointer = getattr(app._agent, "checkpointer", None)
        if planner_checkpointer is None:
            planner_checkpointer = InMemorySaver()
        planner_agent, _planner_backend = create_cli_agent(
            model=model,
            assistant_id=planner_assistant_id,
            system_prompt=planner_system_prompt,
            auto_approve=app._auto_approve,
            enable_memory=False,
            enable_skills=False,
            enable_ask_user=True,
            enable_shell=False,
            tools=planner_tools,
            cwd=app._cwd,
            project_context=project_context,
            mcp_server_info=app._mcp_server_info,
            checkpointer=planner_checkpointer,
            approve_plan_system_prompt=PLANNER_APPROVE_PLAN_SYSTEM_PROMPT,
            extra_middleware=[
                PlannerVisibleToolsMiddleware(planner_allowed_tools),
                PlannerToolAllowListMiddleware(planner_allowed_tools),
            ],
        )
        app._planner_agent = planner_agent
        return app._planner_agent
    except Exception:
        logger.exception("Failed to initialize planner agent")
        return None


async def run_planner(app: Any, task: str) -> bool:  # noqa: ANN401
    """Send a user message to the planner agent session."""
    from invincat_cli.middleware.plan_agent import build_planner_input

    if not app._agent or not app._session_state:
        await app._mount_message(AppMessage(t("plan.agent_not_configured")))
        return False

    planner = await app._ensure_planner_agent()
    if planner is None:
        await app._mount_message(AppMessage(t("plan.planner_unavailable")))
        return False

    if not app._planner_thread_id:
        from invincat_cli.app_runtime.state import new_thread_id

        app._planner_thread_id = new_thread_id()

    app._planner_last_todos_fingerprint = None
    app._planner_prompted_todos_fingerprint = None
    if not getattr(app, "_planner_original_task", None):
        app._planner_original_task = task.strip()
        app._planner_refinement_notes = []
    else:
        notes = list(getattr(app, "_planner_refinement_notes", []))
        if task.strip():
            notes.append(task.strip())
        app._planner_refinement_notes = notes

    planner_task = build_planner_input(
        getattr(app, "_planner_original_task", task),
        getattr(app, "_planner_refinement_notes", []),
        rejected_plan=getattr(app, "_planner_rejected_todos", []),
    )

    return await app._send_to_agent(
        build_planner_turn_input(task=planner_task, cwd=app._cwd),
        agent_override=planner,
        thread_id_override=app._planner_thread_id,
        post_turn_hook=app._after_planner_turn,
    )


async def exit_plan_mode(app: Any) -> None:  # noqa: ANN401
    """Exit plan mode, cancel planner work, and restore main thread."""
    if not app._session_state or not app._session_state.plan_mode:
        await app._mount_message(AppMessage(t("plan.not_on")))
        return

    if app._agent_running and app._agent_worker and app._active_turn_is_planner:
        if app._pending_approval_widget:
            app._pending_approval_widget.action_select_reject()
        await app._remove_approval_placeholder(context="plan exit")
        app._pending_approval_widget = None
        app._agent_worker.cancel()
        app._agent_running = False
        app._agent_worker = None
        app._active_turn_is_planner = False

    app._deferred_actions = [
        action for action in app._deferred_actions if action.kind != "plan_handoff"
    ]
    app._pending_plan_handoff_prompt = None

    app._reset_plan_mode_state()
    await app._mount_message(AppMessage(t("plan.exited")))


async def get_thread_state_values_for_agent(
    agent: Any,  # noqa: ANN401
    thread_id: str,
) -> dict[str, Any]:
    """Fetch state values from a specific agent/thread pair."""
    config = {"configurable": {"thread_id": thread_id}}
    state = await agent.aget_state(config)
    if state and state.values:
        return dict(state.values)
    return {}


async def after_planner_turn(app: Any) -> None:  # noqa: ANN401
    """Check planner turn result and drive plan approval flow."""
    if not app._planner_agent or not app._planner_thread_id:
        return

    state_values = await app._get_thread_state_values_for_agent(
        app._planner_agent, app._planner_thread_id
    )
    if not state_values:
        return

    messages = normalize_state_messages(state_values.get("messages", []))
    resolution = resolve_planner_turn(
        state_values,
        messages=messages,
        prompted_todos_fingerprint=app._planner_prompted_todos_fingerprint,
    )

    if resolution.kind == "noop":
        return
    if resolution.kind == "rejected":
        app._planner_rejected_todos = extract_todos_from_state(state_values)
        if not resolution.suppress_refine_prompt:
            await app._mount_message(AppMessage(t("plan.refine_prompt")))
        return
    if resolution.kind == "approved":
        await app._finalize_planner_approval(
            resolution.todos or [],
            planner_state_values=state_values,
        )
        return
    if resolution.kind == "drifted":
        await app._mount_message(AppMessage(t("plan.missing_checklist")))
        return
    if resolution.kind == "approval_no_valid_todos":
        await app._mount_message(AppMessage(t("plan.approval_no_valid_todos")))
        return
    if resolution.kind == "ready_no_valid_todos":
        await app._mount_message(AppMessage(t("plan.ready_no_valid_todos")))
        return
    if resolution.kind == "already_prompted":
        return
    if resolution.kind == "prompt_todos":
        await app._process_planner_todos_approval(resolution.todos or [])


async def process_planner_todos_approval(
    app: Any,  # noqa: ANN401
    todos: list[dict[str, str]],
) -> bool:
    """Approve planner todos and finalize plan mode when approved."""
    todos_fingerprint = plan_todos_fingerprint(todos)
    if todos_fingerprint == app._planner_last_todos_fingerprint:
        return False

    future = await app._request_approve_plan(todos)
    result = await future
    app._planner_last_todos_fingerprint = todos_fingerprint
    if result.get("type") != "approved":
        await app._mount_message(AppMessage(t("plan.refine_prompt")))
        return False

    await app._finalize_planner_approval(todos)
    return True


async def maybe_approve_current_planner_todos(app: Any) -> bool:  # noqa: ANN401
    """Best-effort immediate approval when planner already has todo state."""
    from invincat_cli.plan_mode.policy import extract_todos_from_message, latest_ai_text

    if not app._planner_agent or not app._planner_thread_id:
        return False
    state_values = await app._get_thread_state_values_for_agent(
        app._planner_agent, app._planner_thread_id
    )
    messages = normalize_state_messages(state_values.get("messages", []))
    if not turn_has_tool(messages, "write_todos"):
        return False
    todos = extract_todos_from_state(state_values)
    if not todos:
        todos = extract_todos_from_message(latest_ai_text(messages)) or []
    if not todos:
        return False
    return await app._process_planner_todos_approval(todos)


async def finalize_planner_approval(
    app: Any,  # noqa: ANN401
    todos: list[dict[str, str]],
    *,
    planner_state_values: dict[str, Any] | None = None,
) -> None:
    """Finalize plan mode after approval and handoff execution to main agent."""
    plan_text = build_plan_text(todos)
    effective_state = planner_state_values
    if effective_state is None and app._planner_agent and app._planner_thread_id:
        try:
            effective_state = await app._get_thread_state_values_for_agent(
                app._planner_agent,
                app._planner_thread_id,
            )
        except Exception:
            logger.debug(
                "Failed to fetch planner state for handoff prompt; "
                "falling back to todos-only handoff",
                exc_info=True,
            )
            effective_state = None
    handoff_prompt = build_plan_handoff_prompt(
        todos,
        planner_state_values=effective_state,
        refinement_notes=getattr(app, "_planner_refinement_notes", []),
    )
    app._reset_plan_mode_state()
    app._pending_plan_handoff_prompt = handoff_prompt
    await app._mount_message(
        AppMessage(f"{t('plan.approved_no_execute')}\n\n{plan_text}")
    )


async def execute_plan_handoff(app: Any, prompt: str) -> None:  # noqa: ANN401
    """Execute approved plan handoff explicitly on the main agent."""
    if not app._session_state:
        return

    app._session_state.plan_mode = False
    if app._status_bar:
        app._status_bar.set_plan_mode(enabled=False)

    await app._mount_message(AppMessage(t("plan.handoff_started")))
    await app._mount_message(
        AppMessage(f"{t('plan.handoff_prompt_preview')}\n\n{prompt}")
    )
    started = await app._send_to_agent(prompt)
    if not started:
        app._pending_plan_handoff_prompt = prompt
