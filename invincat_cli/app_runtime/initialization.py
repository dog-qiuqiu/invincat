"""Constructor-time initialization for the Textual app."""

from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from typing import Any

from invincat_cli.app_runtime.services import AppServices
from invincat_cli.app_runtime.theme_prefs import load_theme_preference
from invincat_cli.core.session_stats import SessionStats
from invincat_cli.widgets.message_store import MessageStore


def initialize_app(
    app: Any,  # noqa: ANN401
    *,
    agent: Any,
    assistant_id: str | None,
    backend: Any,
    auto_approve: bool,
    cwd: str | Path | None,
    thread_id: str | None,
    resume_thread: str | None,
    initial_prompt: str | None,
    mcp_server_info: list[Any] | None,
    profile_override: dict[str, Any] | None,
    server_proc: Any,
    server_kwargs: dict[str, Any] | None,
    mcp_preload_kwargs: dict[str, Any] | None,
    model_kwargs: dict[str, Any] | None,
    defer_server_start: bool,
    services: AppServices | None,
) -> None:
    """Initialize all app-owned runtime state."""
    _initialize_locale_and_theme(app)
    _initialize_constructor_options(
        app,
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
    _initialize_runtime_state(app)
    _initialize_external_integrations(app)


def _initialize_locale_and_theme(app: Any) -> None:  # noqa: ANN401
    app._register_custom_themes()

    from invincat_cli.i18n import load_language_from_config, set_language

    language = load_language_from_config()
    set_language(language)

    app.theme = load_theme_preference()


def _initialize_constructor_options(
    app: Any,  # noqa: ANN401
    *,
    agent: Any,
    assistant_id: str | None,
    backend: Any,
    auto_approve: bool,
    cwd: str | Path | None,
    thread_id: str | None,
    resume_thread: str | None,
    initial_prompt: str | None,
    mcp_server_info: list[Any] | None,
    profile_override: dict[str, Any] | None,
    server_proc: Any,
    server_kwargs: dict[str, Any] | None,
    mcp_preload_kwargs: dict[str, Any] | None,
    model_kwargs: dict[str, Any] | None,
    defer_server_start: bool,
    services: AppServices | None,
) -> None:
    app._agent = agent
    app._assistant_id = assistant_id
    app._backend = backend
    app._auto_approve = auto_approve
    app._cwd = str(cwd) if cwd else str(Path.cwd())
    app._lc_thread_id = thread_id
    app._resume_thread_intent = resume_thread
    app._initial_prompt = initial_prompt
    app._mcp_server_info = mcp_server_info
    app._profile_override = profile_override
    app._server_proc = server_proc
    app._server_kwargs = server_kwargs
    app._mcp_preload_kwargs = mcp_preload_kwargs
    app._model_kwargs = model_kwargs
    app._defer_server_start = defer_server_start
    app._services = services or AppServices()
    app._connecting = server_kwargs is not None and not defer_server_start

    raw = (server_kwargs or {}).get("sandbox_type")
    app._sandbox_type = raw if raw and raw != "none" else None

    app._model_override = None
    app._model_params_override = None
    app._memory_model_override = None
    app._memory_model_params_override = None
    app._model = None
    app._mcp_tool_count = sum(len(s.tools) for s in (mcp_server_info or []))


def _initialize_runtime_state(app: Any) -> None:  # noqa: ANN401
    app._status_bar = None
    app._chat_input = None
    app._quit_pending = False
    app._session_state = None
    app._ui_adapter = None
    app._pending_approval_widget = None
    app._pending_ask_user_widget = None

    app._agent_worker = None
    app._agent_running = False
    app._active_turn_is_planner = False
    app._agent_generation = 0

    app._shell_process = None
    app._shell_worker = None
    app._shell_running = False

    app._loading_widget = None
    app._memory_status_clear_timer = None
    app._planner_agent = None
    app._planner_thread_id = None
    app._main_thread_before_plan = None
    app._planner_last_todos_fingerprint = None
    app._planner_prompted_todos_fingerprint = None
    app._planner_original_task = None
    app._planner_refinement_notes = []
    app._planner_rejected_todos = []
    app._goal_store = None

    app._context_tokens = 0
    app._tokens_approximate = False
    app._auto_offload_cooldown_until = 0.0
    app._offload_budget_cache = None
    app._last_typed_at = None
    app._approval_placeholder = None

    app._update_available = (False, None)
    app._session_stats = SessionStats()
    app._inflight_turn_stats = None
    app._inflight_turn_start = 0.0

    app._pending_messages = deque()
    app._queued_widgets = deque()
    app._processing_pending = False
    app._thread_switching = False
    app._model_switching = False
    app._deferred_actions = []
    app._pending_plan_handoff_prompt = None

    app._message_store = MessageStore()
    app._startup_task = None
    app._discovered_skills = []
    app._skill_allowed_roots = []

    from invincat_cli.io.input import MediaTracker

    app._image_tracker = MediaTracker()


def _initialize_external_integrations(app: Any) -> None:  # noqa: ANN401
    app._wecom_task = None
    app._wecom_bridge = None
    app._wecom_lock = asyncio.Lock()
    app._current_wecom_inbound_frame = None

    app._scheduler_store = app._services.lazy_scheduler_store()
    app._scheduler_runner = None
    app._scheduler_interval_handle = None
    app._active_scheduled_run = None
    app._scheduled_run_message_offset = 0
    app._scheduled_turn_status = "success"
    app._scheduled_turn_error = None
    app._scheduled_turn_retry_used = False
