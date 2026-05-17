"""Textual UI application for deepagents-cli."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual.app import App
from textual.css.query import NoMatches as NoMatches
from textual.screen import ModalScreen as ModalScreen

from invincat_cli.app_runtime.approval_plan_mixins import AppApprovalPlanMixin
from invincat_cli.app_runtime.bindings import APP_BINDINGS
from invincat_cli.app_runtime.command_mixins import AppCommandIntegrationMixin
from invincat_cli.app_runtime.delegate_mixins import AppRuntimeDelegateMixin
from invincat_cli.app_runtime.interaction_mixins import (
    AppInputEventMixin,
    AppSelectionMixin,
)
from invincat_cli.app_runtime.server_events import (
    ServerReady as ServerReadyMessage,
)
from invincat_cli.app_runtime.server_events import (
    ServerStartFailed as ServerStartFailedMessage,
)
from invincat_cli.app_runtime.services import AppServices
from invincat_cli.app_runtime.startup import (
    create_startup_session_state as create_startup_session_state,
)
from invincat_cli.app_runtime.state import (
    AppResult,
    DeferredAction,
)
from invincat_cli.app_runtime.state import QueuedMessage as QueuedMessage  # noqa: F401
from invincat_cli.app_runtime.terminal import disable_cursor_guide
from invincat_cli.app_runtime.textual_patch import (
    patch_textual_utf8_decoder as _patch_textual_utf8_decoder,
)
from invincat_cli.app_runtime.turn_flow_mixins import AppTurnFlowMixin
from invincat_cli.core.debug import configure_debug_logging
from invincat_cli.widgets.chat_input import ChatInput

logger = logging.getLogger(__name__)
configure_debug_logging(logger)
_monotonic = time.monotonic

_patch_textual_utf8_decoder()

if TYPE_CHECKING:
    from collections import deque

    from deepagents.backends import CompositeBackend
    from langchain_core.language_models import BaseChatModel
    from langgraph.pregel import Pregel
    from textual.worker import Worker

    from invincat_cli.mcp.tools import MCPServerInfo
    from invincat_cli.server.app_server import ServerProcess

disable_cursor_guide()


class DeepAgentsApp(
    AppApprovalPlanMixin,
    AppCommandIntegrationMixin,
    AppInputEventMixin,
    AppSelectionMixin,
    AppTurnFlowMixin,
    AppRuntimeDelegateMixin,
    App,
):
    """Main Textual application for deepagents-cli."""

    # Runtime attributes initialized by app_runtime.initialization.initialize_app().
    # Keeping the declarations on the Textual app class makes the implicit
    # cross-module contract visible to type checkers and future refactors.
    _agent: Any
    _assistant_id: str | None
    _backend: Any
    _auto_approve: bool
    _cwd: str
    _lc_thread_id: str | None
    _resume_thread_intent: str | None
    _initial_prompt: str | None
    _mcp_server_info: list[Any] | None
    _profile_override: dict[str, Any] | None
    _server_proc: Any
    _server_kwargs: dict[str, Any] | None
    _mcp_preload_kwargs: dict[str, Any] | None
    _model_kwargs: dict[str, Any] | None
    _defer_server_start: bool
    _services: AppServices
    _connecting: bool
    _sandbox_type: str | None

    _model_override: str | None
    _model_params_override: dict[str, Any] | None
    _memory_model_override: str | None
    _memory_model_params_override: dict[str, Any] | None
    _model: BaseChatModel | None
    _mcp_tool_count: int

    _status_bar: Any | None
    _chat_input: ChatInput | None
    _quit_pending: bool
    _session_state: Any | None
    _ui_adapter: Any | None
    _pending_approval_widget: Any | None
    _pending_ask_user_widget: Any | None

    _agent_worker: Worker | None
    _agent_running: bool
    _active_turn_is_planner: bool
    _agent_generation: int

    _shell_process: Any | None
    _shell_worker: Worker | None
    _shell_running: bool

    _loading_widget: Any | None
    _memory_status_clear_timer: Any | None
    _planner_agent: Any | None
    _planner_thread_id: str | None
    _main_thread_before_plan: str | None
    _planner_last_todos_fingerprint: str | None
    _planner_prompted_todos_fingerprint: str | None
    _goal_store: Any | None

    _context_tokens: int
    _tokens_approximate: bool
    _auto_offload_cooldown_until: float
    _offload_budget_cache: Any | None
    _last_typed_at: float | None
    _approval_placeholder: Any | None

    _update_available: tuple[bool, str | None]
    _session_stats: Any
    _inflight_turn_stats: Any | None
    _inflight_turn_start: float

    _pending_messages: deque[QueuedMessage]
    _queued_widgets: deque[Any]
    _processing_pending: bool
    _thread_switching: bool
    _model_switching: bool
    _deferred_actions: list[DeferredAction]
    _pending_plan_handoff_prompt: str | None

    _message_store: Any
    _startup_task: Any | None
    _discovered_skills: list[Any]
    _skill_allowed_roots: list[Path]
    _image_tracker: Any

    _wecom_task: Any | None
    _wecom_bridge: Any | None
    _wecom_lock: asyncio.Lock
    _current_wecom_inbound_frame: Any | None

    _scheduler_store: Any
    _scheduler_runner: Any | None
    _scheduler_interval_handle: Any | None
    _active_scheduled_run: Any | None
    _scheduled_run_message_offset: int
    _scheduled_turn_status: str
    _scheduled_turn_error: str | None
    _scheduled_turn_retry_used: bool

    TITLE = "Deep Agents"
    """Textual application title."""

    CSS_PATH = "app.tcss"
    """Path to the Textual CSS stylesheet for the app layout."""

    ENABLE_COMMAND_PALETTE = False
    """Disable Textual's built-in command palette in favor of the custom slash
    command system."""

    SCROLL_SENSITIVITY_Y = 1.0
    """Vertical scroll speed (reduced from Textual default for finer control)."""

    BINDINGS = APP_BINDINGS
    """App-level keybindings for interrupt, quit, toggles, and approval menu
    navigation."""

    ServerReady = ServerReadyMessage
    ServerStartFailed = ServerStartFailedMessage

    def __init__(
        self,
        *,
        agent: Pregel | None = None,
        assistant_id: str | None = None,
        backend: CompositeBackend | None = None,
        auto_approve: bool = False,
        cwd: str | Path | None = None,
        thread_id: str | None = None,
        resume_thread: str | None = None,
        initial_prompt: str | None = None,
        mcp_server_info: list[MCPServerInfo] | None = None,
        profile_override: dict[str, Any] | None = None,
        server_proc: ServerProcess | None = None,
        server_kwargs: dict[str, Any] | None = None,
        mcp_preload_kwargs: dict[str, Any] | None = None,
        model_kwargs: dict[str, Any] | None = None,
        defer_server_start: bool = False,
        services: AppServices | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the Deep Agents application.

        Args:
            agent: Pre-configured LangGraph agent, or `None` when server
                startup is deferred via `server_kwargs`.
            assistant_id: Agent identifier for memory storage
            backend: Backend for file operations
            auto_approve: Whether to start with auto-approve enabled
            cwd: Current working directory to display
            thread_id: Thread ID for the session.

                `None` when `resume_thread` is provided (resolved asynchronously).
            resume_thread: Raw resume intent from `-r` flag.

                `'__MOST_RECENT__'` for bare `-r`, a thread ID string for
                `-r <id>`, or `None` for new sessions.

                Resolved via `_resolve_resume_thread`
                during `_start_server_background`.

                Requires `server_kwargs` to be set; ignored otherwise.
            initial_prompt: Optional prompt to auto-submit when session starts
            mcp_server_info: MCP server metadata for the `/mcp` viewer.
            profile_override: Extra profile fields from `--profile-override`,
                retained so later profile-aware behavior stays consistent with
                the CLI override, including model selection details,
                offload budget display, and on-demand `create_model()`
                calls such as `/offload`.
            server_proc: LangGraph server process for the interactive session.
            server_kwargs: When provided, server startup is deferred.

                The app shows a "Connecting..." state and starts the server in
                the background using these kwargs
                for `start_server_and_get_agent`.
            mcp_preload_kwargs: Kwargs for `_preload_session_mcp_server_info`,
                run concurrently with server startup when `server_kwargs` is set.
            model_kwargs: Kwargs for deferred `create_model()`.

                When provided, model creation runs in a background worker after
                first paint instead of blocking startup.
            defer_server_start: Keep `server_kwargs` for later but do not start
                the server until the user selects a primary model.
            services: Runtime service factories. Tests can provide isolated
                stores; production uses lazy defaults.
            **kwargs: Additional arguments passed to parent
        """
        super().__init__(**kwargs)

        from invincat_cli.app_runtime.initialization import initialize_app

        initialize_app(
            self,
            agent=agent,
            assistant_id=assistant_id,
            backend=backend,
            auto_approve=auto_approve,
            cwd=cwd,
            thread_id=thread_id,
            resume_thread=resume_thread,
            initial_prompt=initial_prompt,
            mcp_server_info=mcp_server_info,
            profile_override=profile_override,
            server_proc=server_proc,
            server_kwargs=server_kwargs,
            mcp_preload_kwargs=mcp_preload_kwargs,
            model_kwargs=model_kwargs,
            defer_server_start=defer_server_start,
            services=services,
        )

