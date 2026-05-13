"""App-bound plan-mode handlers."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from invincat_cli.app_runtime.plan import build_planner_system_prompt

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
