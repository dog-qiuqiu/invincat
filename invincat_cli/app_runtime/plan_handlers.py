"""App-bound plan-mode handlers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from invincat_cli.app_runtime.approval import plan_todos_fingerprint
from invincat_cli.app_runtime.plan import (
    build_plan_handoff_prompt,
    build_plan_text,
    build_planner_system_prompt,
    build_planner_turn_input,
    extract_latest_ai_text,
    extract_todos_from_state,
    latest_ai_text_after_latest_tool,
    normalize_state_messages,
    planner_turn_approve_plan_decision,
    planner_turn_has_write_todos,
)
from invincat_cli.i18n import t
from invincat_cli.widgets.messages import AppMessage

logger = logging.getLogger(__name__)


async def ensure_planner_agent(app: Any) -> Any | None:  # noqa: ANN401
    """Lazily create and cache a planner peer-agent."""
    if app._planner_agent is not None:
        return app._planner_agent
    try:
        from langgraph.checkpoint.memory import InMemorySaver

        from invincat_cli.agent import create_cli_agent
        from invincat_cli.config import settings
        from invincat_cli.plan_agent import (
            PLANNER_ALLOWED_TOOLS,
            PLANNER_APPROVE_PLAN_SYSTEM_PROMPT,
            PLANNER_SYSTEM_PROMPT,
            PlannerToolAllowListMiddleware,
            PlannerVisibleToolsMiddleware,
        )
        from invincat_cli.project_utils import ProjectContext
        from invincat_cli.tools import fetch_url, web_search

        model = app._model if app._model is not None else (
            app._model_override or "claude-sonnet-4-6"
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

    return await app._send_to_agent(
        build_planner_turn_input(task=task, cwd=app._cwd),
        agent_override=planner,
        thread_id_override=app._planner_thread_id,
        post_turn_hook=app._after_planner_turn,
    )


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
    from invincat_cli.plan_agent import extract_todos_from_message

    if not app._planner_agent or not app._planner_thread_id:
        return

    state_values = await app._get_thread_state_values_for_agent(
        app._planner_agent, app._planner_thread_id
    )
    if not state_values:
        return

    messages = normalize_state_messages(state_values.get("messages", []))
    approve_plan_decision = planner_turn_approve_plan_decision(messages)
    if approve_plan_decision is not None:
        if approve_plan_decision != "approved":
            if not latest_ai_text_after_latest_tool(messages, "approve_plan"):
                await app._mount_message(AppMessage(t("plan.refine_prompt")))
            return

        todos = extract_todos_from_state(state_values)
        if not todos:
            latest_text = extract_latest_ai_text(messages)
            todos = extract_todos_from_message(latest_text) or []
        if not todos:
            await app._mount_message(AppMessage(t("plan.approval_no_valid_todos")))
            return
        await app._finalize_planner_approval(
            todos,
            planner_state_values=state_values,
        )
        return

    if not planner_turn_has_write_todos(messages):
        return

    todos = extract_todos_from_state(state_values)
    if not todos:
        latest_text = extract_latest_ai_text(messages)
        todos = extract_todos_from_message(latest_text) or []
    if not todos:
        await app._mount_message(AppMessage(t("plan.ready_no_valid_todos")))
        return

    todos_fingerprint = plan_todos_fingerprint(todos)
    if todos_fingerprint == app._planner_prompted_todos_fingerprint:
        return

    await app._process_planner_todos_approval(todos)


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
    from invincat_cli.plan_agent import extract_todos_from_message

    if not app._planner_agent or not app._planner_thread_id:
        return False
    state_values = await app._get_thread_state_values_for_agent(
        app._planner_agent, app._planner_thread_id
    )
    messages = normalize_state_messages(state_values.get("messages", []))
    if not planner_turn_has_write_todos(messages):
        return False
    todos = extract_todos_from_state(state_values)
    if not todos:
        latest_text = extract_latest_ai_text(messages)
        todos = extract_todos_from_message(latest_text) or []
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
    await app._mount_message(AppMessage(f"{t('plan.handoff_prompt_preview')}\n\n{prompt}"))
    started = await app._send_to_agent(prompt)
    if not started:
        app._pending_plan_handoff_prompt = prompt