async def run_textual_app(
    *,
    agent: Any = None,  # noqa: ANN401
    assistant_id: str | None = None,
    backend: CompositeBackend | None = None,
    auto_approve: bool = False,
    cwd: str | Path | None = None,
    thread_id: str | None = None,
    resume_thread: str | None = None,
    initial_prompt: str | None = None,
    mcp_server_info: list[MCPServerInfo] | None = None,
    profile_override: dict[str, Any] | None = None,
    server_proc: ServerProcess | None = None,
    server_kwargs: dict[str, Any] | None = None,
    mcp_preload_kwargs: dict[str, Any] | None = None,
    model_kwargs: dict[str, Any] | None = None,
    defer_server_start: bool = False,
) -> AppResult:
    """Run the Textual application and return its final result."""
    from invincat_cli.app_runtime.runner import run_textual_app as run_app

    return await run_app(
        app_cls=DeepAgentsApp,
        result_cls=AppResult,
        app_kwargs={
            "agent": agent,
            "assistant_id": assistant_id,
            "backend": backend,
            "auto_approve": auto_approve,
            "cwd": cwd,
            "thread_id": thread_id,
            "resume_thread": resume_thread,
            "initial_prompt": initial_prompt,
            "mcp_server_info": mcp_server_info,
            "profile_override": profile_override,
            "server_proc": server_proc,
            "server_kwargs": server_kwargs,
            "mcp_preload_kwargs": mcp_preload_kwargs,
            "model_kwargs": model_kwargs,
            "defer_server_start": defer_server_start,
        },
    )
