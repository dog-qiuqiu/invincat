"""Textual UI application for deepagents-cli."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
import time
import uuid
import webbrowser


def _patch_textual_utf8_decoder() -> None:
    """Patch Textual's Linux input driver to tolerate invalid UTF-8 bytes.

    Textual's LinuxDriver reads raw bytes from the terminal file descriptor and
    decodes them with a *strict* UTF-8 incremental decoder.  On terminals that
    fall back to X10-style mouse tracking (i.e. they don't honour the SGR mouse
    mode escape ``\\x1b[?1006h``), mouse-move events encode coordinates as raw
    bytes: ``\\x1b[M<button+32><x+32><y+32>``.  At column positions > 95,
    ``x+32 > 127``.  Certain combinations of button modifiers and large column
    values produce byte pairs that are invalid UTF-8 (e.g. a 2-byte lead byte
    followed by another lead byte), which crashes the input thread.

    Replacing the strict decoder with a ``'replace'`` mode decoder silently
    substitutes any invalid byte with U+FFFD instead of raising an exception,
    preserving all normal keyboard and mouse input.
    """
    try:
        import textual.drivers.linux_driver as _ld
        from codecs import getincrementaldecoder as _orig_get

        def _tolerant_getincrementaldecoder(encoding: str):  # type: ignore[return]
            decoder_cls = _orig_get(encoding)
            if encoding.lower().replace("-", "") == "utf8":

                class _TolerantDecoder(decoder_cls):  # type: ignore[valid-type]
                    def __init__(self, errors: str = "replace") -> None:
                        super().__init__(errors)

                return _TolerantDecoder
            return decoder_cls

        _ld.getincrementaldecoder = _tolerant_getincrementaldecoder  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass  # Best-effort — don't break startup if patching fails


_patch_textual_utf8_decoder()
from collections import deque
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from textual.app import App, ScreenStackError
from textual.binding import Binding, BindingType
from textual.containers import Container, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import ModalScreen
from textual.style import Style as TStyle
from textual.theme import Theme
from textual.widgets import Static

from invincat_cli import theme
from invincat_cli.model_config import ModelTarget
from invincat_cli.app_runtime.state import (
    AppResult,
    DeferredAction,
    InputMode,
    QueuedMessage,
    TextualSessionState,
    ThreadHistoryPayload,
    new_thread_id,
)
from invincat_cli.app_runtime.approval import (
    APPROVAL_PLACEHOLDER_CLASS,
    APPROVAL_PLACEHOLDER_TEXT,
    DEFERRED_APPROVAL_TIMEOUT_SECONDS,
    DEFERRED_APPROVAL_POLL_SECONDS,
    INTERACTION_POLL_SECONDS,
    TYPING_IDLE_THRESHOLD_SECONDS,
    build_approve_plan_action_request,
    build_auto_approved_shell_message,
    build_interaction_widget_id,
    deadline_expired,
    disallowed_plan_interrupt_tools,
    map_raw_approval_to_plan_decision,
    plan_todos_fingerprint,
    pending_widget_deadline,
    resolve_auto_approved_shell_commands,
    should_cancel_detached_placeholder,
    user_is_typing,
)
from invincat_cli.app_runtime.plan import (
    build_plan_text,
    build_plan_handoff_prompt,
    build_planner_system_prompt,
    build_planner_turn_input,
    extract_latest_ai_text,
    extract_todos_from_state,
    latest_ai_text_after_latest_tool,
    normalize_state_messages,
    planner_turn_approve_plan_decision,
    planner_turn_has_write_todos,
)
from invincat_cli.app_runtime.memory import (
    AUTO_OFFLOAD_COOLDOWN_SECONDS,
    AUTO_OFFLOAD_THRESHOLD,
    build_auto_offload_message,
    build_offload_budget_cache_key,
    build_offload_success_message,
    build_offload_threshold_not_met_message,
    format_memory_update_success,
    resolve_auto_offload_decision,
    resolve_memory_update_notification,
)
from invincat_cli.app_runtime.model_args import (
    split_model_spec,
)
from invincat_cli.app_runtime.model_command import MODEL_DEFAULT_USAGE, parse_model_command
from invincat_cli.app_runtime.model_runtime import (
    choose_default_model_clear_fn,
    choose_default_model_save_fn,
    is_target_already_using,
    missing_credentials_detail,
    model_target_translation_key,
    normalize_default_model_spec,
    resolve_model_spec,
)
from invincat_cli.app_runtime.queueing import can_bypass_busy_queue
from invincat_cli.app_runtime.reload import build_reload_report
from invincat_cli.app_runtime.scheduler import (
    active_scheduled_task_id,
    remove_scheduled_messages,
    resolve_scheduled_wecom_file_path,
    scheduled_run_matches,
)
from invincat_cli.app_runtime.agent import (
    AgentThreadOverrideContext,
    AgentTurnRequest,
    build_agent_cli_context,
    build_agent_error_detail,
    can_start_agent_turn,
    is_current_agent_generation,
    is_planner_agent_turn,
    queued_scheduled_run_state,
    resolve_wecom_file_request_handler,
    should_clear_scheduled_run_before_send,
    should_continue_after_deferred_actions,
    should_route_message_to_planner,
    should_retry_scheduled_turn,
)
from invincat_cli.app_runtime.services import AppServices
from invincat_cli.app_runtime.server import (
    count_mcp_tools,
    normalize_server_start_error,
    resolve_mcp_preload_result,
    resolve_most_recent_agent_filter,
    resolve_no_recent_threads_notice,
    resolve_thread_not_found_notice,
    should_drain_deferred_on_server_ready,
    should_drain_queue_on_server_ready,
    should_update_default_agent_from_thread,
)
from invincat_cli.app_runtime.shell import (
    format_shell_output,
    is_interactive_command,
    shell_termination_strategy,
    should_start_new_shell_session,
)
from invincat_cli.app_runtime.skill import (
    build_skill_agent_metadata,
    build_skill_invocation_prompt,
    discover_skills_and_roots,
    find_skill,
)
from invincat_cli.app_runtime.startup import (
    build_startup_slash_commands,
    create_startup_session_state,
    resolve_memory_status_model,
    resolve_startup_followup,
    resolve_startup_model_overrides,
)
from invincat_cli.app_runtime.theme_prefs import (
    load_theme_preference,
    save_theme_preference,
)
from invincat_cli.app_runtime.tokens import build_tokens_message
from invincat_cli.app_runtime.thread_history import (
    build_resume_summary,
    merge_thread_state_with_fallback,
    thread_history_payload_from_state_values,
)
from invincat_cli.app_runtime.thread_links import build_thread_message
from invincat_cli.app_runtime.thread_runtime import (
    thread_loading_status,
    thread_resume_block_message_key,
    thread_resume_block_reason,
    thread_switch_failed_message,
)
from invincat_cli.app_runtime.version import resolve_version_message
from invincat_cli.app_runtime.wecom import (
    WeComTurnContext,
    create_wecom_message_responder,
    load_wecom_bot_config,
    should_clear_wecom_bridge,
    wecom_bot_already_running_message,
    wecom_bot_is_running,
    wecom_bot_missing_config_message,
    wecom_bot_started_message,
    wecom_bot_status_message,
    wecom_bot_stopped_message,
    wecom_bot_usage_message,
    wecom_bridge_is_online,
    wecom_bridge_offline_error,
    wecom_bridge_offline_message,
    wecom_turn_is_busy,
)
from invincat_cli.core.debug import configure_debug_logging
from invincat_cli.core.session_stats import (
    SessionStats,
    SpinnerStatus,
)
from invincat_cli.i18n import t

# Only is_ascii_mode is needed before first paint (on_mount scrollbar config).
# All other config imports — settings, create_model, detect_provider, etc. — are
# deferred to local imports at their call sites since they are only accessed
# after user interaction begins.
from invincat_cli.core.version import CHANGELOG_URL, DOCS_URL
from invincat_cli.config import is_ascii_mode
from invincat_cli.widgets.chat_input import ChatInput
from invincat_cli.widgets.loading import LoadingWidget
from invincat_cli.widgets.message_store import (
    MessageData,
    MessageStore,
)
from invincat_cli.widgets.messages import (
    AppMessage,
    AssistantMessage,
    ErrorMessage,
    QueuedUserMessage,
    SkillMessage,
    ToolCallMessage,
    UserMessage,
)
from invincat_cli.widgets.status import StatusBar
from invincat_cli.widgets.welcome import WelcomeBanner
from invincat_cli.wecom.bridge import WeComBridge
from invincat_cli.wecom.media import (
    build_wecom_agent_input_with_media_downloads,
)
from invincat_cli.wecom.session import (
    WECOM_AGENT_TIMEOUT,
)
from invincat_cli.wecom.turn import WeComTurnRunner

logger = logging.getLogger(__name__)
configure_debug_logging(logger)
_monotonic = time.monotonic

_SCHEDULED_TRANSIENT_RETRY_DELAY_SECONDS = 3.0


def _wecom_daemon_claims_scheduled_task(task: Any, cwd: str | Path) -> bool:
    """Return True when a running WeCom daemon should own this scheduled task."""
    from invincat_cli.scheduler.delivery import scheduled_task_wecom_chatid
    from invincat_cli.wecom.daemon import is_daemon_running

    cwd_str = str(cwd)
    if getattr(task, "cwd", None) != cwd_str:
        return False
    if not scheduled_task_wecom_chatid(task):
        return False
    return is_daemon_running(Path(cwd_str))


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from deepagents.backends import CompositeBackend
    from langchain_core.language_models import BaseChatModel
    from langchain_core.runnables import RunnableConfig
    from langgraph.pregel import Pregel
    from textual.app import ComposeResult
    from textual.events import Click, MouseUp, Paste
    from textual.scrollbar import ScrollTo, ScrollUp
    from textual.widget import Widget
    from textual.worker import Worker

    from invincat_cli.core.ask_user_types import AskUserWidgetResult, Question
    from invincat_cli.mcp.tools import MCPServerInfo
    from invincat_cli.remote_client import RemoteAgent
    from invincat_cli.server.app_server import ServerProcess
    from invincat_cli.skills.load import ExtendedSkillMetadata
    from invincat_cli.textual_adapter import TextualUIAdapter
    from invincat_cli.widgets.approval import ApprovalMenu
    from invincat_cli.widgets.ask_user import AskUserMenu

# iTerm2 Cursor Guide Workaround
# ===============================
# iTerm2's cursor guide (highlight cursor line) causes visual artifacts when
# Textual takes over the terminal in alternate screen mode. We disable it at
# module load and restore on exit. Both atexit and exit() override are used
# for defense-in-depth: atexit catches abnormal termination (SIGTERM, unhandled
# exceptions), while exit() ensures restoration before Textual's cleanup.

# Detection: check env vars AND that stderr is a TTY (avoids false positives
# when env vars are inherited but running in non-TTY context like CI)
_IS_ITERM = (
    (
        os.environ.get("LC_TERMINAL", "") == "iTerm2"
        or os.environ.get("TERM_PROGRAM", "") == "iTerm.app"
    )
    and hasattr(os, "isatty")
    and os.isatty(2)
)

# iTerm2 cursor guide escape sequences (OSC 1337)
# Format: OSC 1337 ; HighlightCursorLine=<yes|no> ST
# Where OSC = ESC ] (0x1b 0x5d) and ST = ESC \ (0x1b 0x5c)
_ITERM_CURSOR_GUIDE_OFF = "\x1b]1337;HighlightCursorLine=no\x1b\\"
_ITERM_CURSOR_GUIDE_ON = "\x1b]1337;HighlightCursorLine=yes\x1b\\"


def _write_iterm_escape(sequence: str) -> None:
    """Write an iTerm2 escape sequence to stderr.

    Silently fails if the terminal is unavailable (redirected, closed, broken
    pipe). This is a cosmetic feature, so failures should never crash the app.
    """
    if not _IS_ITERM:
        return
    try:
        import sys

        if sys.__stderr__ is not None:
            sys.__stderr__.write(sequence)
            sys.__stderr__.flush()
    except OSError:
        # Terminal may be unavailable (redirected, closed, broken pipe)
        pass


# Disable cursor guide at module load (before Textual takes over)
_write_iterm_escape(_ITERM_CURSOR_GUIDE_OFF)

if _IS_ITERM:
    import atexit

    def _restore_cursor_guide() -> None:
        """Restore iTerm2 cursor guide on exit.

        Registered with atexit to ensure the cursor guide is re-enabled
        when the CLI exits, regardless of how the exit occurs.
        """
        _write_iterm_escape(_ITERM_CURSOR_GUIDE_ON)

    atexit.register(_restore_cursor_guide)


_COMMAND_URLS: dict[str, str] = {
    "/changelog": CHANGELOG_URL,
    "/docs": DOCS_URL,
    "/feedback": "https://github.com/langchain-ai/deepagents/issues/new/choose",
}


"""Slash-command to URL mapping for commands that just open a browser."""


class DeepAgentsApp(App):
    """Main Textual application for deepagents-cli."""

    TITLE = "Deep Agents"
    """Textual application title."""

    CSS_PATH = "app.tcss"
    """Path to the Textual CSS stylesheet for the app layout."""

    ENABLE_COMMAND_PALETTE = False
    """Disable Textual's built-in command palette in favor of the custom slash
    command system."""

    SCROLL_SENSITIVITY_Y = 1.0
    """Vertical scroll speed (reduced from Textual default for finer control)."""

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("escape", "interrupt", "Interrupt", show=False, priority=True),
        Binding(
            "ctrl+c",
            "quit_or_interrupt",
            "Quit/Interrupt",
            show=False,
            priority=True,
        ),
        Binding("ctrl+d", "quit_app", "Quit", show=False, priority=True),
        Binding("ctrl+t", "toggle_auto_approve", "Toggle Auto-Approve", show=False),
        Binding(
            "shift+tab",
            "toggle_auto_approve",
            "Toggle Auto-Approve",
            show=False,
            priority=True,
        ),
        Binding(
            "ctrl+o",
            "toggle_tool_output",
            "Toggle Tool Output",
            show=False,
            priority=True,
        ),
        Binding(
            "ctrl+x",
            "open_editor",
            "Open Editor",
            show=False,
            priority=True,
        ),
        # Approval menu keys (handled at App level for reliability)
        Binding("up", "approval_up", "Up", show=False),
        Binding("k", "approval_up", "Up", show=False),
        Binding("down", "approval_down", "Down", show=False),
        Binding("j", "approval_down", "Down", show=False),
        Binding("enter", "approval_select", "Select", show=False),
        Binding("y", "approval_yes", "Yes", show=False),
        Binding("1", "approval_yes", "Yes", show=False),
        Binding("2", "approval_auto", "Auto", show=False),
        Binding("a", "approval_auto", "Auto", show=False),
        Binding("3", "approval_no", "No", show=False),
        Binding("n", "approval_no", "No", show=False),
    ]
    """App-level keybindings for interrupt, quit, toggles, and approval menu
    navigation."""

    class ServerReady(Message):
        """Posted by the background server-startup worker on success."""

        def __init__(  # noqa: D107
            self,
            agent: Any,  # noqa: ANN401
            server_proc: Any,  # noqa: ANN401
            mcp_server_info: list[Any] | None,
            model: BaseChatModel | None = None,
        ) -> None:
            super().__init__()
            self.agent = agent
            self.server_proc = server_proc
            self.mcp_server_info = mcp_server_info
            self.model = model

    class ServerStartFailed(Message):
        """Posted by the background server-startup worker on failure."""

        def __init__(self, error: Exception) -> None:  # noqa: D107
            super().__init__()
            self.error = error

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

        self._register_custom_themes()

        from invincat_cli.i18n import load_language_from_config, set_language

        language = load_language_from_config()
        set_language(language)

        self.theme = load_theme_preference()

        self._agent = agent

        self._assistant_id = assistant_id

        self._backend = backend

        self._auto_approve = auto_approve

        self._cwd = str(cwd) if cwd else str(Path.cwd())

        self._lc_thread_id = thread_id
        """LangChain thread identifier.

        Named `_lc_thread_id` to avoid collision with Textual's `App._thread_id`.
        """

        self._resume_thread_intent = resume_thread

        self._initial_prompt = initial_prompt

        self._mcp_server_info = mcp_server_info

        self._profile_override = profile_override

        self._server_proc = server_proc

        self._server_kwargs = server_kwargs

        self._mcp_preload_kwargs = mcp_preload_kwargs

        self._model_kwargs = model_kwargs

        self._defer_server_start = defer_server_start

        self._services = services or AppServices()

        self._connecting = server_kwargs is not None and not defer_server_start
        # Extract sandbox type from server kwargs for trace metadata.
        # ServerConfig.__post_init__ normalizes "none" → None, but server_kwargs carries
        # the raw argparse value, so guard against both.

        raw = (server_kwargs or {}).get("sandbox_type")

        self._sandbox_type: str | None = raw if raw and raw != "none" else None

        self._model_override: str | None = None

        self._model_params_override: dict[str, Any] | None = None

        self._memory_model_override: str | None = None

        self._memory_model_params_override: dict[str, Any] | None = None

        self._model: BaseChatModel | None = None

        self._mcp_tool_count = sum(len(s.tools) for s in (mcp_server_info or []))

        self._status_bar: StatusBar | None = None

        self._chat_input: ChatInput | None = None

        self._quit_pending = False

        self._session_state: TextualSessionState | None = None

        self._ui_adapter: TextualUIAdapter | None = None

        self._pending_approval_widget: ApprovalMenu | None = None

        self._pending_ask_user_widget: AskUserMenu | None = None
        # Agent task tracking for interruption

        self._agent_worker: Worker[None] | None = None

        self._agent_running = False
        self._active_turn_is_planner = False

        self._agent_generation: int = 0
        """Monotonically-increasing counter incremented each time a new agent task starts.

        Used by _cleanup_agent_task() to guard against stale cleanup from a
        previously-cancelled worker clobbering the running flags of a newer
        concurrent worker.  When _cancel_worker() eagerly clears _agent_running
        so the user can send a new message immediately, the old (shielded)
        cleanup goroutine can still be alive. Without the generation check it
        would reset _agent_running=False / _agent_worker=None for the NEW agent.
        """

        self._shell_process: asyncio.subprocess.Process | None = None
        """Shell command process tracking for interruption (! commands)."""

        self._shell_worker: Worker[None] | None = None

        self._shell_running = False

        self._loading_widget: LoadingWidget | None = None
        self._memory_status_clear_timer: Any | None = None
        self._planner_agent: Pregel | None = None
        self._planner_thread_id: str | None = None
        self._main_thread_before_plan: str | None = None
        self._planner_last_todos_fingerprint: str | None = None
        self._planner_prompted_todos_fingerprint: str | None = None

        self._context_tokens: int = 0
        """Local cache of the last total-context token count.

        Source of truth is `_context_tokens` in graph state; this is a sync
        copy for the status bar.
        """

        self._tokens_approximate: bool = False
        """Whether the cached token count is stale (interrupted generation)."""

        self._auto_offload_cooldown_until: float = 0.0
        """Monotonic timestamp before which auto-offload must not fire again."""

        self._offload_budget_cache: tuple[tuple[Any, ...], str | None] | None = None
        """(cache_key, result) for `_resolve_offload_budget_str`."""

        self._last_typed_at: float | None = None
        """Typing-aware approval deferral state."""

        self._approval_placeholder: Static | None = None

        self._update_available: tuple[bool, str | None] = (False, None)
        """Update availability state — set by _check_for_updates, read on exit."""

        self._session_stats: SessionStats = SessionStats()
        """Cumulative usage stats across all turns in this session."""

        self._inflight_turn_stats: SessionStats | None = None
        """Stats for the currently executing turn.

        Held here so `exit()` can merge them synchronously before the event loop
        tears down (e.g. `Ctrl+D` during a pending tool call).
        """

        self._inflight_turn_start: float = 0.0
        """Monotonic timestamp when the current turn started."""

        self._pending_messages: deque[QueuedMessage] = deque()
        """User message queue for sequential processing."""

        self._queued_widgets: deque[QueuedUserMessage] = deque()

        self._processing_pending = False

        self._thread_switching = False

        self._model_switching = False

        self._deferred_actions: list[DeferredAction] = []
        """Deferred actions executed after the current busy state resolves."""
        self._pending_plan_handoff_prompt: str | None = None
        """Approved plan handoff prompt waiting to run on the main agent."""

        self._message_store = MessageStore()
        """Message virtualization store."""

        self._startup_task: asyncio.Task[None] | None = None
        """Startup task reference (set in on_mount)."""

        self._discovered_skills: list[ExtendedSkillMetadata] = []
        """Cached skill metadata (populated by startup discovery worker,
        refreshed on `/reload`).

        Used by `_handle_skill_command` to skip re-walking all skill directories
        on every invocation.
        """

        self._skill_allowed_roots: list[Path] = []
        """Pre-resolved skill root directories for containment checks in
        `load_skill_content`.

        Built alongside `_discovered_skills`.
        """

        # Lazily imported here to avoid pulling image dependencies into
        # argument parsing paths.
        from invincat_cli.io.input import MediaTracker

        self._image_tracker = MediaTracker()
        self._wecom_task: asyncio.Task[None] | None = None
        self._wecom_bridge: WeComBridge | None = None
        self._wecom_lock = asyncio.Lock()
        self._current_wecom_inbound_frame: dict[str, Any] | None = None

        from invincat_cli.scheduler.runner import SchedulerRunner

        self._scheduler_store = self._services.lazy_scheduler_store()
        self._scheduler_runner: SchedulerRunner | None = None
        self._scheduler_interval_handle: Any | None = None
        self._active_scheduled_run: tuple[str, str] | None = None  # (run_id, task_id)
        self._scheduled_run_message_offset: int = 0  # message count before this scheduled turn started
        self._scheduled_turn_status: str = "success"
        self._scheduled_turn_error: str | None = None
        self._scheduled_turn_retry_used: bool = False

    def _remote_agent(self) -> RemoteAgent | None:
        """Return the agent narrowed to `RemoteAgent`, or `None`.

        Returns `None` when:

        - No agent is configured (`self._agent is None`).
        - The agent is a local `Pregel` graph (e.g. ACP mode, test harnesses).

        Used to gate features that require a server-backed agent (e.g. model
        switching via `ConfigurableModelMiddleware`, checkpointer fallback).
        Checks the agent type rather than server ownership so this works for
        both CLI-spawned servers and externally managed ones.

        Returns:
            The `RemoteAgent` instance, or `None` for local agents.
        """
        from invincat_cli.remote_client import RemoteAgent

        return self._agent if isinstance(self._agent, RemoteAgent) else None

    def get_theme_variable_defaults(self) -> dict[str, str]:
        """Return custom CSS variable defaults for the current theme.

        Most styling uses Textual's built-in variables (`$primary`,
        `$text-muted`, `$error-muted`, etc.).  This override injects the
        app-specific variables (`$mode-bash`, `$mode-command`, `$skill`,
        `$skill-hover`, `$tool`, `$tool-hover`) that have no Textual equivalent.

        Returns:
            Dict of CSS variable names to hex color values.
        """
        colors = theme.get_theme_colors(self)
        return theme.get_css_variable_defaults(colors=colors)

    def compose(self) -> ComposeResult:
        """Compose the application layout.

        Yields:
            UI components for the main chat area and status bar.
        """
        # Main chat area with scrollable messages
        # VerticalScroll tracks user scroll intent for better auto-scroll behavior
        with VerticalScroll(id="chat"):
            yield WelcomeBanner(
                thread_id=self._lc_thread_id,
                mcp_tool_count=self._mcp_tool_count,
                connecting=self._connecting,
                resuming=self._resume_thread_intent is not None,
                local_server=self._server_kwargs is not None,
                id="welcome-banner",
            )
            yield Container(id="messages")
        with Container(id="bottom-app-container"):
            yield ChatInput(
                cwd=self._cwd,
                image_tracker=self._image_tracker,
                id="input-area",
            )

        # Status bar at bottom
        yield StatusBar(cwd=self._cwd, id="status-bar")

    async def on_mount(self) -> None:
        """Initialize components after mount.

        Only widget queries and lightweight config go here — anything that
        would delay the first rendered frame (subprocess calls, heavy
        imports) is deferred to `_post_paint_init` via `call_after_refresh`.
        """
        # Move all objects allocated during import/compose into the permanent
        # generation so the cyclic GC skips them during first-paint rendering.
        import gc

        gc.freeze()

        chat = self.query_one("#chat", VerticalScroll)
        chat.anchor()
        if is_ascii_mode():
            chat.styles.scrollbar_size_vertical = 0

        from invincat_cli.config import _get_default_memory_model_spec, settings
        from invincat_cli.model_config import get_target_model_params

        startup_overrides = resolve_startup_model_overrides(
            memory_model_override=self._memory_model_override,
            memory_model_params_override=self._memory_model_params_override,
            model_params_override=self._model_params_override,
            model_provider=settings.model_provider,
            model_name=settings.model_name,
            get_default_memory_model_spec=_get_default_memory_model_spec,
            get_target_model_params=get_target_model_params,
        )
        self._memory_model_override = startup_overrides.memory_model
        self._model_params_override = startup_overrides.primary_params
        self._memory_model_params_override = startup_overrides.memory_params

        self._status_bar = self.query_one("#status-bar", StatusBar)
        self._chat_input = self.query_one("#input-area", ChatInput)
        if self._status_bar:
            memory_status_model = resolve_memory_status_model(
                memory_model_override=self._memory_model_override,
                model_provider=settings.model_provider,
                model_name=settings.model_name,
                split_model_spec=split_model_spec,
            )
            self._status_bar.set_memory_model(
                provider=memory_status_model.provider,
                model=memory_status_model.model,
                follow_primary=memory_status_model.follow_primary,
            )

        # Apply slash commands with current language
        from invincat_cli.command_registry import COMMANDS, build_skill_commands

        self._chat_input.update_slash_commands(
            build_startup_slash_commands(
                commands=COMMANDS,
                discovered_skills=self._discovered_skills,
                build_skill_commands=build_skill_commands,
            )
        )

        # Set initial auto-approve state
        if self._auto_approve:
            self._status_bar.set_auto_approve(enabled=True)

        # Focus the input immediately so the cursor is visible on first paint
        self._chat_input.focus_input()

        # Prewarm heavy imports in a thread while the first frame renders.
        # The user can't type yet, so GIL contention is harmless.  By the
        # time _post_paint_init fires its inline imports are dict lookups.
        self.run_worker(
            asyncio.to_thread(self._prewarm_deferred_imports),
            exclusive=True,
            group="startup-import-prewarm",
        )

        # Start branch resolution immediately — the thread launches now
        # (during on_mount) so by the time the first frame finishes painting
        # the subprocess is already done. _post_paint_init fires the heavier
        # workers (server, model creation) afterward.
        self._startup_task = asyncio.create_task(
            self._resolve_git_branch_and_continue()
        )

    async def _resolve_git_branch_and_continue(self) -> None:
        """Resolve git branch, then schedule remaining init workers.

        Launched via `asyncio.create_task()` during `on_mount` so the subprocess
        runs concurrently with first-paint rendering. `_post_paint_init` is
        scheduled via `call_after_refresh` regardless of whether branch
        resolution succeeds.
        """
        try:
            import subprocess  # noqa: S404  # stdlib, already loaded

            def _get_branch() -> str:
                try:
                    result = subprocess.run(
                        ["git", "rev-parse", "--abbrev-ref", "HEAD"],  # noqa: S607
                        capture_output=True,
                        text=True,
                        timeout=2,
                        check=False,
                    )
                    if result.returncode == 0:
                        return result.stdout.strip()
                except FileNotFoundError:
                    pass  # git not installed
                except subprocess.TimeoutExpired:
                    logger.debug("Git branch detection timed out")
                except OSError:
                    logger.debug("Git branch detection failed", exc_info=True)
                return ""

            branch = await asyncio.to_thread(_get_branch)
            if self._status_bar:
                self._status_bar.branch = branch
        except Exception:
            logger.warning("Git branch resolution failed", exc_info=True)
        finally:
            # Always schedule post-paint init — even if branch resolution
            # fails, the app must still start the server, session, etc.
            self.call_after_refresh(self._post_paint_init)

    async def _post_paint_init(self) -> None:
        """Fire background workers for remaining startup work.

        Everything here is non-blocking: workers and thread-offloaded calls
        so the UI stays responsive.
        """
        # Create UI adapter unconditionally — it only holds UI callbacks and
        # doesn't depend on the agent. The agent is injected later at
        # execute_task_textual() call time.
        from invincat_cli.textual_adapter import TextualUIAdapter

        self._ui_adapter = TextualUIAdapter(
            mount_message=self._mount_message,
            update_status=self._update_status,
            request_approval=self._request_approval,
            on_auto_approve_enabled=self._on_auto_approve_enabled,
            set_spinner=self._set_spinner,
            set_active_message=self._set_active_message,
            sync_message_content=self._sync_message_content,
            request_ask_user=self._request_ask_user,
            request_approve_plan=self._request_approve_plan,
        )
        # Wire token display callbacks
        self._ui_adapter._on_tokens_update = self._on_tokens_update
        self._ui_adapter._on_tokens_hide = self._hide_tokens
        self._ui_adapter._on_tokens_show = self._show_tokens
        # Wire message store for updating tool messages after pruning
        self._ui_adapter.set_message_store(self._message_store)

        # Fire-and-forget workers — none of these block the event loop.

        # Discover skills first so /skill: autocomplete is ready as early
        # as possible. The heavy filesystem scan runs in a thread.
        self.run_worker(
            self._discover_skills(),
            exclusive=True,
            group="startup-skill-discovery",
        )

        self.run_worker(self._init_session_state, exclusive=True, group="session-init")

        # Server startup (model creation + server process)
        if self._server_kwargs is not None and not self._defer_server_start:
            self.run_worker(
                self._start_server_background,
                exclusive=True,
                group="server-startup",
            )

        # Background update check and what's-new banner
        # (opt-out via env var or config.toml [update].check)
        # 暂时屏蔽自动更新机制
        # if is_update_check_enabled():
        #     self.run_worker(
        #         self._check_for_updates,
        #         exclusive=True,
        #         group="startup-update-check",
        #     )
        #     self.run_worker(
        #         self._show_whats_new,
        #         exclusive=True,
        #         group="startup-whats-new",
        #     )

        # Prewarm model discovery and profile caches unconditionally so
        # /model opens instantly even before the agent/server is ready.
        self.run_worker(
            self._prewarm_model_caches,
            exclusive=True,
            group="startup-model-prewarm",
        )

        # Prewarm thread message counts so /threads opens instantly.
        self.run_worker(
            self._prewarm_threads_cache,
            exclusive=True,
            group="startup-thread-prewarm",
        )

        # Optional tool warnings in a thread (shutil.which is sync I/O)
        self.run_worker(
            self._check_optional_tools_background,
            exclusive=True,
            group="startup-tool-check",
        )

        # Start scheduler runner — checks for due tasks every 60 seconds.
        self._start_scheduler()

        # Auto-submit initial prompt if provided via -m flag.
        # This check must come first because _lc_thread_id and _agent are
        # always set (even for brand-new sessions), so an elif after the
        # thread-history branch would never execute.
        # When connecting, defer until on_deep_agents_app_server_ready fires.
        followup = resolve_startup_followup(
            connecting=self._connecting,
            initial_prompt=self._initial_prompt,
            thread_id=self._lc_thread_id,
            agent=self._agent,
        )
        if followup and followup.kind == "submit_prompt" and followup.prompt is not None:
            self.call_after_refresh(
                lambda: asyncio.create_task(self._handle_user_message(followup.prompt))
            )
        elif followup and followup.kind == "load_history":
            self.call_after_refresh(
                lambda: asyncio.create_task(self._load_thread_history())
            )

    async def _init_session_state(self) -> None:
        """Create session state in a thread (imports deepagents_cli.sessions)."""

        try:
            self._session_state = await asyncio.to_thread(
                create_startup_session_state,
                auto_approve=self._auto_approve,
                thread_id=self._lc_thread_id,
            )
        except Exception:
            logger.exception("Failed to create session state")
            self.notify(
                t("app.session_init_failed"),
                severity="error",
                timeout=10,
            )

    async def _check_optional_tools_background(self) -> None:
        """Check for optional tools in a thread and notify if missing."""
        try:
            from invincat_cli.main import (
                check_optional_tools,
                format_tool_warning_tui,
            )
        except ImportError:
            logger.warning(
                "Could not import optional tools checker",
                exc_info=True,
            )
            return

        try:
            missing = await asyncio.to_thread(check_optional_tools)
        except (OSError, FileNotFoundError):
            logger.debug("Failed to check for optional tools", exc_info=True)
            return
        except Exception:
            logger.warning("Unexpected error checking optional tools", exc_info=True)
            return

        for tool in missing:
            self.notify(
                format_tool_warning_tui(tool),
                severity="warning",
                timeout=15,
                markup=False,
            )

    async def _discover_skills(self) -> None:
        """Discover skills, cache metadata, and update autocomplete.

        Caches the full `ExtendedSkillMetadata` list and pre-resolved
        containment roots so that `/skill:<name>` invocations can skip
        re-walking every skill directory.

        Runs filesystem I/O in a thread to avoid blocking the event loop.
        """
        from invincat_cli.command_registry import SLASH_COMMANDS, build_skill_commands

        try:
            skills, roots = await asyncio.to_thread(self._discover_skills_and_roots)
            self._discovered_skills = skills
            self._skill_allowed_roots = roots
        except OSError:
            # Clear stale cache so /reload failures don't silently
            # leave old data in place.
            self._discovered_skills = []
            self._skill_allowed_roots = []
            logger.warning(
                "Filesystem error during skill discovery",
                exc_info=True,
            )
            self.notify(
                t("app.skill_scan_failed"),
                severity="warning",
                timeout=6,
                markup=False,
            )
        except Exception:
            self._discovered_skills = []
            self._skill_allowed_roots = []
            logger.exception("Unexpected error during skill discovery")
            self.notify(
                t("app.skill_discovery_failed"),
                severity="warning",
                timeout=8,
                markup=False,
            )
        if self._chat_input:
            skill_commands = build_skill_commands(self._discovered_skills)
            merged = list(SLASH_COMMANDS) + skill_commands
            self._chat_input.update_slash_commands(merged)
        else:
            logger.debug(
                "Skill discovery completed (%d skills) but chat input "
                "not yet mounted; autocomplete deferred",
                len(self._discovered_skills),
            )

    def _discover_skills_and_roots(
        self,
    ) -> tuple[list[ExtendedSkillMetadata], list[Path]]:
        """Discover skills and build pre-resolved containment roots.

        Shared by `_discover_skills` (startup/reload) and the cache-miss
        fallback in `_handle_skill_command` to avoid duplicating the
        `list_skills` call and root-resolution logic.

        Returns:
            Tuple of `(skill metadata list, pre-resolved containment roots)`.
        """
        from invincat_cli.config import settings

        assistant_id = self._assistant_id or "agent"
        return discover_skills_and_roots(settings=settings, assistant_id=assistant_id)

    async def _resolve_resume_thread(self) -> None:
        """Resolve a `-r` resume intent into a concrete thread ID.

        Consumes `self._resume_thread_intent` and resolves it into a concrete
        thread ID. Mutates `self._lc_thread_id` and optionally
        `self._assistant_id` / `self._server_kwargs`. Falls back to a fresh
        thread on any DB error.
        """
        from invincat_cli.sessions import (
            find_similar_threads,
            generate_thread_id,
            get_most_recent,
            get_thread_agent,
            thread_exists,
        )

        resume = self._resume_thread_intent
        self._resume_thread_intent = None  # consumed

        if not resume:
            return

        try:
            if resume == "__MOST_RECENT__":
                agent_filter = resolve_most_recent_agent_filter(
                    assistant_id=self._assistant_id
                )
                thread_id = await get_most_recent(agent_filter)
                if thread_id:
                    agent_name = await get_thread_agent(thread_id)
                    if agent_name:
                        self._assistant_id = agent_name
                        if self._server_kwargs:
                            self._server_kwargs["assistant_id"] = agent_name
                    self._lc_thread_id = thread_id
                else:
                    self._lc_thread_id = generate_thread_id()
                    notice = resolve_no_recent_threads_notice(agent_filter)
                    msg = t(notice.key, **notice.params)
                    self.notify(msg, severity="warning", markup=False)
            elif await thread_exists(resume):
                self._lc_thread_id = resume
                if should_update_default_agent_from_thread(
                    assistant_id=self._assistant_id
                ):
                    agent_name = await get_thread_agent(resume)
                    if agent_name:
                        self._assistant_id = agent_name
                        if self._server_kwargs:
                            self._server_kwargs["assistant_id"] = agent_name
            else:
                # Thread not found — notify + fall back to new thread
                self._lc_thread_id = generate_thread_id()
                similar = await find_similar_threads(resume)
                notice = resolve_thread_not_found_notice(
                    thread_id=resume,
                    similar=similar,
                )
                hint = t(notice.key, **notice.params)
                self.notify(hint, severity="warning", timeout=6, markup=False)
        except Exception:
            logger.exception("Failed to resolve resume thread %r", resume)
            self._lc_thread_id = generate_thread_id()
            self.notify(
                t("app.thread_lookup_failed"),
                severity="warning",
            )

        # Update session state if ready (may still be initializing in a
        # concurrent worker)
        if self._session_state:
            self._session_state.thread_id = self._lc_thread_id

    async def _start_server_background(self) -> None:
        """Background worker: resolve resume-thread intent, start server + MCP preload.

        Also runs deferred model creation if `model_kwargs` was provided,
        so the langchain import + init doesn't block first paint.
        """
        # Phase 1: Resolve resume thread (if any) before server startup
        if self._resume_thread_intent:
            await self._resolve_resume_thread()

        # Run deferred model creation. settings.model_name / model_provider
        # are already set eagerly for the status bar display; this call
        # does the heavy langchain import + SDK init and may refine them
        # (e.g., context_limit from the model profile).
        model_instance: BaseChatModel | None = None
        if self._model_kwargs is not None:
            from invincat_cli.config import create_model
            from invincat_cli.model_config import ModelConfigError, save_recent_model

            try:
                result = create_model(**self._model_kwargs)
            except ModelConfigError as exc:
                self.post_message(self.ServerStartFailed(error=exc))
                return
            result.apply_to_settings()
            save_recent_model(f"{result.provider}:{result.model_name}")
            model_instance = result.model
            self._model_kwargs = None  # consumed

        from invincat_cli.server.manager import start_server_and_get_agent

        coros: list[Any] = [start_server_and_get_agent(**self._server_kwargs)]  # type: ignore[arg-type]

        if self._mcp_preload_kwargs is not None:
            from invincat_cli.main import _preload_session_mcp_server_info

            coros.append(_preload_session_mcp_server_info(**self._mcp_preload_kwargs))

        try:
            results = await asyncio.gather(*coros, return_exceptions=True)
        except Exception as exc:  # noqa: BLE001  # defensive catch around gather
            self.post_message(self.ServerStartFailed(error=exc))
            return

        server_result = results[0]
        server_error = normalize_server_start_error(server_result)
        if server_error is not None:
            self.post_message(self.ServerStartFailed(error=server_error))
            return

        agent, server_proc, _ = server_result

        # Assign immediately so the finally block in run_textual_app can
        # clean up the server even if the ServerReady message is never
        # processed (e.g. user quits during startup).
        self._server_proc = server_proc

        mcp_preload = resolve_mcp_preload_result(results)
        if mcp_preload.error is not None:
            logger.warning(
                "MCP metadata preload failed: %s",
                mcp_preload.error,
                exc_info=mcp_preload.error,
            )

        self.post_message(
            self.ServerReady(
                agent=agent,
                server_proc=server_proc,
                mcp_server_info=mcp_preload.info,
                model=model_instance,
            )
        )

    def on_deep_agents_app_server_ready(self, event: ServerReady) -> None:
        """Handle successful background server startup."""
        self._connecting = False
        self._agent = event.agent
        self._server_proc = event.server_proc
        self._mcp_server_info = event.mcp_server_info
        self._mcp_tool_count = count_mcp_tools(event.mcp_server_info)
        if event.model is not None:
            self._model = event.model

        # Update welcome banner to show ready state
        try:
            banner = self.query_one("#welcome-banner", WelcomeBanner)
            banner.set_connected(self._mcp_tool_count)
        except NoMatches:
            logger.warning("Welcome banner not found during server ready transition")

        # Handle deferred initial prompt or thread history
        followup = resolve_startup_followup(
            connecting=self._connecting,
            initial_prompt=self._initial_prompt,
            thread_id=self._lc_thread_id,
            agent=self._agent,
        )
        if followup and followup.kind == "submit_prompt" and followup.prompt is not None:
            self.call_after_refresh(
                lambda: asyncio.create_task(self._handle_user_message(followup.prompt))
            )
        elif followup and followup.kind == "load_history":
            self.call_after_refresh(
                lambda: asyncio.create_task(self._load_thread_history())
            )

        # Drain deferred actions (e.g. model/thread switch queued during connection)
        # if the agent is not actively running. Wrapped in a helper so that
        # exceptions are logged rather than becoming unhandled task errors.
        if should_drain_deferred_on_server_ready(
            deferred_action_count=len(self._deferred_actions),
            agent_running=self._agent_running,
        ):

            async def _safe_drain() -> None:
                try:
                    await self._maybe_drain_deferred()
                except Exception:
                    logger.exception("Unhandled error while draining deferred actions")
                    with suppress(Exception):
                        await self._mount_message(
                            ErrorMessage(
                                "A deferred action failed during startup. "
                                "You may need to retry the operation."
                            )
                        )

            self.call_after_refresh(lambda: asyncio.create_task(_safe_drain()))

        # Drain any messages the user typed while the server was starting.
        # (If an initial prompt exists, its cleanup path will drain the queue.)
        if should_drain_queue_on_server_ready(
            pending_message_count=len(self._pending_messages),
            initial_prompt=self._initial_prompt,
        ):
            self.call_after_refresh(
                lambda: asyncio.create_task(self._process_next_from_queue())
            )

    def on_deep_agents_app_server_start_failed(self, event: ServerStartFailed) -> None:
        """Handle background server startup failure."""
        self._connecting = False
        logger.error("Server startup failed: %s", event.error, exc_info=event.error)
        # Update banner to show persistent failure state
        try:
            banner = self.query_one("#welcome-banner", WelcomeBanner)
            banner.set_failed(str(event.error))
        except NoMatches:
            logger.warning("Welcome banner not found during server failure transition")

        # Discard any messages queued while the server was starting
        if self._pending_messages:
            self._pending_messages.clear()
            for w in self._queued_widgets:
                w.remove()
            self._queued_widgets.clear()
        self._deferred_actions.clear()
        self._pending_plan_handoff_prompt = None

    @staticmethod
    def _prewarm_deferred_imports() -> None:
        """Background-load modules deferred from the startup path.

        Populates `sys.modules` so the first user-triggered inline import
        is a cheap dict lookup instead of a cold module load.
        """
        # Internal modules moved from top-level to local imports — a failure
        # here indicates a packaging or code bug, not a missing optional dep, so
        # we let the exception propagate (the worker catches it and logs
        # at WARNING). textual_adapter and update_check are included so
        # _post_paint_init's inline imports are dict lookups.
        from invincat_cli.io.clipboard import (
            copy_selection_to_clipboard,  # noqa: F401
        )
        from invincat_cli.command_registry import ALWAYS_IMMEDIATE  # noqa: F401
        from invincat_cli.config import settings  # noqa: F401
        from invincat_cli.hooks import dispatch_hook  # noqa: F401
        from invincat_cli.model_config import ModelSpec  # noqa: F401
        from invincat_cli.textual_adapter import TextualUIAdapter  # noqa: F401
        from invincat_cli.update_check import is_update_check_enabled  # noqa: F401

        try:
            # Heavy third-party deps deferred from textual_adapter /
            # tool_display — hit on first message send and first tool
            # approval. Best-effort: missing optional deps should not block the
            # TUI from rendering.
            from deepagents.backends import DEFAULT_EXECUTE_TIMEOUT  # noqa: F401
            from langchain.agents.middleware.human_in_the_loop import (  # noqa: F401
                ApproveDecision,
            )
            from langchain_core.messages import AIMessage  # noqa: F401
            from langgraph.types import Command  # noqa: F401
        except Exception:
            logger.warning("Could not prewarm third-party imports", exc_info=True)

        # Markdown rendering stack — ~170 ms cold (textual._markdown pulls in
        # markdown_it, pygments, linkify_it — 438 modules).  Hit on first
        # SkillMessage compose() and first code-fence highlight.  Warming
        # here makes the first expand/Ctrl+O instant.
        import markdown_it  # noqa: F401
        from pygments.lexers import get_lexer_by_name as _get_lexer
        from textual.widgets import Markdown  # noqa: F401

        # Instantiate the Python lexer to populate Pygments' internal
        # lexer cache (~12 ms cold).  Python is the most common fence
        # language in skill bodies.
        _get_lexer("python")

        # Widgets deferred from app.py module level — a failure here indicates
        # a packaging or code bug (same as the block above), so we let
        # exceptions propagate.
        from invincat_cli.widgets.approval import ApprovalMenu  # noqa: F401
        from invincat_cli.widgets.ask_user import AskUserMenu  # noqa: F401
        from invincat_cli.widgets.model_selector import (
            ModelSelectorScreen,  # noqa: F401
        )
        from invincat_cli.widgets.memory_viewer import MemoryViewerScreen  # noqa: F401
        from invincat_cli.widgets.thread_selector import (  # noqa: F401
            DeleteThreadConfirmScreen,
            ThreadSelectorScreen,
        )

    async def _prewarm_threads_cache(self) -> None:  # noqa: PLR6301  # Worker hook kept as instance method
        """Prewarm thread selector cache without blocking app startup."""
        from invincat_cli.sessions import (
            get_thread_limit,
            prewarm_thread_message_counts,
        )

        await prewarm_thread_message_counts(limit=get_thread_limit())

    async def _prewarm_model_caches(self) -> None:
        """Prewarm model discovery and profile caches without blocking startup."""
        try:
            from invincat_cli.model_config import (
                get_available_models,
                get_model_profiles,
            )

            await asyncio.to_thread(get_available_models)
            await asyncio.to_thread(
                get_model_profiles, cli_override=self._profile_override
            )
        except Exception:
            logger.warning("Could not prewarm model caches", exc_info=True)

    async def _check_for_updates(self) -> None:
        """Check PyPI for a newer version and optionally auto-update."""
        # Phase 1: version check (benign failure)
        try:
            from invincat_cli.update_check import (
                is_auto_update_enabled,
                is_update_available,
                upgrade_command,
            )

            available, latest = await asyncio.to_thread(is_update_available)
            if not available:
                return

            self._update_available = (True, latest)
        except Exception:
            logger.debug("Background update check failed", exc_info=True)
            return

        # Phase 2: auto-update or notify (failures surfaced to user)
        try:
            from invincat_cli.core.version import __version__ as cli_version

            if is_auto_update_enabled():
                from invincat_cli.update_check import perform_upgrade

                self.notify(
                    t("app.updating_to", version=latest),
                    severity="information",
                    timeout=5,
                )
                success, _output = await perform_upgrade()
                if success:
                    self.notify(
                        t("app.updated_to", version=latest),
                        severity="information",
                        timeout=10,
                    )
                else:
                    cmd = upgrade_command()
                    self.notify(
                        t("app.auto_update_failed", command=cmd),
                        severity="warning",
                        timeout=15,
                        markup=False,
                    )
            else:
                cmd = upgrade_command()
                self.notify(
                    t("app.update_available", latest=latest, current=cli_version, command=cmd),
                    severity="information",
                    timeout=15,
                    markup=False,
                )
        except Exception:
            logger.warning("Auto-update failed unexpectedly", exc_info=True)
            self.notify(
                t("app.update_failed"),
                severity="warning",
                timeout=10,
            )

    async def _show_whats_new(self) -> None:
        """Show a 'what's new' banner on the first launch after an upgrade."""
        try:
            from invincat_cli.update_check import should_show_whats_new

            if not await asyncio.to_thread(should_show_whats_new):
                return
        except Exception:
            logger.debug("What's new check failed", exc_info=True)
            return

        try:
            from invincat_cli.core.version import __version__ as cli_version

            await self._mount_message(
                AppMessage(
                    f"Updated to v{cli_version}\nSee what's new: {CHANGELOG_URL}"
                )
            )
        except Exception:
            logger.debug("What's new banner display failed", exc_info=True)
            return

        try:
            from invincat_cli.core.version import __version__ as cli_version
            from invincat_cli.update_check import mark_version_seen

            await asyncio.to_thread(mark_version_seen, cli_version)
        except Exception:
            logger.warning("Failed to persist seen-version marker", exc_info=True)

    async def _handle_update_command(self) -> None:
        """Handle the `/update` slash command — check for and install updates."""
        await self._mount_message(UserMessage("/update"))
        try:
            from invincat_cli.update_check import (
                is_update_available,
                perform_upgrade,
                upgrade_command,
            )

            await self._mount_message(AppMessage(t("update.checking")))
            available, latest = await asyncio.to_thread(
                is_update_available, bypass_cache=True
            )
            if not available:
                await self._mount_message(AppMessage(t("success.up_to_date")))
                return

            from invincat_cli.core.version import __version__ as cli_version

            await self._mount_message(
                AppMessage(
                    t("app.update_available_upgrading").format(
                        latest=latest,
                        current=cli_version,
                    )
                )
            )
            success, output = await perform_upgrade()
            if success:
                self._update_available = (False, None)
                await self._mount_message(
                    AppMessage(t("app.updated_to").format(version=latest))
                )
            else:
                cmd = upgrade_command()
                detail = f": {output[:200]}" if output else ""
                await self._mount_message(
                    AppMessage(
                        t("app.auto_update_failed_with_detail").format(
                            detail=detail,
                            command=cmd,
                        )
                    )
                )
        except Exception as exc:
            logger.warning("/update command failed", exc_info=True)
            await self._mount_message(
                ErrorMessage(
                    t("app.update_failed_with_error").format(
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
            )

    async def _handle_auto_update_toggle(self) -> None:
        """Handle the `/auto-update` slash command — persist toggle immediately."""
        try:
            from invincat_cli.config import _is_editable_install
            from invincat_cli.update_check import (
                is_auto_update_enabled,
                set_auto_update,
            )

            if await asyncio.to_thread(_is_editable_install):
                self.notify(
                    t("app.auto_update_not_available"),
                    severity="warning",
                    timeout=5,
                )
                return

            currently_enabled = await asyncio.to_thread(is_auto_update_enabled)
            new_state = not currently_enabled
            await asyncio.to_thread(set_auto_update, new_state)
            label = t("app.auto_updates_enabled") if new_state else t("app.auto_updates_disabled")
            self.notify(
                label,
                severity="information",
                timeout=5,
                markup=False,
            )
        except Exception as exc:
            logger.warning("/auto-update command failed", exc_info=True)
            self.notify(
                t("app.auto_update_toggle_failed", error=f"{type(exc).__name__}: {exc}"),
                severity="warning",
                timeout=5,
                markup=False,
            )

    def on_scroll_up(self, _event: ScrollUp) -> None:
        """Handle scroll up to check if we need to hydrate older messages."""
        self._check_hydration_needed()

    def on_scroll_to(self, _event: ScrollTo) -> None:
        """Handle scroll events to check if we need to hydrate older messages.

        This catches all scroll events including mouse wheel, keyboard, and
        scrollbar drag, not just clicking on the scrollbar track.
        """
        self._check_hydration_needed()
        self._maybe_reanchor()

    def _update_status(self, message: str) -> None:
        """Update the status bar with a message."""
        if self._status_bar:
            self._status_bar.set_status_message(message)

    def _update_tokens(self, count: int, *, approximate: bool = False) -> None:
        """Update the token count in the status bar.

        Low-level helper — only touches the UI.  Callers that also need to
        update the local cache should use `_on_tokens_update` instead.

        Args:
            count: Total context token count.
            approximate: Append "+" to signal a stale/interrupted count.
        """
        if self._status_bar:
            self._status_bar.set_tokens(count, approximate=approximate)

    def _on_tokens_update(self, count: int, *, approximate: bool = False) -> None:
        """Update the local cache *and* the status bar.

        This is the callback wired to the adapter's `_on_tokens_update`.

        Args:
            count: Total context token count to cache and display.
            approximate: Append "+" to signal a stale/interrupted count.
        """
        self._context_tokens = count
        self._tokens_approximate = approximate
        self._update_tokens(count, approximate=approximate)

    def _show_tokens(self, *, approximate: bool = False) -> None:
        """Restore the status bar to the cached token value.

        Args:
            approximate: Append "+" to signal a stale/interrupted count.

                This flag is sticky until `_on_tokens_update` receives a fresh
                count from the model.
        """
        self._tokens_approximate = self._tokens_approximate or approximate
        self._update_tokens(
            self._context_tokens,
            approximate=self._tokens_approximate,
        )

    def _hide_tokens(self) -> None:
        """Hide the token display during streaming."""
        if self._status_bar:
            self._status_bar.hide_tokens()

    def _maybe_reanchor(self) -> None:
        """Re-establish the scroll anchor when the user has scrolled to the bottom.

        Textual releases the anchor automatically on manual scroll-up.  When
        the user scrolls back to the bottom we restore it so new content keeps
        the view up-to-date.
        """
        try:
            chat = self.query_one("#chat", VerticalScroll)
        except NoMatches:
            return
        if not chat.is_anchored and chat.max_scroll_y > 0:
            if chat.scroll_y >= chat.max_scroll_y - 2:
                chat.anchor()

    def _check_hydration_needed(self) -> None:
        """Check if we need to hydrate messages from the store.

        Called when user scrolls up near the top of visible messages.
        """
        if not self._message_store.has_messages_above:
            return

        try:
            chat = self.query_one("#chat", VerticalScroll)
        except NoMatches:
            logger.debug("Skipping hydration check: #chat container not found")
            return

        scroll_y = chat.scroll_y
        viewport_height = chat.size.height

        if self._message_store.should_hydrate_above(scroll_y, viewport_height):
            self.call_later(self._hydrate_messages_above)

    async def _hydrate_messages_above(self) -> None:
        """Hydrate older messages when user scrolls near the top.

        This recreates widgets for archived messages and inserts them
        at the top of the messages container.
        """
        if not self._message_store.has_messages_above:
            return

        try:
            chat = self.query_one("#chat", VerticalScroll)
        except NoMatches:
            logger.debug("Skipping hydration: #chat not found")
            return

        try:
            messages_container = self.query_one("#messages", Container)
        except NoMatches:
            logger.debug("Skipping hydration: #messages not found")
            return

        to_hydrate = self._message_store.get_messages_to_hydrate()
        if not to_hydrate:
            return

        old_scroll_y = chat.scroll_y
        first_child = (
            messages_container.children[0] if messages_container.children else None
        )

        # Build widgets in chronological order, then mount in reverse so
        # each is inserted before the previous first_child, resulting in
        # correct chronological order in the DOM.
        hydrated_count = 0
        hydrated_widgets: list[tuple[Widget, MessageData]] = []
        for msg_data in to_hydrate:
            try:
                widget = msg_data.to_widget()
                hydrated_widgets.append((widget, msg_data))
            except Exception:
                logger.warning(
                    "Failed to create widget for message %s",
                    msg_data.id,
                    exc_info=True,
                )

        widgets_to_mount = [w for w, _ in hydrated_widgets]  # chronological order
        try:
            if first_child:
                await messages_container.mount(*widgets_to_mount, before=first_child)
            else:
                await messages_container.mount(*widgets_to_mount)
            hydrated_count = len(widgets_to_mount)
        except Exception:
            logger.warning("Batch hydration mount failed; falling back to sequential", exc_info=True)
            for widget, _ in hydrated_widgets:
                try:
                    if first_child:
                        await messages_container.mount(widget, before=first_child)
                    else:
                        await messages_container.mount(widget)
                    first_child = widget
                    hydrated_count += 1
                except Exception:
                    logger.warning("Failed to mount hydrated widget %s", widget.id, exc_info=True)

        # Render Markdown content after all widgets are mounted
        for widget, msg_data in hydrated_widgets:
            if isinstance(widget, AssistantMessage) and msg_data.content:
                try:
                    await widget.set_content(msg_data.content)
                except Exception:
                    logger.warning("Failed to set content for hydrated widget", exc_info=True)

        # Only update store for the number we actually mounted
        if hydrated_count > 0:
            self._message_store.mark_hydrated(hydrated_count)

        # Adjust scroll position to maintain the user's view.
        # Widget heights aren't known until after layout, so we use a
        # heuristic. A more accurate approach would measure actual heights
        # via call_after_refresh.
        estimated_height_per_message = 5  # terminal rows, rough estimate
        added_height = hydrated_count * estimated_height_per_message
        chat.scroll_y = old_scroll_y + added_height

    async def _mount_before_queued(self, container: Container, widget: Widget) -> None:
        """Mount a widget in the messages container, before any queued widgets.

        Queued-message widgets must stay at the bottom of the container so
        they remain visually anchored below the current agent response.
        This helper inserts `widget` just before the first queued widget,
        or appends at the end when the queue is empty.

        Args:
            container: The `#messages` container to mount into.
            widget: The widget to mount.
        """
        if not container.is_attached:
            return
        first_queued = self._queued_widgets[0] if self._queued_widgets else None
        if first_queued is not None and first_queued.parent is container:
            try:
                await container.mount(widget, before=first_queued)
            except Exception:
                logger.warning(
                    "Stale queued-widget reference; appending at end",
                    exc_info=True,
                )
            else:
                return
        await container.mount(widget)

    def _is_spinner_at_correct_position(self, container: Container) -> bool:
        """Check whether the loading spinner is already correctly positioned.

        The spinner should be immediately before the first queued widget, or
        at the very end of the container when the queue is empty.

        Args:
            container: The `#messages` container.

        Returns:
            `True` if the spinner is already in the correct position.
        """
        children = list(container.children)
        if not children or self._loading_widget not in children:
            return False

        if self._queued_widgets:
            first_queued = self._queued_widgets[0]
            if first_queued not in children:
                return False
            return children.index(self._loading_widget) == (
                children.index(first_queued) - 1
            )

        return children[-1] == self._loading_widget

    async def _set_spinner(self, status: SpinnerStatus) -> None:
        """Show, update, or hide the loading spinner.

        Args:
            status: The spinner status to display, or `None` to hide.
        """
        if status is None:
            # Hide
            if self._loading_widget:
                await self._loading_widget.remove()
                self._loading_widget = None
            return

        messages = self.query_one("#messages", Container)

        if self._loading_widget is None:
            # Create new
            self._loading_widget = LoadingWidget(status)
            await self._mount_before_queued(messages, self._loading_widget)
        else:
            # Update existing
            self._loading_widget.set_status(status)
            # Reposition if not already at the correct location
            if not self._is_spinner_at_correct_position(messages):
                await self._loading_widget.remove()
                await self._mount_before_queued(messages, self._loading_widget)
        # NOTE: Don't call anchor() here - it would re-anchor and drag user back
        # to bottom if they've scrolled away during streaming

    async def _request_approval(
        self,
        action_requests: Any,  # noqa: ANN401  # ActionRequest uses dynamic typing
        assistant_id: str | None,
        *,
        bypass_plan_guard: bool = False,
        allow_auto_approve: bool = True,
    ) -> asyncio.Future:
        """Request user approval inline in the messages area.

        Mounts ApprovalMenu in the messages area (inline with chat).
        ChatInput stays visible - user can still see it.

        If another approval is already pending, queue this one.

        Auto-approves shell commands that are in the configured allow-list.

        Args:
            action_requests: List of action request dicts to approve
            assistant_id: The assistant ID for display purposes
            allow_auto_approve: Whether to show the auto-approve option.

        Returns:
            A Future that resolves to the user's decision.
        """
        from invincat_cli.config import (
            SHELL_TOOL_NAMES,
            is_shell_command_allowed,
            settings,
        )

        loop = asyncio.get_running_loop()
        result_future: asyncio.Future = loop.create_future()

        # In /plan mode, hard-reject non-read/plan interrupting tools so
        # planner cannot proceed with implementation-side effects.
        if (
            not bypass_plan_guard
            and self._session_state
            and self._session_state.plan_mode
            and self._active_turn_is_planner
            and action_requests
        ):
            disallowed_tool_names = disallowed_plan_interrupt_tools(action_requests)
            if disallowed_tool_names:
                # If planner already wrote todos in this turn, surface plan
                # approval immediately (before rejecting the disallowed write).
                try:
                    await self._maybe_approve_current_planner_todos()
                except Exception:
                    logger.debug(
                        "Failed to trigger immediate /plan approval before rejecting tool call",
                        exc_info=True,
                    )
                result_future.set_result({"type": "reject"})
                denied = ", ".join(disallowed_tool_names)
                try:
                    from invincat_cli.i18n import t

                    messages = self.query_one("#messages", Container)
                    await self._mount_before_queued(
                        messages,
                        AppMessage(
                            t("plan.auto_reject_non_plan_tool").format(tools=denied)
                        ),
                    )
                except Exception:  # noqa: BLE001  # best-effort status message
                    logger.debug(
                        "Failed to mount /plan auto-reject notice",
                        exc_info=True,
                    )
                return result_future

        approved_commands = resolve_auto_approved_shell_commands(
            action_requests,
            shell_allow_list=settings.shell_allow_list,
            shell_tool_names=SHELL_TOOL_NAMES,
            cwd=self._cwd,
            is_shell_command_allowed=is_shell_command_allowed,
        )
        if approved_commands is not None:
            # Auto-approve all commands in the batch
            result_future.set_result({"type": "approve"})

            # Mount system messages showing the auto-approvals
            try:
                messages = self.query_one("#messages", Container)
                for command in approved_commands:
                    auto_msg = AppMessage(
                        build_auto_approved_shell_message(command)
                    )
                    await self._mount_before_queued(messages, auto_msg)
                with suppress(NoMatches, ScreenStackError):
                    self.query_one("#chat", VerticalScroll).anchor()
            except Exception:  # noqa: BLE001  # Resilient auto-message display
                logger.debug("Failed to display auto-approval message", exc_info=True)

            return result_future

        # If there's already a pending approval, wait for it to complete first
        if self._pending_approval_widget is not None:
            queue_deadline = pending_widget_deadline(now=_monotonic())
            while self._pending_approval_widget is not None:  # noqa: ASYNC110
                if deadline_expired(now=_monotonic(), deadline=queue_deadline):
                    logger.warning(
                        "Timed out waiting for previous approval widget to clear "
                        "after 30s; proceeding with new approval"
                    )
                    break
                await asyncio.sleep(INTERACTION_POLL_SECONDS)

        # Create menu with unique ID to avoid conflicts
        from invincat_cli.widgets.approval import ApprovalMenu

        unique_id = build_interaction_widget_id(
            prefix="approval-menu",
            token=uuid.uuid4().hex[:8],
        )
        menu = ApprovalMenu(
            action_requests,
            assistant_id,
            allow_auto_approve=allow_auto_approve,
            id=unique_id,
        )
        menu.set_future(result_future)

        self._pending_approval_widget = menu

        if self._is_user_typing():
            # Show a placeholder until the user stops typing, then swap in the
            # real ApprovalMenu.  This prevents accidental key presses (e.g.
            # 'y', 'n') from triggering approval decisions mid-sentence.
            placeholder = Static(
                APPROVAL_PLACEHOLDER_TEXT,
                classes=APPROVAL_PLACEHOLDER_CLASS,
            )
            self._approval_placeholder = placeholder
            try:
                messages = self.query_one("#messages", Container)
                await self._mount_before_queued(messages, placeholder)
                self.call_after_refresh(placeholder.scroll_visible)
            except Exception:
                logger.exception("Failed to mount approval placeholder")
                # Placeholder failed — fall back to showing the menu directly
                # so the future is always resolvable.
                self._approval_placeholder = None
                await self._mount_approval_widget(menu, result_future)
                return result_future

            self.run_worker(
                self._deferred_show_approval(placeholder, menu, result_future),
                exclusive=False,
            )
        else:
            await self._mount_approval_widget(menu, result_future)

        return result_future

    async def _mount_approval_widget(
        self,
        menu: ApprovalMenu,
        result_future: asyncio.Future[dict[str, str]],
    ) -> None:
        """Mount the approval menu widget inline in the messages area.

        If mounting fails, clears `_pending_approval_widget` and propagates
        the exception via `result_future`.

        Args:
            menu: The `ApprovalMenu` instance to mount.
            result_future: The future to resolve/reject for the caller.
        """
        try:
            messages = self.query_one("#messages", Container)
            await self._mount_before_queued(messages, menu)
            self.call_after_refresh(menu.scroll_visible)
            self.call_after_refresh(menu.focus)
        except Exception as e:
            logger.exception(
                "Failed to mount approval menu (id=%s) in messages container",
                menu.id,
            )
            self._pending_approval_widget = None
            if not result_future.done():
                result_future.set_exception(e)

    async def _deferred_show_approval(
        self,
        placeholder: Static,
        menu: ApprovalMenu,
        result_future: asyncio.Future[dict[str, str]],
    ) -> None:
        """Wait until the user is idle, then swap the placeholder for the real menu.

        Exits early if the placeholder has already been detached (e.g. the
        approval was cancelled while waiting).  In that case the future is
        cancelled so the caller is not left hanging.

        Args:
            placeholder: The temporary placeholder widget currently mounted.
            menu: The `ApprovalMenu` to show once the user stops typing.
            result_future: The future backing this approval flow.
        """
        try:
            deadline = _monotonic() + DEFERRED_APPROVAL_TIMEOUT_SECONDS
            while self._is_user_typing():  # Simple polling
                if deadline_expired(now=_monotonic(), deadline=deadline):
                    logger.warning(
                        "Timed out waiting for user to stop typing; showing approval now"
                    )
                    break
                await asyncio.sleep(DEFERRED_APPROVAL_POLL_SECONDS)

            # Guard: if the placeholder was already removed (e.g. agent cancelled
            # the approval while we were waiting), clean up and cancel the future.
            if should_cancel_detached_placeholder(
                placeholder_attached=placeholder.is_attached
            ):
                logger.warning(
                    "Approval placeholder detached before menu shown (id=%s)",
                    menu.id,
                )
                self._approval_placeholder = None
                self._pending_approval_widget = None
                if not result_future.done():
                    result_future.cancel()
                return

            self._approval_placeholder = None
            try:
                await placeholder.remove()
            except Exception:
                logger.warning(
                    "Failed to remove approval placeholder during swap",
                    exc_info=True,
                )
            await self._mount_approval_widget(menu, result_future)
        except BaseException:
            # Worker cancelled (CancelledError) or unexpected crash — ensure the
            # future is always resolved so the agent is never left deadlocked
            # awaiting an approval that will never arrive.
            if not result_future.done():
                self._pending_approval_widget = None
                self._approval_placeholder = None
                result_future.cancel()
            raise

    async def _remove_approval_placeholder(self, *, context: str) -> None:
        """Remove any mounted deferred approval placeholder."""
        placeholder = self._approval_placeholder
        if placeholder is None:
            return
        self._approval_placeholder = None
        if not placeholder.is_attached:
            return
        try:
            await placeholder.remove()
        except Exception:
            logger.warning(
                "Failed to remove approval placeholder during %s",
                context,
                exc_info=True,
            )

    def _on_auto_approve_enabled(self) -> None:
        """Handle auto-approve being enabled via the HITL approval menu.

        Called when the user selects "Auto-approve all" from an approval
        dialog. Syncs the auto-approve state across the app flag, status
        bar indicator, and session state so subsequent tool calls skip
        the approval prompt.
        """
        self._auto_approve = True
        if self._status_bar:
            self._status_bar.set_auto_approve(enabled=True)
        if self._session_state:
            self._session_state.auto_approve = True

    async def _handle_plan_task(self) -> None:
        """Handle /plan command.

        Enter plan mode and wait for the user's planning task as the next chat
        message. `/plan <task>` is intentionally unsupported so mode entry and
        the user's requirement stay as separate conversational events.
        """
        from invincat_cli.i18n import t

        if self._session_state and self._session_state.plan_mode:
            await self._mount_message(AppMessage(t("plan.already_on")))
            return
        self._planner_thread_id = new_thread_id()
        self._planner_last_todos_fingerprint = None
        self._planner_prompted_todos_fingerprint = None
        if self._session_state:
            self._main_thread_before_plan = self._session_state.thread_id
        if self._session_state:
            self._session_state.plan_mode = True
        if self._status_bar:
            self._status_bar.set_plan_mode(enabled=True)
        await self._mount_message(UserMessage("/plan"))
        await self._mount_message(AppMessage(t("plan.entered")))

    def _reset_plan_mode_state(self) -> None:
        """Restore main-thread state and clear planner-only bookkeeping."""
        if self._session_state:
            self._session_state.plan_mode = False
            if self._main_thread_before_plan:
                self._session_state.thread_id = self._main_thread_before_plan
        if self._status_bar:
            self._status_bar.set_plan_mode(enabled=False)
        self._planner_thread_id = None
        self._main_thread_before_plan = None
        self._planner_last_todos_fingerprint = None
        self._planner_prompted_todos_fingerprint = None
        self._pending_plan_handoff_prompt = None

    async def _exit_plan_mode(self) -> None:
        """Exit plan mode, cancel planner work, and restore main thread."""
        from invincat_cli.i18n import t

        if not self._session_state or not self._session_state.plan_mode:
            await self._mount_message(AppMessage(t("plan.not_on")))
            return

        if self._agent_running and self._agent_worker and self._active_turn_is_planner:
            if self._pending_approval_widget:
                self._pending_approval_widget.action_select_reject()
            await self._remove_approval_placeholder(context="plan exit")
            self._pending_approval_widget = None
            self._agent_worker.cancel()
            self._agent_running = False
            self._agent_worker = None
            self._active_turn_is_planner = False

        # Ensure exiting plan mode also cancels any queued handoff to main agent.
        self._deferred_actions = [
            action
            for action in self._deferred_actions
            if action.kind != "plan_handoff"
        ]
        self._pending_plan_handoff_prompt = None

        self._reset_plan_mode_state()
        await self._mount_message(AppMessage(t("plan.exited")))

    async def _run_planner(self, task: str) -> bool:
        """Send a user message to the planner agent session.

        Args:
            task: The task description to plan.
        """
        if not self._agent or not self._session_state:
            from invincat_cli.i18n import t

            await self._mount_message(AppMessage(t("plan.agent_not_configured")))
            return False

        planner = await self._ensure_planner_agent()
        if planner is None:
            from invincat_cli.i18n import t

            await self._mount_message(AppMessage(t("plan.planner_unavailable")))
            return False

        if not self._planner_thread_id:
            self._planner_thread_id = new_thread_id()

        # Reset per-turn dedupe so a rejected plan can be re-submitted (same
        # todos) on the next planner turn.
        self._planner_last_todos_fingerprint = None
        self._planner_prompted_todos_fingerprint = None

        return await self._send_to_agent(
            build_planner_turn_input(task=task, cwd=self._cwd),
            agent_override=planner,
            thread_id_override=self._planner_thread_id,
            post_turn_hook=self._after_planner_turn,
        )

    async def _ensure_planner_agent(self) -> Pregel | None:
        """Lazily create and cache a planner peer-agent.

        The planner is created from the same CLI agent assembly path as the
        main agent (same interaction/runtime behavior), but with a dedicated
        planning system prompt.
        """
        if self._planner_agent is not None:
            return self._planner_agent
        try:
            from pathlib import Path

            from langgraph.checkpoint.memory import InMemorySaver

            from invincat_cli.agent import create_cli_agent
            from invincat_cli.plan_agent import (
                PLANNER_APPROVE_PLAN_SYSTEM_PROMPT,
                PLANNER_ALLOWED_TOOLS,
                PLANNER_SYSTEM_PROMPT,
                PlannerToolAllowListMiddleware,
                PlannerVisibleToolsMiddleware,
            )
            from invincat_cli.config import settings
            from invincat_cli.project_utils import ProjectContext
            from invincat_cli.tools import fetch_url, web_search

            model = self._model if self._model is not None else (self._model_override or "claude-sonnet-4-6")
            planner_assistant_id = f"{self._assistant_id or 'agent'}-planner"
            planner_tools: list[Any] = [fetch_url]
            planner_allowed_tools = set(PLANNER_ALLOWED_TOOLS)
            if settings.has_tavily:
                planner_tools.append(web_search)
            else:
                planner_allowed_tools.discard("web_search")
            project_context = ProjectContext.from_user_cwd(Path(self._cwd))
            planner_system_prompt = build_planner_system_prompt(
                base_prompt=PLANNER_SYSTEM_PROMPT,
                cwd=self._cwd,
            )
            planner_checkpointer = getattr(self._agent, "checkpointer", None)
            if planner_checkpointer is None:
                # approve_plan relies on Command(resume=...), which requires
                # a checkpointer. Fallback to in-memory checkpointing for the
                # planner peer-agent if main agent metadata is unavailable.
                planner_checkpointer = InMemorySaver()
            planner_agent, _planner_backend = create_cli_agent(
                model=model,
                assistant_id=planner_assistant_id,
                system_prompt=planner_system_prompt,
                auto_approve=self._auto_approve,
                enable_memory=False,
                enable_skills=False,
                enable_ask_user=True,
                enable_shell=False,
                tools=planner_tools,
                cwd=self._cwd,
                project_context=project_context,
                mcp_server_info=self._mcp_server_info,
                checkpointer=planner_checkpointer,
                approve_plan_system_prompt=PLANNER_APPROVE_PLAN_SYSTEM_PROMPT,
                extra_middleware=[
                    PlannerVisibleToolsMiddleware(planner_allowed_tools),
                    PlannerToolAllowListMiddleware(planner_allowed_tools)
                ],
            )
            self._planner_agent = planner_agent
            return self._planner_agent
        except Exception:
            logger.exception("Failed to initialize planner agent")
            return None

    async def _get_thread_state_values_for_agent(
        self,
        agent: Pregel,
        thread_id: str,
    ) -> dict[str, Any]:
        """Fetch state values from a specific agent/thread pair."""
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        state = await agent.aget_state(config)
        if state and state.values:
            return dict(state.values)
        return {}

    async def _after_planner_turn(self) -> None:
        """Check planner turn result and drive plan approval flow."""
        from invincat_cli.plan_agent import extract_todos_from_message

        if not self._planner_agent or not self._planner_thread_id:
            return

        state_values = await self._get_thread_state_values_for_agent(
            self._planner_agent, self._planner_thread_id
        )
        if not state_values:
            return

        messages = normalize_state_messages(state_values.get("messages", []))
        approve_plan_decision = planner_turn_approve_plan_decision(messages)
        if approve_plan_decision is not None:
            if approve_plan_decision != "approved":
                if not latest_ai_text_after_latest_tool(messages, "approve_plan"):
                    await self._mount_message(AppMessage(t("plan.refine_prompt")))
                return

            todos = extract_todos_from_state(state_values)
            if not todos:
                latest_text = extract_latest_ai_text(messages)
                todos = extract_todos_from_message(latest_text) or []
            if not todos:
                await self._mount_message(
                    AppMessage(
                        t("plan.approval_no_valid_todos")
                    )
                )
                return
            await self._finalize_planner_approval(
                todos,
                planner_state_values=state_values,
            )
            return
        wrote_todos_this_turn = planner_turn_has_write_todos(messages)
        if not wrote_todos_this_turn:
            return

        todos = extract_todos_from_state(state_values)
        if not todos:
            latest_text = extract_latest_ai_text(messages)
            todos = extract_todos_from_message(latest_text) or []
        if not todos:
            await self._mount_message(
                AppMessage(t("plan.ready_no_valid_todos"))
            )
            return

        todos_fingerprint = plan_todos_fingerprint(todos)
        if todos_fingerprint == self._planner_prompted_todos_fingerprint:
            return

        await self._process_planner_todos_approval(todos)

    async def _process_planner_todos_approval(
        self,
        todos: list[dict[str, str]],
    ) -> bool:
        """Approve planner todos and finalize plan mode when approved."""
        from invincat_cli.i18n import t

        todos_fingerprint = plan_todos_fingerprint(todos)
        if todos_fingerprint == self._planner_last_todos_fingerprint:
            return False

        future = await self._request_approve_plan(todos)
        result = await future
        self._planner_last_todos_fingerprint = todos_fingerprint
        if result.get("type") != "approved":
            await self._mount_message(AppMessage(t("plan.refine_prompt")))
            return False

        await self._finalize_planner_approval(todos)
        return True

    async def _maybe_approve_current_planner_todos(self) -> bool:
        """Best-effort immediate approval when planner already has todo state."""
        from invincat_cli.plan_agent import extract_todos_from_message

        if not self._planner_agent or not self._planner_thread_id:
            return False
        state_values = await self._get_thread_state_values_for_agent(
            self._planner_agent, self._planner_thread_id
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
        return await self._process_planner_todos_approval(todos)

    def _invalidate_planner_agent_cache(self) -> None:
        """Invalidate cached planner runtime so it picks up fresh model config."""
        self._planner_agent = None
        self._planner_last_todos_fingerprint = None
        self._planner_prompted_todos_fingerprint = None

    async def _finalize_planner_approval(
        self,
        todos: list[dict[str, str]],
        *,
        planner_state_values: dict[str, Any] | None = None,
    ) -> None:
        """Finalize plan mode after approval and handoff execution to main agent."""
        from invincat_cli.i18n import t

        plan_text = build_plan_text(todos)
        effective_state = planner_state_values
        if effective_state is None and self._planner_agent and self._planner_thread_id:
            try:
                effective_state = await self._get_thread_state_values_for_agent(
                    self._planner_agent,
                    self._planner_thread_id,
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
        self._reset_plan_mode_state()
        self._pending_plan_handoff_prompt = handoff_prompt
        await self._mount_message(
            AppMessage(
                f"{t('plan.approved_no_execute')}\n\n{plan_text}"
            )
        )

    async def _execute_plan_handoff(self, prompt: str) -> None:
        """Execute approved plan handoff explicitly on the main agent.

        This bypasses `_handle_user_message()` routing so handoff execution
        cannot be redirected back into planner mode by stale session flags.
        """
        if not self._session_state:
            return

        self._session_state.plan_mode = False
        if self._status_bar:
            self._status_bar.set_plan_mode(enabled=False)

        from invincat_cli.i18n import t

        await self._mount_message(AppMessage(t("plan.handoff_started")))
        await self._mount_message(
            AppMessage(
                f"{t('plan.handoff_prompt_preview')}\n\n{prompt}"
            )
        )
        started = await self._send_to_agent(prompt)
        if not started:
            self._pending_plan_handoff_prompt = prompt

    async def _remove_ask_user_widget(  # noqa: PLR6301  # Shared helper used by ask_user event handlers
        self,
        widget: AskUserMenu,
        *,
        context: str,
    ) -> None:
        """Remove an ask_user widget without surfacing cleanup races.

        Args:
            widget: Ask-user widget instance to remove.
            context: Short context string for diagnostics.
        """
        try:
            await widget.remove()
        except Exception:
            logger.debug(
                "Failed to remove ask-user widget during %s",
                context,
                exc_info=True,
            )

    async def _request_ask_user(
        self,
        questions: list[Question],
    ) -> asyncio.Future[AskUserWidgetResult]:
        """Display the ask_user widget and return a Future with user response.

        Args:
            questions: List of question dicts, each with `question`, `type`,
                and optional `choices` and `required` keys.

        Returns:
            A Future that resolves to a dict with `'type'` (`'answered'` or
                `'cancelled'`) and, when answered, an `'answers'` list.
        """
        loop = asyncio.get_running_loop()
        result_future: asyncio.Future[AskUserWidgetResult] = loop.create_future()

        if self._pending_ask_user_widget is not None:
            deadline = pending_widget_deadline(now=_monotonic())
            while self._pending_ask_user_widget is not None:
                if deadline_expired(now=_monotonic(), deadline=deadline):
                    logger.error(
                        "Timed out waiting for previous ask-user widget to "
                        "clear. Forcefully cleaning up."
                    )
                    old_widget = self._pending_ask_user_widget
                    if old_widget is not None:
                        old_widget.action_cancel()
                        self._pending_ask_user_widget = None
                        await self._remove_ask_user_widget(
                            old_widget,
                            context="ask-user timeout cleanup",
                        )
                    break
                await asyncio.sleep(INTERACTION_POLL_SECONDS)

        from invincat_cli.widgets.ask_user import AskUserMenu

        unique_id = build_interaction_widget_id(
            prefix="ask-user-menu",
            token=uuid.uuid4().hex[:8],
        )
        menu = AskUserMenu(questions, id=unique_id)
        menu.set_future(result_future)

        self._pending_ask_user_widget = menu

        try:
            messages = self.query_one("#messages", Container)
            await self._mount_before_queued(messages, menu)
            self.call_after_refresh(menu.scroll_visible)
            self.call_after_refresh(menu.focus_active)
        except Exception as e:
            logger.exception(
                "Failed to mount ask-user menu (id=%s)",
                unique_id,
            )
            self._pending_ask_user_widget = None
            if not result_future.done():
                result_future.set_exception(e)

        return result_future

    async def on_ask_user_menu_answered(
        self,
        event: Any,  # noqa: ARG002, ANN401
    ) -> None:
        """Handle ask_user menu answers - remove widget and refocus input."""
        if self._pending_ask_user_widget:
            widget = self._pending_ask_user_widget
            self._pending_ask_user_widget = None
            await self._remove_ask_user_widget(widget, context="ask-user answered")

        if self._chat_input:
            self.call_after_refresh(self._chat_input.focus_input)

    async def on_ask_user_menu_cancelled(
        self,
        event: Any,  # noqa: ARG002, ANN401
    ) -> None:
        """Handle ask_user menu cancellation - remove widget and refocus input."""
        if self._pending_ask_user_widget:
            widget = self._pending_ask_user_widget
            self._pending_ask_user_widget = None
            await self._remove_ask_user_widget(widget, context="ask-user cancelled")

        if self._chat_input:
            self.call_after_refresh(self._chat_input.focus_input)

    async def _request_approve_plan(
        self,
        todos: list[dict[str, Any]],
    ) -> asyncio.Future[dict[str, Any]]:
        """Display plan approval using the standard ApprovalMenu component.

        Args:
            todos: List of todo items, each with `content` and `status` keys.

        Returns:
            A Future that resolves to a dict with `'type'` (`'approved'` or
                `'rejected'`).
        """
        loop = asyncio.get_running_loop()
        mapped_future: asyncio.Future[dict[str, Any]] = loop.create_future()

        action_request = build_approve_plan_action_request(todos)
        self._planner_prompted_todos_fingerprint = plan_todos_fingerprint(todos)

        raw_future = await self._request_approval(
            [action_request],
            self._assistant_id,
            bypass_plan_guard=True,
            allow_auto_approve=False,
        )

        async def _map_plan_decision() -> None:
            try:
                raw = await raw_future
                mapped = map_raw_approval_to_plan_decision(raw)
                if not mapped_future.done():
                    mapped_future.set_result(mapped)
            except Exception as exc:
                if not mapped_future.done():
                    mapped_future.set_exception(exc)

        self.run_worker(_map_plan_decision(), exclusive=False)
        return mapped_future

    async def on_approve_widget_approved(
        self,
        event: Any,  # noqa: ARG002, ANN401
    ) -> None:
        """Handle approve widget approval - remove widget and refocus input."""
        from invincat_cli.i18n import t

        await self._mount_message(AppMessage(t("approve.approved")))
        if self._chat_input:
            self.call_after_refresh(self._chat_input.focus_input)

    async def on_approve_widget_rejected(
        self,
        event: Any,  # noqa: ARG002, ANN401
    ) -> None:
        """Handle approve widget rejection - remove widget and refocus input."""
        from invincat_cli.i18n import t

        await self._mount_message(AppMessage(t("approve.rejected")))
        if self._chat_input:
            self.call_after_refresh(self._chat_input.focus_input)

    async def _process_message(self, value: str, mode: InputMode) -> None:
        """Route a message to the appropriate handler based on mode.

        Args:
            value: The message text to process.
            mode: The input mode that determines message routing.
        """
        if mode == "shell":
            await self._handle_shell_command(value.removeprefix("!"))
        elif mode == "command":
            await self._handle_command(value)
        elif mode == "normal":
            await self._handle_user_message(value)
        else:
            logger.warning("Unrecognized input mode %r, treating as normal", mode)
            await self._handle_user_message(value)

    def _can_bypass_queue(self, value: str) -> bool:
        """Check if a slash command can skip the message queue.

        Args:
            value: The lowered, stripped command string (e.g. `/model`).

        Returns:
            `True` if the command should bypass the busy-state queue.
        """
        return can_bypass_busy_queue(
            value,
            connecting=self._connecting,
            agent_running=self._agent_running,
            shell_running=self._shell_running,
        )

    async def on_chat_input_submitted(self, event: ChatInput.Submitted) -> None:
        """Handle submitted input from ChatInput widget."""
        value = event.value
        mode: InputMode = event.mode  # type: ignore[assignment]  # Textual event mode is str at type level but InputMode at runtime

        # Reset quit pending state on any input
        self._quit_pending = False

        from invincat_cli.hooks import dispatch_hook

        await dispatch_hook("user.prompt", {})

        # /quit and /q always execute immediately, even mid-thread-switch.
        from invincat_cli.command_registry import ALWAYS_IMMEDIATE

        if mode == "command" and value.lower().strip() in ALWAYS_IMMEDIATE:
            self.exit()
            return

        # Prevent message handling while a thread switch is in-flight.
        if self._thread_switching:
            self.notify(
                t("app.thread_switch_in_progress"),
                severity="warning",
                timeout=3,
            )
            return

        # If agent/shell is running or server is still starting up, enqueue
        # instead of processing. Messages queued during connection are drained
        # once the server is ready (see on_deep_agents_app_server_ready).
        if self._agent_running or self._shell_running or self._connecting:
            if mode == "command" and self._can_bypass_queue(value.lower().strip()):
                await self._process_message(value, mode)
                return
            self._pending_messages.append(QueuedMessage(text=value, mode=mode))
            queued_widget = QueuedUserMessage(value)
            self._queued_widgets.append(queued_widget)
            await self._mount_message(queued_widget)
            return

        await self._process_message(value, mode)

    def on_chat_input_mode_changed(self, event: ChatInput.ModeChanged) -> None:
        """Update status bar when input mode changes."""
        if self._status_bar:
            self._status_bar.set_mode(event.mode)

    def on_chat_input_typing(
        self,
        event: ChatInput.Typing,  # noqa: ARG002  # Textual event handler signature
    ) -> None:
        """Record the most recent keystroke time for typing-aware approval deferral."""
        self._last_typed_at = _monotonic()

    def _is_user_typing(self) -> bool:
        """Return whether the user typed recently (within the idle threshold).

        Returns:
            `True` if the last recorded typing event occurred within the last
                `TYPING_IDLE_THRESHOLD_SECONDS` seconds, `False` otherwise.
        """
        return user_is_typing(
            last_typed_at=self._last_typed_at,
            now=_monotonic(),
            threshold_seconds=TYPING_IDLE_THRESHOLD_SECONDS,
        )

    async def on_approval_menu_decided(
        self,
        event: Any,  # noqa: ARG002, ANN401  # Textual event handler signature
    ) -> None:
        """Handle approval menu decision - remove from messages and refocus input."""
        # Defensively remove any lingering placeholder (should already be gone
        # once the deferred worker swaps it, but guard against edge cases).
        await self._remove_approval_placeholder(context="approval cleanup")

        # Remove ApprovalMenu using stored reference
        if self._pending_approval_widget:
            await self._pending_approval_widget.remove()
            self._pending_approval_widget = None

        # Refocus the chat input
        if self._chat_input:
            self.call_after_refresh(self._chat_input.focus_input)

    async def _handle_shell_command(self, command: str) -> None:
        """Handle a shell command (! prefix).

        Thin dispatcher that mounts the user message and spawns a worker
        so the event loop stays free for key events (Esc/Ctrl+C).

        Args:
            command: The shell command to execute.
        """
        await self._mount_message(UserMessage(f"!{command}"))
        self._shell_running = True

        if self._chat_input:
            self._chat_input.set_cursor_active(active=False)

        # Use suspend() for interactive commands (vi, top, etc.)
        if is_interactive_command(command):
            self._shell_worker = self.run_worker(
                self._run_interactive_shell_task(command),
                exclusive=False,
            )
        else:
            self._shell_worker = self.run_worker(
                self._run_shell_task(command),
                exclusive=False,
            )

    async def _run_interactive_shell_task(self, command: str) -> None:
        """Run an interactive shell command using suspend().

        This allows commands like vi, top, etc. to work properly by
        temporarily releasing the terminal back to the command.

        Args:
            command: The interactive shell command to execute.
        """
        import subprocess  # noqa: S404

        try:
            with self.suspend():
                result = subprocess.run(  # noqa: S603
                    command,
                    shell=True,
                    cwd=self._cwd,
                    check=False,
                )

            if result.returncode != 0:
                await self._mount_message(
                    ErrorMessage(
                        t("shell.exit_code").format(code=result.returncode)
                    )
                )
            else:
                await self._mount_message(AppMessage(t("shell.command_completed")))

            # Anchor to bottom so output stays visible
            with suppress(NoMatches, ScreenStackError):
                self.query_one("#chat", VerticalScroll).anchor()

        except FileNotFoundError:
            await self._mount_message(
                ErrorMessage(t("shell.command_not_found").format(command=command))
            )
        except OSError as e:
            logger.exception("Failed to execute interactive shell command: %s", command)
            await self._mount_message(
                ErrorMessage(t("shell.command_failed").format(error=str(e)))
            )
        finally:
            await self._cleanup_shell_task()

    async def _run_shell_task(self, command: str) -> None:
        """Run a shell command in a background worker.

        This mirrors `_run_agent_task`: running in a worker keeps the event
        loop free so Esc/Ctrl+C can cancel the worker -> raise
        `CancelledError` -> kill the process.

        Args:
            command: The shell command to execute.

        Raises:
            CancelledError: If the command is interrupted by the user.
        """
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self._cwd,
                start_new_session=should_start_new_shell_session(sys.platform),
            )
            self._shell_process = proc

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=60
                )
            except TimeoutError:
                await self._kill_shell_process()
                await self._mount_message(
                    ErrorMessage(t("shell.command_timeout").format(seconds=60))
                )
                return
            except asyncio.CancelledError:
                await self._kill_shell_process()
                raise

            output = format_shell_output(stdout_bytes, stderr_bytes)

            if output:
                msg = AssistantMessage(f"```\n{output}\n```")
                await self._mount_message(msg)
                await msg.write_initial_content()
            else:
                await self._mount_message(
                    AppMessage(t("shell.command_completed_no_output"))
                )

            if proc.returncode and proc.returncode != 0:
                await self._mount_message(
                    ErrorMessage(t("shell.exit_code").format(code=proc.returncode))
                )

            # Anchor to bottom so shell output stays visible
            with suppress(NoMatches, ScreenStackError):
                self.query_one("#chat", VerticalScroll).anchor()

        except OSError as e:
            logger.exception("Failed to execute shell command: %s", command)
            err_msg = t("shell.command_failed").format(error=str(e))
            await self._mount_message(ErrorMessage(err_msg))
        finally:
            await self._cleanup_shell_task()

    async def _cleanup_shell_task(self) -> None:
        """Clean up after shell command task completes or is cancelled."""
        was_interrupted = self._shell_process is not None and (
            self._shell_worker is not None and self._shell_worker.is_cancelled
        )
        self._shell_process = None
        self._shell_running = False
        self._shell_worker = None
        if was_interrupted:
            await self._mount_message(AppMessage(t("shell.command_interrupted")))
        if self._chat_input:
            self._chat_input.set_cursor_active(active=True)
        try:
            await self._maybe_drain_deferred()
        except Exception:
            logger.exception("Failed to drain deferred actions during shell cleanup")
            with suppress(Exception):
                await self._mount_message(
                    ErrorMessage(
                        "A deferred action failed after task completion. "
                        "You may need to retry the operation."
                    )
                )
        await self._process_next_from_queue()

    async def _kill_shell_process(self) -> None:
        """Terminate the running shell command process.

        On POSIX, sends SIGTERM to the entire process group (killing children).
        On Windows, terminates only the root process. No-op if the process has
        already exited. Waits up to 5s for clean shutdown, then escalates
        to SIGKILL.
        """
        proc = self._shell_process
        if proc is None or proc.returncode is not None:
            return

        try:
            strategy = shell_termination_strategy(sys.platform)
            if strategy == "process_group":
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                proc.terminate()
        except ProcessLookupError:
            return
        except OSError:
            logger.warning(
                "Failed to terminate shell process (pid=%s)", proc.pid, exc_info=True
            )
            return

        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except TimeoutError:
            logger.warning(
                "Shell process (pid=%s) did not exit after SIGTERM; sending SIGKILL",
                proc.pid,
            )
            with suppress(ProcessLookupError, OSError):
                if strategy == "process_group":
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:
                    proc.kill()
            with suppress(ProcessLookupError, OSError):
                await proc.wait()
        except (ProcessLookupError, OSError):
            pass

    async def _open_url_command(self, command: str, cmd: str) -> None:
        """Open a URL in the browser and display a clickable link.

        The browser opens immediately regardless of busy state. When the app is
        busy, a queued indicator is shown and the real chat output (user echo
        + clickable link) replaces it after the current task finishes.

        Args:
            command: The raw command text (displayed as user message).
            cmd: The normalized slash command used to look up the URL.
        """
        url = _COMMAND_URLS[cmd]
        webbrowser.open(url)

        if self._agent_running or self._shell_running:
            queued_widget = QueuedUserMessage(command)
            self._queued_widgets.append(queued_widget)
            await self._mount_message(queued_widget)

            async def _mount_output() -> None:
                # Remove the ephemeral queued widget, then mount real output.
                if queued_widget in self._queued_widgets:
                    self._queued_widgets.remove(queued_widget)
                with suppress(Exception):
                    await queued_widget.remove()
                await self._mount_message(UserMessage(command))
                link = Content.styled(url, TStyle(dim=True, italic=True, link=url))
                await self._mount_message(AppMessage(link))

            # Append directly — no dedup; each URL command gets its own output.
            self._deferred_actions.append(
                DeferredAction(kind="chat_output", execute=_mount_output)
            )
            return

        await self._mount_message(UserMessage(command))
        link = Content.styled(url, TStyle(dim=True, italic=True, link=url))
        await self._mount_message(AppMessage(link))

    async def _handle_trace_command(self, command: str) -> None:
        """Open the current thread in LangSmith.

        Resolves the URL and opens the browser immediately regardless of busy
        state. When the app is busy, chat output (user echo + clickable link)
        is deferred until the current task finishes. Error conditions (no
        session, URL failure, tracing not configured) render immediately
        regardless of busy state.

        Args:
            command: The raw command text (displayed as user message).
        """
        from invincat_cli.config import build_langsmith_thread_url

        if not self._session_state:
            await self._mount_message(UserMessage(command))
            await self._mount_message(AppMessage(t("trace.no_active_session")))
            return
        thread_id = self._session_state.thread_id
        try:
            url = await asyncio.to_thread(build_langsmith_thread_url, thread_id)
        except Exception:
            logger.exception("Failed to build LangSmith thread URL for %s", thread_id)
            await self._mount_message(UserMessage(command))
            await self._mount_message(
                AppMessage(t("trace.resolve_failed"))
            )
            return
        if not url:
            await self._mount_message(UserMessage(command))
            await self._mount_message(
                AppMessage(
                    t("trace.not_configured")
                )
            )
            return

        def _open_browser() -> None:
            try:
                webbrowser.open(url)
            except Exception:
                logger.debug("Could not open browser for URL: %s", url, exc_info=True)

        asyncio.get_running_loop().run_in_executor(None, _open_browser)

        # Defer chat output while a turn is in progress — rendering the user
        # echo + link immediately would splice it into the middle of the
        # streaming assistant response
        if self._agent_running or self._shell_running:
            queued_widget = QueuedUserMessage(command)
            self._queued_widgets.append(queued_widget)
            await self._mount_message(queued_widget)

            async def _mount_output() -> None:
                if queued_widget in self._queued_widgets:
                    self._queued_widgets.remove(queued_widget)
                with suppress(Exception):
                    await queued_widget.remove()
                await self._mount_message(UserMessage(command))
                link = Content.styled(url, TStyle(dim=True, italic=True, link=url))
                await self._mount_message(AppMessage(link))

            # Append directly — no dedup; each /trace invocation gets its own output.
            self._deferred_actions.append(
                DeferredAction(kind="chat_output", execute=_mount_output)
            )
            return

        await self._mount_message(UserMessage(command))
        link = Content.styled(url, TStyle(dim=True, italic=True, link=url))
        await self._mount_message(AppMessage(link))

    async def _handle_command(self, command: str) -> None:
        """Handle a slash command.

        Args:
            command: The slash command (including /)
        """
        from invincat_cli.config import settings
        from invincat_cli.i18n import t

        cmd = command.lower().strip()

        if cmd in {"/quit", "/q"}:
            self.exit()
        elif cmd == "/help":
            await self._mount_message(UserMessage(command))
            from invincat_cli.app_runtime.help import build_help_content

            await self._mount_message(AppMessage(build_help_content()))

        elif cmd in {"/changelog", "/docs", "/feedback"}:
            await self._open_url_command(command, cmd)
        elif cmd == "/version":
            await self._mount_message(UserMessage(command))
            await self._mount_message(AppMessage(resolve_version_message()))
        elif cmd == "/clear":
            self._pending_messages.clear()
            self._queued_widgets.clear()
            await self._clear_messages()
            self._context_tokens = 0
            self._tokens_approximate = False
            self._update_tokens(0)
            # Clear status message (e.g., "Interrupted" from previous session)
            self._update_status("")
            # Reset thread to start fresh conversation
            if self._session_state:
                new_thread_id = self._session_state.reset_thread()
                try:
                    banner = self.query_one("#welcome-banner", WelcomeBanner)
                    banner.update_thread_id(new_thread_id)
                except NoMatches:
                    pass
                await self._mount_message(
                    AppMessage(t("success.new_thread").format(thread_id=new_thread_id))
                )
        elif cmd == "/editor":
            await self.action_open_editor()
        elif cmd in {"/offload", "/compact"}:
            await self._mount_message(UserMessage(command))
            await self._handle_offload()
        elif cmd == "/plan":
            await self._handle_plan_task()
        elif cmd == "/exit-plan":
            await self._exit_plan_mode()
        elif cmd == "/threads":
            await self._show_thread_selector()
        elif cmd == "/trace":
            await self._handle_trace_command(command)
        elif cmd == "/update":
            await self._handle_update_command()
        elif cmd == "/auto-update":
            await self._handle_auto_update_toggle()
        elif cmd == "/tokens":
            await self._mount_message(UserMessage(command))
            conversation_tokens = (
                await self._get_conversation_token_count()
                if self._context_tokens > 0
                else None
            )
            await self._mount_message(
                AppMessage(
                    build_tokens_message(
                        context_tokens=self._context_tokens,
                        model_name=settings.model_name,
                        context_limit=settings.model_context_limit,
                        conversation_tokens=conversation_tokens,
                    )
                )
            )
        elif cmd == "/skill-creator" or cmd.startswith("/skill-creator "):
            # Convenience alias for /skill:skill-creator — shorter and
            # discoverable before skill loading completes.
            args = command.strip()[len("/skill-creator") :].strip()
            rewritten = (
                f"/skill:skill-creator {args}" if args else "/skill:skill-creator"
            )
            await self._handle_skill_command(rewritten)
        elif cmd == "/mcp":
            await self._show_mcp_viewer()
        elif cmd == "/memory":
            await self._show_memory_viewer()
        elif cmd == "/wecombot-start":
            await self._handle_wecombot_command(command, action="start")
        elif cmd == "/wecombot-status":
            await self._handle_wecombot_command(command, action="status")
        elif cmd == "/wecombot-stop":
            await self._handle_wecombot_command(command, action="stop")
        elif cmd == "/schedule" or cmd.startswith("/schedule "):
            await self._handle_schedule_command(command)
        elif cmd == "/theme":
            await self._show_theme_selector()
        elif cmd == "/language":
            await self._show_language_selector()
        elif cmd == "/model" or cmd.startswith("/model "):
            action = parse_model_command(command)
            if action.kind == "error":
                await self._mount_message(UserMessage(command))
                await self._mount_message(ErrorMessage(action.error or ""))
                return
            if action.kind == "usage":
                await self._mount_message(UserMessage(command))
                await self._mount_message(AppMessage(MODEL_DEFAULT_USAGE))
            elif action.kind == "clear_default":
                await self._mount_message(UserMessage(command))
                await self._clear_default_model(target=action.target)
            elif action.kind == "set_default" and action.model_arg:
                await self._mount_message(UserMessage(command))
                await self._set_default_model(
                    action.model_arg,
                    target=action.target,
                    apply_to_session=(action.target == "memory"),
                )
            elif action.kind == "switch" and action.model_arg:
                await self._mount_message(UserMessage(command))
                await self._switch_model(
                    action.model_arg,
                    target=action.target,
                    extra_kwargs=action.extra_kwargs,
                )
            else:
                await self._show_model_selector(
                    target=action.target,
                    extra_kwargs=action.extra_kwargs,
                )
        elif cmd == "/reload":
            await self._mount_message(UserMessage(command))
            try:
                changes = settings.reload_from_environment()

                from invincat_cli.model_config import clear_caches

                clear_caches()
            except (OSError, ValueError):
                logger.exception("Failed to reload configuration")
                await self._mount_message(
                    AppMessage(
                        "Failed to reload configuration. Check your .env "
                        "file and environment variables for syntax errors, "
                        "then try again."
                    )
                )
                return

            # Reload user themes from config.toml and re-register with Textual
            theme_reload_ok = True
            try:
                theme.reload_registry()
                self._register_custom_themes()
            except Exception:
                theme_reload_ok = False
                logger.warning("Failed to reload user themes", exc_info=True)

            await self._mount_message(
                AppMessage(
                    build_reload_report(
                        changes,
                        theme_reload_ok=theme_reload_ok,
                    )
                )
            )

            # Re-discover skills so autocomplete reflects any new/removed skills
            self.run_worker(
                self._discover_skills(),
                exclusive=True,
                group="startup-skill-discovery",
            )
        elif cmd.startswith("/skill:"):
            await self._handle_skill_command(command)
        else:
            await self._mount_message(UserMessage(command))
            await self._mount_message(
                AppMessage(t("command.unknown").format(command=cmd))
            )

        # Anchor to bottom so command output stays visible
        with suppress(NoMatches, ScreenStackError):
            self.query_one("#chat", VerticalScroll).anchor()

    # ------------------------------------------------------------------
    # Scheduler integration
    # ------------------------------------------------------------------

    def _start_scheduler(self) -> None:
        """Create SchedulerRunner and start the 60-second tick interval."""
        from invincat_cli.scheduler.runner import SchedulerRunner
        from invincat_cli.scheduler.store import FilteredSchedulerStore

        runner_store = FilteredSchedulerStore(
            db_path=getattr(self._scheduler_store, "_db_path", None),
            exclude_task=lambda task: _wecom_daemon_claims_scheduled_task(
                task, self._cwd
            ),
        )

        self._scheduler_runner = SchedulerRunner(
            runner_store,
            inject_message=self._inject_scheduled_message,
            notify=lambda msg: self.notify(msg, timeout=6),
            is_busy=lambda: self._agent_running or self._shell_running,
            on_timeout=self._handle_scheduled_timeout,
            cwd=self._cwd,
        )
        self._scheduler_interval_handle = self.set_interval(
            60, self._scheduler_tick, pause=False
        )
        # Fire once immediately (after a short delay) for misfire recovery.
        self.set_timer(3, self._scheduler_tick)

    async def _scheduler_tick(self) -> None:
        if self._scheduler_runner is not None:
            await self._scheduler_runner.tick()

    async def _handle_scheduled_timeout(self, run_id: str, task_id: str) -> None:
        self._cancel_timed_out_scheduled_turn(run_id, task_id)
        await self._deliver_scheduled_result_to_wecom(
            task_id=task_id,
            run_id=run_id,
            status="timeout",
            error="Scheduled task timed out",
        )

    def _cancel_timed_out_scheduled_turn(self, run_id: str, task_id: str) -> None:
        """Cancel or dequeue a scheduled turn after SchedulerRunner timeout."""
        self._pending_messages = remove_scheduled_messages(
            self._pending_messages,
            run_id=run_id,
            task_id=task_id,
        )
        if not scheduled_run_matches(
            self._active_scheduled_run,
            run_id=run_id,
            task_id=task_id,
        ):
            return

        if self._pending_approval_widget is not None:
            with suppress(Exception):
                self._pending_approval_widget.action_select_reject()
        if self._pending_ask_user_widget is not None:
            with suppress(Exception):
                self._pending_ask_user_widget.action_cancel()
        if self._shell_worker is not None:
            self._shell_worker.cancel()
        if self._agent_worker is not None:
            self._agent_worker.cancel()
        self._shell_running = False
        self._shell_worker = None
        self._agent_running = False
        self._agent_worker = None
        self._active_turn_is_planner = False
        self._active_scheduled_run = None
        self._scheduled_turn_status = "timeout"
        self._scheduled_turn_error = "Scheduled task timed out"
        logger.warning(
            "scheduled run timed out; cancelled active worker run_id=%s task_id=%s",
            run_id,
            task_id,
        )

    async def _deliver_scheduled_result_to_wecom(
        self,
        *,
        task_id: str,
        run_id: str,
        status: str,
        error: str | None,
    ) -> None:
        """Best-effort active WeCom delivery for a completed scheduled run."""
        from datetime import datetime, timezone
        from pathlib import Path

        from invincat_cli.scheduler.wecom_delivery import (
            build_scheduled_wecom_text,
            latest_assistant_summary,
            scheduled_report_path_for_wecom,
            scheduled_wecom_delivery_target,
        )
        from invincat_cli.wecom.media import upload_wecom_outbound_media
        from invincat_cli.wecom.protocol import (
            build_wecom_file_frame_for_chat,
            build_wecom_text_frame,
        )

        task = self._scheduler_store.load_task(task_id)
        run = self._scheduler_store.load_run(run_id)
        if task is None or run is None:
            return

        has_wecom_channel, chatid = scheduled_wecom_delivery_target(task)
        if not has_wecom_channel:
            self._scheduler_store.update_run_delivery(
                run_id,
                status="none",
                error=None,
                attempts_delta=0,
            )
            return
        if chatid is None:
            self._scheduler_store.update_run_delivery(
                run_id,
                status="failed",
                error="missing chatid",
            )
            await self._mount_message(
                ErrorMessage("Scheduled task WeCom delivery skipped: missing chatid.")
            )
            return

        report_path = scheduled_report_path_for_wecom(task, run)

        all_messages = self._message_store.get_all_messages()
        run_messages = all_messages[self._scheduled_run_message_offset:]
        content = build_scheduled_wecom_text(
            title=task.title,
            status=status,
            summary=latest_assistant_summary(run_messages),
            report_path=report_path,
            error=error,
        )

        try:
            if not wecom_bridge_is_online(self._wecom_bridge):
                self._scheduler_store.update_run_delivery(
                    run_id,
                    status="failed",
                    error=wecom_bridge_offline_message(),
                )
                await self._mount_message(
                    ErrorMessage("Scheduled task WeCom delivery failed: WeCom bridge is offline.")
                )
                return
            self._wecom_enqueue(build_wecom_text_frame(chatid, content))
            flushed = await self._wecom_flush_outbox()
            if not flushed:
                self._scheduler_store.update_run_delivery(
                    run_id,
                    status="queued",
                    error="waiting for bridge reconnect",
                )
                await self._mount_message(
                    AppMessage(
                        "Scheduled task WeCom delivery queued; waiting for bridge reconnect."
                    )
                )
            else:
                self._scheduler_store.update_run_delivery(
                    run_id,
                    status="success",
                    error=None,
                    delivered_at=datetime.now(timezone.utc).isoformat(),
                )
            if status == "success" and report_path:
                if not wecom_bridge_is_online(self._wecom_bridge):
                    await self._mount_message(
                        ErrorMessage(
                            "Scheduled task WeCom file delivery skipped: WeCom bridge is offline."
                        )
                    )
                    return
                media_id = await upload_wecom_outbound_media(
                    Path(report_path),
                    send_request=self._wecom_send_request,
                )
                await self._wecom_send_request(build_wecom_file_frame_for_chat(chatid, media_id))
        except Exception as exc:
            self._scheduler_store.update_run_delivery(
                run_id,
                status="failed",
                error=str(exc),
            )
            logger.warning("Scheduled task WeCom delivery failed: %s", exc, exc_info=True)
            await self._mount_message(
                ErrorMessage(f"Scheduled task WeCom delivery failed: {exc}")
            )

    def _active_scheduled_wecom_chat_id(self) -> str | None:
        """Return the WeCom chat id for the active scheduled run, if any."""
        task_id = active_scheduled_task_id(self._active_scheduled_run)
        if task_id is None:
            return None
        task = self._scheduler_store.load_task(task_id)
        if task is None:
            return None
        from invincat_cli.scheduler.wecom_delivery import scheduled_wecom_chat_id

        return scheduled_wecom_chat_id(task)

    async def _send_scheduled_wecom_file_request(self, payload: dict[str, Any]) -> None:
        """Send a file requested by send_wecom_file during a scheduled WeCom run."""
        from invincat_cli.wecom.media import upload_wecom_outbound_media
        from invincat_cli.wecom.protocol import build_wecom_file_frame_for_chat

        chatid = self._active_scheduled_wecom_chat_id()
        if not chatid:
            raise RuntimeError("Scheduled task has no WeCom delivery target")
        if not wecom_bridge_is_online(self._wecom_bridge):
            raise RuntimeError(wecom_bridge_offline_message())

        path = resolve_scheduled_wecom_file_path(
            payload.get("path"),
            cwd=self._cwd,
        )

        media_id = await upload_wecom_outbound_media(
            path,
            send_request=self._wecom_send_request,
        )
        await self._wecom_send_request(build_wecom_file_frame_for_chat(chatid, media_id))

    async def _inject_scheduled_message(self, task_id: str, run_id: str, prompt: str) -> None:
        """Inject a scheduled task prompt into the TUI message queue."""
        from invincat_cli.i18n import t

        task = self._scheduler_store.load_task(task_id)
        title = task.title if task else task_id
        await self._mount_message(AppMessage(t("schedule.running").format(title=title)))
        self._pending_messages.append(QueuedMessage(
            text=prompt,
            mode="normal",
            scheduled_run_id=run_id,
            scheduled_task_id=task_id,
        ))
        if not (self._agent_running or self._shell_running):
            await self._process_next_from_queue()

    async def _handle_schedule_tool_payload(self, payload: dict) -> None:
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
                    cwd=self._cwd,
                    active_wecom_frame=self._current_wecom_inbound_frame,
                )
            except ValueError as exc:
                await self._mount_message(ErrorMessage(str(exc)))
                return
            self._scheduler_store.save_task(result.task)
            await self._mount_message(
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
            task = self._scheduler_store.load_task(task_id)
            if task is None:
                await self._mount_message(
                    AppMessage(t("schedule.not_found").format(task_id=task_id))
                )
                return
            updates = payload.get("updates", {})
            try:
                task = apply_schedule_update_payload(task, updates)
            except ValueError as exc:
                await self._mount_message(ErrorMessage(str(exc)))
                return
            self._scheduler_store.save_task(task)
            await self._mount_message(
                AppMessage(t("schedule.updated").format(title=task.title))
            )

        elif ptype == "schedule_cancel":
            task_id = payload.get("task_id", "")
            task = self._scheduler_store.load_task(task_id)
            title = task.title if task else task_id
            self._scheduler_store.delete_task(task_id)
            await self._mount_message(
                AppMessage(t("schedule.deleted").format(title=title))
            )

        elif ptype == "schedule_run_now":
            task_id = payload.get("task_id", "")
            title = payload.get("title", task_id)
            task = self._scheduler_store.load_task(task_id)
            if task is None:
                await self._mount_message(
                    AppMessage(t("schedule.not_found").format(task_id=task_id))
                )
                return
            if _wecom_daemon_claims_scheduled_task(task, self._cwd):
                await self._mount_message(
                    AppMessage(
                        "WeCom daemon is running; this scheduled task is handled by the daemon."
                    )
                )
                return
            await self._mount_message(
                AppMessage(t("schedule.run_queued").format(title=title))
            )
            if self._scheduler_runner is not None:
                await self._scheduler_runner.fire_now(task)

        elif ptype == "schedule_list":
            tasks = payload.get("tasks", [])
            if not tasks:
                await self._mount_message(AppMessage(t("schedule.list_empty")))
            else:
                lines = [t("schedule.list_header").format(count=len(tasks))]
                for task_info in tasks:
                    lines.append(format_schedule_list_item(task_info))
                await self._mount_message(AppMessage("\n".join(lines)))

    async def _handle_schedule_command(self, command: str) -> None:
        """Open the schedule manager modal screen."""
        await self._show_schedule_manager()

    async def _show_schedule_manager(self) -> None:
        """Push the ScheduleManagerScreen modal."""
        from invincat_cli.widgets.schedule_manager import ScheduleManagerScreen, ScheduleAction

        screen = ScheduleManagerScreen(store=self._scheduler_store)

        def handle_result(result: "ScheduleAction | None") -> None:
            if self._chat_input:
                self._chat_input.focus_input()
            if result is None:
                return
            # Execute the chosen action after the modal closes
            self.call_later(self._execute_schedule_action, result)

        self.push_screen(screen, handle_result)

    async def _execute_schedule_action(self, action: "ScheduleAction") -> None:  # noqa: F821
        """Execute a schedule action returned by the manager modal."""
        from invincat_cli.i18n import t

        task = self._scheduler_store.load_task(action.task_id)
        if task is None:
            await self._mount_message(
                AppMessage(t("schedule.not_found").format(task_id=action.task_id))
            )
            return

        if action.kind == "run_now":
            if _wecom_daemon_claims_scheduled_task(task, self._cwd):
                await self._mount_message(
                    AppMessage(
                        "WeCom daemon is running; this scheduled task is handled by the daemon."
                    )
                )
                return
            await self._mount_message(
                AppMessage(t("schedule.run_queued").format(title=task.title))
            )
            if self._scheduler_runner is not None:
                await self._scheduler_runner.fire_now(task)

        elif action.kind == "pause":
            self._scheduler_store.set_task_enabled(task.id, False)
            await self._mount_message(
                AppMessage(t("schedule.paused").format(title=task.title))
            )

        elif action.kind == "resume":
            self._scheduler_store.set_task_enabled(task.id, True)
            await self._mount_message(
                AppMessage(t("schedule.resumed").format(title=task.title))
            )

        elif action.kind == "delete":
            self._scheduler_store.delete_task(task.id)
            await self._mount_message(
                AppMessage(t("schedule.deleted").format(title=task.title))
            )

    async def _handle_wecombot_command(self, command: str, *, action: str) -> None:
        """Manage WeCom bridge lifecycle in current CLI session.

        Supported forms:
        - /wecombot-start
        - /wecombot-status
        - /wecombot-stop
        """
        await self._mount_message(UserMessage(command))

        if action == "start":
            if wecom_bot_is_running(self._wecom_task):
                await self._mount_message(AppMessage(wecom_bot_already_running_message()))
                return
            auto_approve_was_enabled = self._auto_approve
            self._on_auto_approve_enabled()
            self._wecom_task = asyncio.create_task(self._run_wecombot_bridge())
            await self._mount_message(
                AppMessage(
                    wecom_bot_started_message(
                        auto_approve_was_enabled=auto_approve_was_enabled,
                    )
                )
            )
            return

        if action == "stop":
            if self._wecom_bridge is not None:
                self._wecom_bridge.stop()
            if wecom_bot_is_running(self._wecom_task):
                self._wecom_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._wecom_task
            self._wecom_task = None
            self._wecom_bridge = None
            await self._mount_message(AppMessage(wecom_bot_stopped_message()))
            return

        if action == "status":
            await self._mount_message(
                AppMessage(
                    wecom_bot_status_message(
                        running=wecom_bot_is_running(self._wecom_task)
                    )
                )
            )
            return

        await self._mount_message(AppMessage(wecom_bot_usage_message()))

    async def _run_wecombot_bridge(self) -> None:
        """Run WeCom long-connection client and bridge to current session."""
        config = load_wecom_bot_config(os.environ)
        if not config.is_complete:
            await self._mount_message(ErrorMessage(wecom_bot_missing_config_message()))
            return

        async def _on_status(message: str) -> None:
            await self._mount_message(AppMessage(message))

        async def _on_error(message: str) -> None:
            await self._mount_message(ErrorMessage(message))

        async def _on_message(frame: dict[str, Any]) -> None:
            await self._wecom_handle_inbound_message(frame=frame)

        bridge = WeComBridge(
            on_status=_on_status,
            on_error=_on_error,
            on_message=_on_message,
            should_exit=lambda: self._exit,
        )
        self._wecom_bridge = bridge
        try:
            await bridge.run(
                bot_id=config.bot_id,
                secret=config.secret,
                ws_url=config.ws_url,
            )
        finally:
            if should_clear_wecom_bridge(
                current_bridge=self._wecom_bridge,
                bridge=bridge,
            ):
                self._wecom_bridge = None

    async def _wecom_handle_inbound_message(
        self,
        *,
        frame: dict[str, Any],
    ) -> None:
        """Process one inbound WeCom message and deliver a true streaming reply."""

        async def _build_agent_input(inbound_frame: dict[str, Any]) -> str:
            return await build_wecom_agent_input_with_media_downloads(
                inbound_frame,
                cwd=self._cwd,
            )

        async def _run_turn(
            text: str,
            inbound_frame: dict[str, Any],
            on_content: Callable[[str], Awaitable[None]],
        ) -> str:
            return await self._process_wecom_message_via_cli(
                text,
                inbound_frame=inbound_frame,
                on_content=on_content,
            )

        responder = create_wecom_message_responder(
            enqueue=self._wecom_enqueue,
            flush=self._wecom_flush_outbox,
            build_agent_input=_build_agent_input,
            run_turn=_run_turn,
            report_error=lambda message: self._mount_message(ErrorMessage(message)),
        )
        await responder.handle(frame)

    def _wecom_enqueue(self, payload: dict[str, Any]) -> None:
        if not wecom_bridge_is_online(self._wecom_bridge):
            logger.debug("Skipping WeCom enqueue while bridge is offline")
            return
        self._wecom_bridge.enqueue(payload)

    async def _wecom_flush_outbox(self) -> bool:
        """Flush pending outbound replies using the current live WS connection.

        Returns False when no connection is available or sending failed; queued
        items are preserved and retried when the next connection is established.
        """
        if not wecom_bridge_is_online(self._wecom_bridge):
            return False
        return await self._wecom_bridge.flush_outbox()

    async def _wecom_send_request(
        self,
        payload: dict[str, Any],
        *,
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Send a WeCom request frame and wait for its matching req_id response."""
        if not wecom_bridge_is_online(self._wecom_bridge):
            raise wecom_bridge_offline_error()
        return await self._wecom_bridge.send_request(payload, timeout=timeout)

    async def _process_wecom_message_via_cli(
        self,
        text: str,
        *,
        inbound_frame: dict[str, Any],
        on_content: Callable[[str], Awaitable[None]] | None = None,
    ) -> str:
        """Inject one WeCom message into the current session and return the final answer.

        on_content, if provided, is called with a one-line progress string while
        the agent works. The complete assistant text is sent only once in the
        final finish=True frame.
        """
        async def _handle_user_message(
            message: str,
            on_text_delta: Callable[[str, str], Awaitable[None]],
            on_wecom_file_request: Callable[[dict[str, Any]], Awaitable[None]],
        ) -> None:
            await self._handle_user_message(
                message,
                on_text_delta=on_text_delta,
                on_wecom_file_request=on_wecom_file_request,
            )

        turn_context = WeComTurnContext(
            get_current_frame=lambda: self._current_wecom_inbound_frame,
            set_current_frame=lambda frame: setattr(
                self,
                "_current_wecom_inbound_frame",
                frame,
            ),
            inbound_frame=inbound_frame,
        )

        runner = WeComTurnRunner(
            lock=self._wecom_lock,
            cwd=self._cwd,
            is_busy=lambda: wecom_turn_is_busy(
                connecting=self._connecting,
                thread_switching=self._thread_switching,
                model_switching=self._model_switching,
                agent_running=self._agent_running,
                shell_running=self._shell_running,
            ),
            get_messages=self._message_store.get_all_messages,
            handle_user_message=_handle_user_message,
            send_request=self._wecom_send_request,
            cancel_timed_out_turn=self._cancel_wecom_timed_out_turn,
            on_content=on_content,
            enter_turn_context=turn_context.enter,
            exit_turn_context=turn_context.exit,
        )
        return await runner.run(text, inbound_frame=inbound_frame)

    async def _handle_skill_command(self, command: str) -> None:
        """Handle a `/skill:<name>` command by loading and invoking a skill.

        Looks up the skill from cached metadata (populated at startup), falling
        back to a fresh filesystem walk on cache miss. Reads the `SKILL.md`
        body, wraps it in a prompt envelope with any user-provided arguments,
        and sends the composed message to the agent.

        Args:
            command: The full command string (e.g., `/skill:web-research find X`).
        """
        from invincat_cli.command_registry import parse_skill_command
        from invincat_cli.skills.load import load_skill_content

        skill_name, args = parse_skill_command(command)
        if not skill_name:
            await self._mount_message(UserMessage(command))
            await self._mount_message(AppMessage(t("skill.usage")))
            return

        # Fast path: look up from the cached discovery results
        cached = find_skill(self._discovered_skills, skill_name)
        allowed_roots = self._skill_allowed_roots

        # Cache miss — fall back to fresh discovery (offloaded to thread)
        if cached is None:
            try:
                skills, allowed_roots = await asyncio.to_thread(
                    self._discover_skills_and_roots
                )
                # Backfill cache so subsequent invocations are fast
                self._discovered_skills = skills
                self._skill_allowed_roots = allowed_roots
                cached = find_skill(skills, skill_name)
            except OSError as exc:
                logger.warning(
                    "Filesystem error loading skill %r", skill_name, exc_info=True
                )
                await self._mount_message(UserMessage(command))
                await self._mount_message(
                    AppMessage(
                        t("skill.load_filesystem_error").format(
                            skill=skill_name,
                            error=str(exc),
                        )
                    )
                )
                return
            except Exception as exc:
                logger.warning(
                    "Error searching for skill %r", skill_name, exc_info=True
                )
                await self._mount_message(UserMessage(command))
                await self._mount_message(
                    AppMessage(
                        t("skill.load_unexpected_error").format(
                            skill=skill_name,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                    )
                )
                return

        if cached is None:
            await self._mount_message(UserMessage(command))
            await self._mount_message(
                AppMessage(t("skill.not_found").format(skill=skill_name))
            )
            return

        # Load SKILL.md content (filesystem I/O offloaded to thread)
        skill_path = cached["path"]

        def _load() -> str | None:
            return load_skill_content(str(skill_path), allowed_roots=allowed_roots)

        try:
            content = await asyncio.to_thread(_load)
        except PermissionError as exc:
            logger.warning(
                "Containment check failed for skill %r", skill_name, exc_info=True
            )
            await self._mount_message(UserMessage(command))
            await self._mount_message(
                AppMessage(
                    t("skill.load_permission_error").format(
                        skill=skill_name,
                        error=str(exc),
                    )
                )
            )
            return
        except OSError as exc:
            logger.warning(
                "Filesystem error loading skill %r", skill_name, exc_info=True
            )
            await self._mount_message(UserMessage(command))
            await self._mount_message(
                AppMessage(
                    t("skill.load_filesystem_error").format(
                        skill=skill_name,
                        error=str(exc),
                    )
                )
            )
            return
        except Exception as exc:
            logger.warning("Error reading skill %r", skill_name, exc_info=True)
            await self._mount_message(UserMessage(command))
            await self._mount_message(
                AppMessage(
                    t("skill.load_unexpected_error").format(
                        skill=skill_name,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
            )
            return

        if content is None:
            await self._mount_message(UserMessage(command))
            await self._mount_message(
                AppMessage(
                    t("skill.content_unreadable").format(skill=skill_name)
                )
            )
            return

        if not content.strip():
            await self._mount_message(UserMessage(command))
            await self._mount_message(
                AppMessage(
                    t("skill.content_empty").format(skill=skill_name)
                )
            )
            return

        prompt = build_skill_invocation_prompt(
            skill=cached,
            content=content,
            args=args,
        )

        await self._mount_message(
            SkillMessage(
                skill_name=cached["name"],
                description=str(cached.get("description", "")),
                source=str(cached.get("source", "")),
                body=content,
                args=args,
            )
        )
        await self._send_to_agent(
            prompt,
            message_kwargs={
                "additional_kwargs": build_skill_agent_metadata(
                    skill=cached,
                    args=args,
                ),
            },
        )

    async def _get_conversation_token_count(self) -> int | None:
        """Return the approximate conversation-only token count.

        Returns:
            Token count as an integer, or `None` if state is unavailable.
        """
        if not self._agent:
            return None
        try:
            from langchain_core.messages.utils import (
                count_tokens_approximately,
            )

            config: RunnableConfig = {
                "configurable": {"thread_id": self._lc_thread_id},
            }
            state = await self._agent.aget_state(config)
            if not state or not state.values:
                return None
            messages = state.values.get("messages", [])
            if not messages:
                return None
            return count_tokens_approximately(messages)
        except Exception:  # best-effort for /tokens display
            logger.debug("Failed to retrieve conversation token count", exc_info=True)
            return None

    async def _maybe_auto_offload(self) -> None:
        """Trigger offload automatically when the context window is nearly full.

        Runs at the end of every agent turn. Returns immediately if the usage
        ratio is below `AUTO_OFFLOAD_THRESHOLD`, the limit is unknown, or a
        cooldown is active.

        A `AUTO_OFFLOAD_COOLDOWN_SECONDS` cooldown is set after every attempt
        (successful or not) to prevent the feedback loop where system-prompt
        overhead keeps the usage ratio above the threshold even after offloading
        conversation messages — which would cause the auto-trigger to fire on
        every subsequent turn.

        Skips when the token count is stale (approximate flag set by an
        interrupted generation) to avoid acting on unreliable data.
        """
        from invincat_cli.config import settings

        decision = resolve_auto_offload_decision(
            tokens_approximate=self._tokens_approximate,
            now=_monotonic(),
            cooldown_until=self._auto_offload_cooldown_until,
            context_tokens=self._context_tokens,
            context_limit=settings.model_context_limit,
            threshold=AUTO_OFFLOAD_THRESHOLD,
            cooldown_seconds=AUTO_OFFLOAD_COOLDOWN_SECONDS,
        )
        if decision is None:
            return

        await self._mount_message(
            AppMessage(build_auto_offload_message(decision))
        )
        await self._handle_offload()
        # Set cooldown regardless of outcome so we don't re-trigger next turn.
        self._auto_offload_cooldown_until = decision.cooldown_until

    async def _maybe_notify_memory_update(self) -> None:
        """Show a status bar notification when memory files were updated this turn.

        Shows "记忆整理中..." immediately, then transitions to the success message
        after a brief pause so the user sees the two-phase notification.
        """
        try:
            state_values = await self._get_thread_state_values(self._lc_thread_id)
            updated_paths = state_values.get("_auto_memory_updated_paths")
            notification = resolve_memory_update_notification(
                updated_paths,
                home=Path.home(),
            )
            if notification is None:
                return
            success_msg = format_memory_update_success(
                notification,
                single_template=t("status.memory_updated"),
                multiple_template=t("status.memory_updated_n"),
            )

            # Phase 1: show "记忆整理中..."
            self._update_status(t("status.memory_updating"))
            if self._memory_status_clear_timer is not None:
                self._memory_status_clear_timer.stop()
            # Phase 2: transition to success message after a short delay
            self._memory_status_clear_timer = self.set_timer(
                0.8, lambda msg=success_msg: self._on_memory_update_done(msg)
            )
        except Exception:
            logger.debug("Failed to check memory update state", exc_info=True)

    def _on_memory_update_done(self, msg: str) -> None:
        """Transition from '记忆整理中...' to the success message."""
        self._update_status(msg)
        if self._memory_status_clear_timer is not None:
            self._memory_status_clear_timer.stop()
        self._memory_status_clear_timer = self.set_timer(4.0, self._clear_memory_status)

    def _clear_memory_status(self) -> None:
        """Clear the memory-update status bar message."""
        self._memory_status_clear_timer = None
        self._update_status("")

    def _resolve_offload_budget_str(self) -> str | None:
        """Resolve the offload retention budget as a human-readable string.

        Result is cached by (provider, model, context_limit, profile_override)
        so repeated calls from `/tokens` and the status bar are cheap.  The
        cache is automatically invalidated when any of those values change
        (e.g. the user switches models with `/model`).

        Returns:
            A string like `"20.0K (10% of 200.0K)"` or
            `"last 6 messages"`, or `None` if the budget cannot be determined.
        """
        from invincat_cli.config import create_model, settings

        cache_key = build_offload_budget_cache_key(
            model_provider=settings.model_provider,
            model_name=settings.model_name,
            model_context_limit=settings.model_context_limit,
            profile_override=self._profile_override,
        )
        if self._offload_budget_cache is not None:
            cached_key, cached_val = self._offload_budget_cache
            if cached_key == cache_key:
                return cached_val

        val: str | None = None
        try:
            from deepagents.middleware.summarization import (
                compute_summarization_defaults,
            )

            model_spec = f"{settings.model_provider}:{settings.model_name}"
            result = create_model(
                model_spec,
                profile_overrides=self._profile_override,
            )
            defaults = compute_summarization_defaults(result.model)
            from invincat_cli.offload import format_offload_limit

            val = format_offload_limit(
                defaults["keep"],
                settings.model_context_limit,
            )
        except Exception:  # best-effort for /tokens display
            logger.debug("Failed to compute offload budget string", exc_info=True)

        self._offload_budget_cache = (cache_key, val)
        return val

    async def _handle_offload(self) -> None:
        """Offload older messages to free context window space."""
        from invincat_cli.config import settings
        from invincat_cli.offload import (
            OffloadModelError,
            OffloadThresholdNotMet,
            perform_offload,
        )

        if not self._agent or not self._lc_thread_id:
            await self._mount_message(
                AppMessage(t("offload.nothing_to_offload"))
            )
            return

        if self._agent_running:
            await self._mount_message(
                AppMessage(t("offload.cannot_while_running"))
            )
            return

        config: RunnableConfig = {"configurable": {"thread_id": self._lc_thread_id}}

        try:
            state_values = await self._get_thread_state_values(self._lc_thread_id)
        except Exception as exc:  # noqa: BLE001
            await self._mount_message(
                ErrorMessage(t("offload.failed_read_state").format(error=str(exc)))
            )
            return

        if not state_values:
            await self._mount_message(
                AppMessage(t("offload.nothing_to_offload"))
            )
            return

        # Prevent concurrent user input while offload modifies state
        self._agent_running = True
        try:
            from invincat_cli.hooks import dispatch_hook

            await dispatch_hook("context.offload", {})
            # Keep old hook name for backward compatibility
            await dispatch_hook("context.compact", {})
            await self._set_spinner(t("status.offloading"))

            from langchain_core.messages.utils import convert_to_messages

            raw_messages = state_values.get("messages", [])
            # Checkpointer may return messages as plain dicts (e.g. after a
            # server restart or when reading from SQLite directly). Convert
            # to LangChain message objects so SummarizationMiddleware can
            # process them — same pattern used in _fetch_thread_history_data.
            if raw_messages and isinstance(raw_messages[0], dict):
                raw_messages = convert_to_messages(raw_messages)

            prior_event = state_values.get("_summarization_event")
            # The summary_message inside prior_event also arrives as a plain
            # dict from the checkpointer. Convert it to a LangChain message
            # object so SummarizationMiddleware can process it.
            if isinstance(prior_event, dict):
                summary_msg_raw = prior_event.get("summary_message")
                if isinstance(summary_msg_raw, dict):
                    converted = convert_to_messages([summary_msg_raw])
                    if converted:
                        prior_event = {**prior_event, "summary_message": converted[0]}

            result = await perform_offload(
                messages=raw_messages,
                prior_event=prior_event,
                thread_id=self._lc_thread_id,
                model_spec=(f"{settings.model_provider}:{settings.model_name}"),
                profile_overrides=self._profile_override,
                context_limit=settings.model_context_limit,
                total_context_tokens=self._context_tokens,
                backend=self._backend,
            )

            if isinstance(result, OffloadThresholdNotMet):
                await self._mount_message(
                    AppMessage(
                        build_offload_threshold_not_met_message(
                            conversation_tokens=result.conversation_tokens,
                            total_context_tokens=result.total_context_tokens,
                            context_limit=result.context_limit,
                            budget_str=result.budget_str,
                        )
                    )
                )
                return

            # OffloadResult — success
            if result.offload_warning:
                await self._mount_message(ErrorMessage(result.offload_warning))

            if remote := self._remote_agent():
                await remote.aensure_thread(config)  # ty: ignore[invalid-argument-type]

            await self._agent.aupdate_state(
                config, {"_summarization_event": result.new_event}
            )

            await self._mount_message(
                AppMessage(
                    build_offload_success_message(
                        messages_offloaded=result.messages_offloaded,
                        tokens_before=result.tokens_before,
                        tokens_after=result.tokens_after,
                        pct_decrease=result.pct_decrease,
                        messages_kept=result.messages_kept,
                    )
                )
            )

            self._on_tokens_update(result.tokens_after)
            from invincat_cli.textual_adapter import _persist_context_tokens

            await _persist_context_tokens(self._agent, config, result.tokens_after)

        except OffloadModelError as exc:
            logger.warning("Offload model creation failed: %s", exc, exc_info=True)
            await self._mount_message(ErrorMessage(str(exc)))
        except Exception as exc:  # surface offload errors to user
            logger.exception("Offload failed")
            await self._mount_message(
                ErrorMessage(t("offload.failed").format(error=str(exc)))
            )
        finally:
            self._agent_running = False
            try:
                await self._set_spinner(None)
            except Exception:  # best-effort spinner cleanup
                logger.exception("Failed to dismiss spinner after offload")

    async def _handle_user_message(
        self,
        message: str,
        *,
        on_text_delta: Callable[[str, str], Awaitable[None]] | None = None,
        on_wecom_file_request: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        """Handle a user message to send to the agent.

        Args:
            message: The user's message
            on_text_delta: Optional callback for each real assistant text chunk.
            on_wecom_file_request: Optional callback for WeCom file-send requests.
        """
        if should_route_message_to_planner(self._session_state):
            await self._mount_message(UserMessage(message))
            planner_started = await self._run_planner(message)
            if not planner_started:
                self._reset_plan_mode_state()
            return

        # Mount the user message
        await self._mount_message(UserMessage(message))
        await self._send_to_agent(
            message,
            on_text_delta=on_text_delta,
            on_wecom_file_request=on_wecom_file_request,
        )

    async def _send_to_agent(
        self,
        message: str,
        *,
        message_kwargs: dict[str, Any] | None = None,
        agent_override: Pregel | None = None,
        thread_id_override: str | None = None,
        post_turn_hook: Callable[[], Awaitable[None]] | None = None,
        on_text_delta: Callable[[str, str], Awaitable[None]] | None = None,
        on_wecom_file_request: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> bool:
        """Send a message to the agent and start execution.

        This is the low-level send path. It does NOT mount any widget — the
        caller is responsible for mounting the appropriate visual representation
        (e.g., `UserMessage`, `SkillMessage`) before calling this method.

        Args:
            message: The prompt to send to the agent.
            message_kwargs: Extra fields merged into the stream input message
                dict (e.g., `additional_kwargs` for skill metadata).
            agent_override: Optional target agent; defaults to the main agent.
            thread_id_override: Optional thread ID used only for this turn.
            post_turn_hook: Optional async callback executed after streaming
                finishes successfully (before cleanup).
            on_text_delta: Optional callback for each real assistant text chunk.
            on_wecom_file_request: Optional callback for WeCom file-send requests.
        """
        # Anchor to bottom so streaming response stays visible
        with suppress(NoMatches, ScreenStackError):
            self.query_one("#chat", VerticalScroll).anchor()

        # If this is a direct user message (not dequeued from the scheduled
        # queue), discard any stale scheduled-run context that may have been
        # left from an interrupted scheduled turn.
        if should_clear_scheduled_run_before_send(
            processing_pending=self._processing_pending
        ):
            self._active_scheduled_run = None

        # Check if agent is available
        target_agent = agent_override or self._agent
        if can_start_agent_turn(
            target_agent=target_agent,
            ui_adapter=self._ui_adapter,
            session_state=self._session_state,
        ):
            self._agent_generation += 1
            generation = self._agent_generation
            self._agent_running = True
            self._active_turn_is_planner = is_planner_agent_turn(
                agent_override=agent_override,
                target_agent=target_agent,
                planner_agent=self._planner_agent,
                thread_id_override=thread_id_override,
                planner_thread_id=self._planner_thread_id,
            )

            if self._chat_input:
                self._chat_input.set_cursor_active(active=False)

            # Use run_worker to avoid blocking the main event loop
            # This allows the UI to remain responsive during agent execution
            self._agent_worker = self.run_worker(
                self._run_agent_task(
                    AgentTurnRequest(
                        message=message,
                        message_kwargs=message_kwargs,
                        generation=generation,
                        agent_override=target_agent,
                        thread_id_override=thread_id_override,
                        post_turn_hook=post_turn_hook,
                        on_text_delta=on_text_delta,
                        on_wecom_file_request=on_wecom_file_request,
                    )
                ),
                exclusive=False,
            )
            return True
        else:
            self._finish_active_scheduled_run_as_failed("Agent not available")
            await self._mount_message(
                AppMessage(t("agent.not_configured_session"))
            )
            return False

    def _finish_active_scheduled_run_as_failed(self, error: str) -> None:
        """Finish the active scheduled run as failed, if one is active."""
        if self._active_scheduled_run is None:
            return

        run_id, task_id = self._active_scheduled_run
        self._active_scheduled_run = None
        if self._scheduler_runner is not None:
            with suppress(Exception):
                self._scheduler_runner.finish_run(
                    run_id,
                    task_id,
                    status="failed",
                    error=error,
                )

    async def _run_agent_task(
        self,
        request: AgentTurnRequest,
    ) -> None:
        """Run the agent task in a background worker.

        This runs in a Textual worker so the main event loop stays responsive.
        """
        # Caller ensures _ui_adapter is set (checked in _handle_user_message)
        if self._ui_adapter is None:
            return
        from invincat_cli.textual_adapter import execute_task_textual

        target_agent = request.agent_override or self._agent
        if target_agent is None or self._session_state is None:
            return
        session_state = self._session_state

        # Create the stats object up-front and store on the app so
        # exit() can merge it synchronously if the worker is cancelled
        # before this method can return (e.g. Ctrl+D during HITL).
        turn_stats = SessionStats()
        self._inflight_turn_stats = turn_stats
        self._inflight_turn_start = time.monotonic()
        thread_context = AgentThreadOverrideContext(
            session_state,
            request.thread_id_override,
        )
        retry_after_exc: BaseException | None = None
        effective_wecom_file_request = resolve_wecom_file_request_handler(
            explicit_handler=request.on_wecom_file_request,
            active_scheduled_wecom_chat_id=self._active_scheduled_wecom_chat_id(),
            scheduled_handler=self._send_scheduled_wecom_file_request,
        )
        try:
            thread_context.enter()
            await execute_task_textual(
                user_input=request.message,
                agent=target_agent,
                assistant_id=self._assistant_id,
                session_state=session_state,
                adapter=self._ui_adapter,
                backend=self._backend,
                image_tracker=self._image_tracker,
                sandbox_type=self._sandbox_type,
                is_planner_turn=self._active_turn_is_planner,
                message_kwargs=request.message_kwargs,
                context=build_agent_cli_context(
                    model=self._model_override,
                    model_params=self._model_params_override,
                    memory_model=self._memory_model_override,
                    memory_model_params=self._memory_model_params_override,
                    wecom_enabled=effective_wecom_file_request is not None,
                    scheduled_run=self._active_scheduled_run is not None,
                ),
                turn_stats=turn_stats,
                on_text_delta=request.on_text_delta,
                on_wecom_file_request=effective_wecom_file_request,
                on_schedule_payload=self._handle_schedule_tool_payload,
            )
            if request.post_turn_hook is not None:
                await request.post_turn_hook()
        except Exception as e:  # Resilient tool rendering
            if await self._handle_agent_task_exception(e):
                retry_after_exc = e
        finally:
            thread_context.exit()
            # Merge turn stats before cleanup — _cleanup_agent_task may raise
            # during teardown (widget removal on a torn-down DOM), and stats
            # should ideally be captured regardless.
            # exit() clears _inflight_turn_stats when it merges, so
            # checking for None prevents double-counting.
            if self._inflight_turn_stats is not None:
                self._session_stats.merge(turn_stats)
                self._inflight_turn_stats = None
            if retry_after_exc is not None:
                await asyncio.sleep(_SCHEDULED_TRANSIENT_RETRY_DELAY_SECONDS)
                await self._run_agent_task(request)
                return
            await self._cleanup_agent_task(generation=request.generation)

    async def _handle_agent_task_exception(self, exc: BaseException) -> bool:
        """Handle a failed agent turn and return whether it should retry."""
        scheduled_retryable = should_retry_scheduled_turn(
            active_scheduled_run=self._active_scheduled_run,
            retry_used=self._scheduled_turn_retry_used,
            exc=exc,
        )
        if scheduled_retryable:
            self._scheduled_turn_retry_used = True
            logger.warning(
                "Scheduled run transient agent error; retrying once after %.1fs",
                _SCHEDULED_TRANSIENT_RETRY_DELAY_SECONDS,
                exc_info=True,
            )
            with suppress(Exception):
                await self._mount_message(
                    AppMessage(
                        "Scheduled task hit a transient model/network error; retrying once..."
                    )
                )
        else:
            self._scheduled_turn_status = "failed"
            self._scheduled_turn_error = build_agent_error_detail(exc)

        logger.exception("Agent execution failed")
        error_detail = self._agent_error_detail_with_server_log(exc)
        if self._ui_adapter:
            self._ui_adapter.finalize_pending_tools_with_error(
                t("agent.error").format(error=error_detail)
            )
        if not scheduled_retryable:
            try:
                await self._mount_message(
                    ErrorMessage(t("agent.error").format(error=error_detail))
                )
            except Exception:
                logger.debug(
                    "Could not mount error message (app closing?)",
                    exc_info=True,
                )
        return scheduled_retryable

    def _agent_error_detail_with_server_log(self, exc: BaseException) -> str:
        """Build agent error detail, including server log tail when useful."""
        server_log_tail: str | None = None
        if self._server_proc is not None:
            try:
                server_log_tail = self._server_proc.read_log_tail(max_chars=4000)
            except Exception:
                logger.debug("Failed to read server log tail", exc_info=True)
        return build_agent_error_detail(exc, server_log_tail=server_log_tail)

    async def _process_next_from_queue(self) -> None:
        """Process the next message from the queue if any exist.

        Dequeues and processes the next pending message in FIFO order.
        Uses the `_processing_pending` flag to prevent reentrant execution.
        """
        if self._processing_pending or not self._pending_messages or self._exit:
            return

        self._processing_pending = True
        try:
            msg = self._pending_messages.popleft()

            scheduled_state = queued_scheduled_run_state(
                msg,
                message_offset=self._message_store.total_count,
            )
            self._active_scheduled_run = scheduled_state.active_run
            if scheduled_state.message_offset is not None:
                self._scheduled_run_message_offset = scheduled_state.message_offset
            self._scheduled_turn_status = scheduled_state.turn_status
            self._scheduled_turn_error = scheduled_state.turn_error
            self._scheduled_turn_retry_used = scheduled_state.retry_used

            # Remove the ephemeral queued-message widget
            if self._queued_widgets:
                widget = self._queued_widgets.popleft()
                await widget.remove()

            await self._process_message(msg.text, msg.mode)
        except Exception as _queue_exc:
            logger.exception("Failed to process queued message")
            self._finish_active_scheduled_run_as_failed(str(_queue_exc))
            await self._mount_message(
                ErrorMessage(
                    t("queue.process_failed").format(message=msg.text[:60])
                )
            )
        finally:
            self._processing_pending = False

        # Command mode messages complete synchronously without spawning
        # a worker, so cleanup won't fire again. Continue draining the
        # queue if no worker was started.
        busy = self._agent_running or self._shell_running
        if not busy and self._pending_messages:
            await self._process_next_from_queue()

    async def _cleanup_agent_task(self, *, generation: int = 0) -> None:
        """Clean up after agent task completes or is cancelled.

        Args:
            generation: The `_agent_generation` value captured when this task
                started.  Running-flag cleanup is skipped when a newer agent
                has already taken over (i.e., `generation` is stale), preventing
                a shielded-but-cancelled old worker from clobbering the flags of
                the new concurrent worker that started after ESC was pressed.
        """
        is_current_generation = is_current_agent_generation(
            generation=generation,
            current_generation=self._agent_generation,
        )
        if is_current_generation:
            self._agent_running = False
            self._agent_worker = None
            self._active_turn_is_planner = False

        # Remove spinner if present
        await self._set_spinner(None)

        if is_current_generation and self._chat_input:
            self._chat_input.set_cursor_active(active=True)

        # Ensure token display is restored (in case of early cancellation).
        # Pass the cached approximate flag so an interrupted "+" isn't clobbered.
        if is_current_generation:
            self._show_tokens(approximate=self._tokens_approximate)

        if not is_current_generation:
            # A newer agent took over — skip queue drain, deferred actions, and
            # auto-offload so they don't interfere with the new agent's turn.
            # But still clear any stale scheduled-run context so the next
            # user turn isn't wrongly treated as a scheduled run.
            self._finish_active_scheduled_run_as_failed("Interrupted by user")
            logger.debug(
                "Skipping stale cleanup for generation %d (current: %d)",
                generation,
                self._agent_generation,
            )
            return

        try:
            await self._maybe_drain_deferred()
        except Exception:
            logger.exception("Failed to drain deferred actions during agent cleanup")
            with suppress(Exception):
                await self._mount_message(
                    ErrorMessage(
                        "A deferred action failed after task completion. "
                        "You may need to retry the operation."
                    )
                )

        # Deferred actions may start a new run (for example approved plan
        # handoff to the main agent). Avoid post-cleanup side effects from the
        # old run once a new run is already active.
        if not should_continue_after_deferred_actions(
            agent_running=self._agent_running,
            shell_running=self._shell_running,
        ):
            return

        # Auto-offload when context window is near full (no-op when below threshold)
        try:
            await self._maybe_auto_offload()
        except Exception:
            logger.exception("Auto-offload failed during agent cleanup")

        # Notify user if memory files were updated this turn
        await self._maybe_notify_memory_update()

        # Must happen before draining queue so the next message doesn't
        # overwrite _active_scheduled_run first.
        await self._complete_active_scheduled_run()
        await self._drain_scheduler_if_idle()

        # Process next message from queue if any
        await self._process_next_from_queue()

    async def _complete_active_scheduled_run(self) -> None:
        """Record completion and WeCom delivery for the active scheduled run."""
        if self._active_scheduled_run is None:
            return

        run_id, task_id = self._active_scheduled_run
        self._active_scheduled_run = None
        try:
            if self._scheduler_runner is not None:
                run = self._scheduler_store.load_run(run_id)
                if run is None or run.finished_at is None:
                    try:
                        await self._deliver_scheduled_result_to_wecom(
                            task_id=task_id,
                            run_id=run_id,
                            status=self._scheduled_turn_status,
                            error=self._scheduled_turn_error,
                        )
                    except Exception:
                        logger.exception(
                            "Failed to deliver scheduled run %r to WeCom",
                            run_id,
                        )
                try:
                    self._scheduler_runner.finish_run(
                        run_id,
                        task_id,
                        status=self._scheduled_turn_status,
                        error=self._scheduled_turn_error,
                    )
                except Exception:
                    logger.exception("Failed to finish scheduled run %r", run_id)
        finally:
            self._scheduled_turn_error = None
            self._scheduled_turn_retry_used = False

    async def _drain_scheduler_if_idle(self) -> None:
        """Drain scheduler fire-now queue when no foreground task is running."""
        if self._scheduler_runner is None or self._agent_running or self._shell_running:
            return
        await self._scheduler_runner.drain_pending_now()

    async def _get_thread_state_values(self, thread_id: str) -> dict[str, Any]:
        """Fetch thread state values, with remote checkpointer fallback.

        In server mode the LangGraph dev server can report an empty thread state
        after a restart even when checkpoints exist on disk. When that happens,
        read the latest checkpoint directly so resumed threads can still load
        history and offload correctly.

        Args:
            thread_id: Thread ID to fetch from checkpoint storage.

        Returns:
            Thread state values keyed by channel name. Returns an empty dict
                when no checkpointed values are available.
        """
        if not self._agent:
            return {}

        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        state = await self._agent.aget_state(config)

        values: dict[str, Any] = {}
        if state and state.values:
            values = dict(state.values)

        messages = values.get("messages")
        if isinstance(messages, list) and messages:
            return values
        if not self._remote_agent():
            return values

        logger.debug(
            "Remote state empty for thread %s; falling back to local checkpointer",
            thread_id,
        )
        fallback_values = await self._read_channel_values_from_checkpointer(thread_id)
        return merge_thread_state_with_fallback(values, fallback_values)

    async def _fetch_thread_history_data(self, thread_id: str) -> ThreadHistoryPayload:
        """Fetch and convert stored messages for a thread.

        In server mode the LangGraph dev server starts with an empty thread
        store, so `aget_state` via the HTTP API returns no messages even when
        checkpoints exist on disk. We fall back to reading the SQLite
        checkpointer directly to guarantee resumed threads load their history.

        Args:
            thread_id: Thread ID to fetch from checkpoint storage.

        Returns:
            Payload containing converted message data and the persisted
            context-token count.
        """
        state_values = await self._get_thread_state_values(thread_id)
        return await asyncio.to_thread(
            thread_history_payload_from_state_values,
            state_values,
        )

    @staticmethod
    async def _read_channel_values_from_checkpointer(thread_id: str) -> dict[str, Any]:
        """Read checkpoint channel values directly from the SQLite checkpointer.

        Args:
            thread_id: Thread ID to look up.

        Returns:
            Channel values from the latest checkpoint, or an empty dict on
                failure.
        """
        try:
            from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

            from invincat_cli.sessions import get_db_path

            db_path = str(get_db_path())
            config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
            async with AsyncSqliteSaver.from_conn_string(db_path) as saver:
                tup = await saver.aget_tuple(config)
                if tup and tup.checkpoint:
                    channel_values = tup.checkpoint.get("channel_values", {})
                    if isinstance(channel_values, dict):
                        return dict(channel_values)
        except (ImportError, OSError) as exc:
            logger.warning(
                "Failed to read checkpointer directly for %s: %s",
                thread_id,
                exc,
            )
        except Exception:
            logger.warning(
                "Unexpected error reading checkpointer for %s",
                thread_id,
                exc_info=True,
            )
        return {}

    async def _upgrade_thread_message_link(
        self,
        widget: AppMessage,
        *,
        prefix: str,
        thread_id: str,
    ) -> None:
        """Upgrade a plain thread message to a linked one when URL resolves.

        Args:
            widget: The already-mounted app message.
            prefix: Text prefix before thread ID.
            thread_id: Thread ID to resolve.
        """
        try:
            thread_msg = await build_thread_message(prefix, thread_id)
            if not isinstance(thread_msg, Content):
                logger.debug(
                    "Skipping thread link upgrade for %s: URL did not resolve",
                    thread_id,
                )
                return
            if widget.parent is None:
                logger.debug(
                    "Skipping thread link upgrade for %s: widget no longer mounted",
                    thread_id,
                )
                return
            # Keep serialized content in sync with the rendered content.
            widget._content = thread_msg
            widget.update(thread_msg)
        except Exception:
            logger.warning(
                "Failed to upgrade thread message link for %s",
                thread_id,
                exc_info=True,
            )

    def _schedule_thread_message_link(
        self,
        widget: AppMessage,
        *,
        prefix: str,
        thread_id: str,
    ) -> None:
        """Schedule thread URL link resolution and apply updates in the background.

        Args:
            widget: The message widget to update.
            prefix: Text prefix before thread ID.
            thread_id: Thread ID to resolve.
        """
        self.run_worker(
            self._upgrade_thread_message_link(
                widget,
                prefix=prefix,
                thread_id=thread_id,
            ),
            exclusive=False,
        )

    async def _load_thread_history(
        self,
        *,
        thread_id: str | None = None,
        preloaded_payload: ThreadHistoryPayload | None = None,
    ) -> None:
        """Load and render message history when resuming a thread.

        When `preloaded_payload` is provided (e.g., from `_resume_thread`),
        this reuses that data. Otherwise, it fetches checkpoint state from the
        agent and converts stored messages into lightweight `MessageData`
        objects. The method then bulk-loads into the `MessageStore` and mounts
        only the last `WINDOW_SIZE` widgets to reduce DOM operations on large
        threads.

        Args:
            thread_id: Optional explicit thread ID to load.

                Defaults to current.
            preloaded_payload: Optional pre-fetched history payload for the
                thread.
        """
        history_thread_id = thread_id or self._lc_thread_id
        if not history_thread_id:
            logger.debug("Skipping history load: no thread ID available")
            return
        if preloaded_payload is None and not self._agent:
            logger.debug(
                "Skipping history load for %s: no active agent and no preloaded data",
                history_thread_id,
            )
            return

        try:
            # Fetch + convert, or reuse preloaded payload on thread switch.
            payload = (
                preloaded_payload
                if preloaded_payload is not None
                else await self._fetch_thread_history_data(history_thread_id)
            )
            if not payload.messages:
                return

            # Seed token cache from persisted state
            if payload.context_tokens > 0:
                self._on_tokens_update(payload.context_tokens)

            # 3. Bulk load into store (sets visible window)
            _archived, visible = self._message_store.bulk_load(payload.messages)

            # Update message count display
            if self._status_bar:
                self._status_bar.set_message_count(self._message_store.total_count)

            # 5. Cache container ref (single query)
            try:
                messages_container = self.query_one("#messages", Container)
            except NoMatches:
                return

            # 6-7. Create and mount only visible widgets (max WINDOW_SIZE)
            widgets = [msg_data.to_widget() for msg_data in visible]
            if widgets:
                await messages_container.mount(*widgets)

            # 8. Render content for AssistantMessage after mount
            assistant_updates = [
                widget.set_content(msg_data.content)
                for widget, msg_data in zip(widgets, visible, strict=False)
                if isinstance(widget, AssistantMessage) and msg_data.content
            ]
            if assistant_updates:
                assistant_results = await asyncio.gather(
                    *assistant_updates,
                    return_exceptions=True,
                )
                for error in assistant_results:
                    if isinstance(error, Exception):
                        logger.warning(
                            "Failed to render assistant history message for %s: %s",
                            history_thread_id,
                            error,
                        )

            # 9. Show a brief summary of prior session activity, then the footer.
            summary = build_resume_summary(payload.messages, payload.context_tokens)
            if summary:
                await self._mount_message(AppMessage(summary))

            thread_msg_widget = AppMessage(
                t("thread.resumed").format(thread_id=history_thread_id)
            )
            await self._mount_message(thread_msg_widget)
            self._schedule_thread_message_link(
                thread_msg_widget,
                prefix="Resumed thread",
                thread_id=history_thread_id,
            )

            # 10. Scroll once to bottom after history loads
            def scroll_to_end() -> None:
                with suppress(NoMatches):
                    chat = self.query_one("#chat", VerticalScroll)
                    chat.scroll_end(animate=False, immediate=True)

            self.set_timer(0.1, scroll_to_end)

        except Exception as e:  # Resilient history loading
            logger.exception(
                "Failed to load thread history for %s",
                history_thread_id,
            )
            await self._mount_message(
                AppMessage(t("thread.history_load_failed").format(error=str(e)))
            )

    async def _mount_message(
        self, widget: Static | AssistantMessage | ToolCallMessage | SkillMessage
    ) -> None:
        """Mount a message widget to the messages area.

        This method also stores the message data and handles pruning
        when the widget count exceeds the maximum.

        If the ``#messages`` container is not present (e.g. the screen has
        been torn down during an interruption), the call is silently skipped
        to avoid cascading `NoMatches` errors.

        Args:
            widget: The message widget to mount
        """
        try:
            messages = self.query_one("#messages", Container)
        except NoMatches:
            return

        # During shutdown (e.g. Ctrl+D mid-stream) the container may still
        # be in the DOM tree but already detached, so mount() would raise
        # MountError. Bail out silently — the app is exiting anyway.
        if not messages.is_attached:
            return

        # Store message data for virtualization
        message_data = MessageData.from_widget(widget)
        # Ensure the widget's DOM id matches the store id so that
        # features like click-to-show-timestamp can look it up.
        if not widget.id:
            widget.id = message_data.id
        self._message_store.append(message_data)

        # Update message count display
        if self._status_bar:
            self._status_bar.set_message_count(self._message_store.total_count)

        # Queued-message widgets must always stay at the bottom so they
        # remain visually anchored below the current agent response.
        if isinstance(widget, QueuedUserMessage):
            await messages.mount(widget)
        else:
            await self._mount_before_queued(messages, widget)

        # Prune old widgets if window exceeded
        await self._prune_old_messages()

        # Scroll to keep input bar visible
        try:
            input_container = self.query_one("#bottom-app-container", Container)
            input_container.scroll_visible()
        except NoMatches:
            pass

    async def _prune_old_messages(self) -> None:
        """Prune oldest message widgets if we exceed the window size.

        This removes widgets from the DOM but keeps data in MessageStore
        for potential re-hydration when scrolling up.
        """
        if not self._message_store.window_exceeded():
            return

        try:
            messages_container = self.query_one("#messages", Container)
        except NoMatches:
            logger.debug("Skipping pruning: #messages container not found")
            return

        to_prune = self._message_store.get_messages_to_prune()
        if not to_prune:
            return

        pruned_ids: list[str] = []
        # Build set of widgets still actively tracked (pending/running tool calls).
        # We must not prune these — their ToolMessage result hasn't arrived yet,
        # and removing them from the DOM would leave the result with no target
        # widget.  The fallback hydration path would re-create them, but that
        # adds latency and a visible flash.
        active_tool_widgets: set[object] = set()
        if self._ui_adapter is not None:
            active_tool_widgets = set(self._ui_adapter._current_tool_messages.values())

        for msg_data in to_prune:
            try:
                widget = messages_container.query_one(f"#{msg_data.id}")

                # Skip widgets that are still in the live tracking map — their
                # result hasn't arrived yet and removing them now would cause
                # the incoming ToolMessage to silently lose its target.
                if widget in active_tool_widgets:
                    logger.debug(
                        "Skipping prune of in-flight tool widget id=%s "
                        "(still awaiting ToolMessage result)",
                        msg_data.id,
                    )
                    continue

                if (
                    msg_data.type == "tool"
                    and self._ui_adapter is not None
                ):
                    # Remove ALL keys that point to this widget, not just the
                    # first one.  A widget can accumulate multiple keys when it
                    # is re-keyed from an index-based key ("0") to its real UUID
                    # during streaming; leaving stale keys causes Strategy 3
                    # (name-match fallback) to find a "pending" widget that is
                    # about to be removed, stealing the result from the correct
                    # newly-created widget in the next turn.
                    stale_keys = [
                        k
                        for k, v in self._ui_adapter._current_tool_messages.items()
                        if v is widget
                    ]
                    for key in stale_keys:
                        self._ui_adapter._current_tool_messages.pop(key, None)
                        logger.debug(
                            "Removed tool message from tracking: key=%s id=%s",
                            key,
                            msg_data.id,
                        )
                try:
                    await widget.remove()
                except Exception:  # noqa: BLE001
                    # widget.remove() failed — do not mark as pruned so the
                    # store stays consistent with whatever the DOM state is.
                    logger.warning(
                        "Failed to remove widget %s during pruning; "
                        "skipping to keep store/DOM in sync",
                        msg_data.id,
                        exc_info=True,
                    )
                    continue
                pruned_ids.append(msg_data.id)
            except NoMatches:
                # Widget not in the DOM.  Two distinct cases:
                #
                # 1. Historical (non-streaming) message whose widget was never
                #    mounted because bulk_load() interleaved with streaming
                #    append() calls: the visible window grew but the widget was
                #    never created, so there is nothing to desync.  Force-mark
                #    as pruned so _visible_start advances and the window stays
                #    bounded.
                #
                # 2. Streaming message: its widget IS being constructed — skip
                #    so we don't prune a widget that is mid-mount.
                if msg_data.is_streaming:
                    logger.debug(
                        "Widget %s not found but still streaming; skipping prune",
                        msg_data.id,
                    )
                else:
                    logger.debug(
                        "Widget %s not in DOM and not streaming; "
                        "force-advancing window to prevent unbounded growth",
                        msg_data.id,
                    )
                    pruned_ids.append(msg_data.id)

        if pruned_ids:
            self._message_store.mark_pruned(pruned_ids)

    def _set_active_message(self, message_id: str | None) -> None:
        """Set the active streaming message (won't be pruned).

        Args:
            message_id: The ID of the active message, or None to clear.
        """
        self._message_store.set_active_message(message_id)

    def _sync_message_content(self, message_id: str, content: str) -> None:
        """Sync final message content back to the store after streaming.

        Called when streaming finishes so the store holds the full text
        instead of the empty string captured at mount time.

        Args:
            message_id: The ID of the message to update.
            content: The final content after streaming.
        """
        self._message_store.update_message(
            message_id,
            content=content,
            is_streaming=False,
        )

    async def _clear_messages(self) -> None:
        """Clear the messages area and message store."""
        # Clear the message store first
        self._message_store.clear()
        try:
            messages = self.query_one("#messages", Container)
            await messages.remove_children()
        except NoMatches:
            logger.warning(
                "Messages container (#messages) not found during clear; "
                "UI may be out of sync with message store"
            )

    def _pop_last_queued_message(self) -> None:
        """Remove the most recently queued message (LIFO).

        If the chat input is empty the evicted text is restored there so the
        user can edit and re-submit. Otherwise the message is discarded. The
        toast message distinguishes between the two outcomes.

        Caller must ensure `_pending_messages` is non-empty. A defensive guard
        is included in case of async TOCTOU races.
        """
        if not self._pending_messages:
            return

        # Guard: the two deques must stay in lockstep (each enqueue appends to
        # both; each dequeue removes from both).  If they differ in length the
        # tracking is already corrupted — abort rather than remove the wrong
        # widget or leave a dangling message with no visual counterpart.
        if len(self._pending_messages) != len(self._queued_widgets):
            logger.error(
                "_pending_messages (%d) and _queued_widgets (%d) are out of sync; "
                "skipping pop to avoid mismatched removal. "
                "Call _discard_queue() to reset both deques.",
                len(self._pending_messages),
                len(self._queued_widgets),
            )
            return

        msg = self._pending_messages.pop()
        widget = self._queued_widgets.pop()
        # Textual's Widget.remove() is safe to call from sync context — it
        # posts a removal message to the event loop and returns an awaitable
        # that can optionally be awaited for completion.  Not awaiting here is
        # intentional: the caller (action_interrupt) is a sync action handler
        # and the DOM update will be applied on the next layout pass.
        widget.remove()

        if not self._chat_input:
            logger.warning(
                "Chat input unavailable during queue pop; "
                "message text cannot be restored: %s",
                msg.text[:60],
            )
            self.notify(t("queue.discarded"), timeout=2)
            return

        if not self._chat_input.value.strip():
            self._chat_input.value = msg.text
            self.notify(t("queue.moved_to_input"), timeout=2)
        else:
            self.notify(t("queue.discarded_input_not_empty"), timeout=3)

    def _discard_queue(self) -> None:
        """Clear pending messages, deferred actions, and queued widgets."""
        self._pending_messages.clear()
        for w in self._queued_widgets:
            w.remove()
        self._queued_widgets.clear()
        self._deferred_actions.clear()

    def _defer_action(self, action: DeferredAction) -> None:
        """Queue a deferred action, replacing any existing action of the same kind.

        Last-write-wins: if the user selects a model twice while busy, only the
        final selection runs.

        Args:
            action: The deferred action to queue.
        """
        self._deferred_actions = [
            a for a in self._deferred_actions if a.kind != action.kind
        ]
        self._deferred_actions.append(action)

    async def _maybe_drain_deferred(self) -> None:
        """Drain deferred actions unless a server connection is still in progress."""
        if not self._connecting:
            await self._drain_deferred_actions()
            if (
                self._pending_plan_handoff_prompt
                and not (self._agent_running or self._shell_running or self._connecting)
            ):
                prompt = self._pending_plan_handoff_prompt
                self._pending_plan_handoff_prompt = None
                try:
                    await self._execute_plan_handoff(prompt)
                except Exception:
                    self._pending_plan_handoff_prompt = prompt
                    raise

    async def _drain_deferred_actions(self) -> None:
        """Execute deferred actions queued while busy (e.g. model/thread switch)."""
        while self._deferred_actions:
            action = self._deferred_actions.pop(0)
            try:
                await action.execute()
            except Exception:
                logger.exception(
                    "Failed to execute deferred action %r (callable=%r)",
                    action.kind,
                    action.execute,
                )
                label = action.kind.replace("_", " ")
                with suppress(Exception):
                    await self._mount_message(
                        ErrorMessage(
                            f"Deferred {label} failed unexpectedly. "
                            "You may need to retry the operation."
                        )
                    )

    def _cancel_worker(self, worker: Worker[None] | None) -> None:
        """Discard the message queue and cancel an active worker.

        Args:
            worker: The worker to cancel.
        """
        self._discard_queue()
        # Immediately clear running flags to prevent race condition.
        # worker.cancel() is async and only sets a cancellation flag,
        # so _agent_running may still be True when the user sends a new
        # message immediately after ESC. This causes the message to be
        # queued instead of processed directly.
        if worker is not None:
            worker.cancel()
        # Clear flags immediately after requesting cancellation
        self._agent_running = False
        self._agent_worker = None
        self._active_turn_is_planner = False

    def _cancel_wecom_timed_out_turn(self) -> None:
        """Cancel a WeCom-injected turn after its bridge timeout.

        Unlike user-triggered cancellation, this must not discard locally queued
        messages. The remote user has already received a timeout, so continuing
        the worker in the background risks later file sends or state writes
        leaking into subsequent WeCom turns.
        """
        if self._shell_worker is not None:
            self._shell_worker.cancel()
        if self._agent_worker is not None:
            self._agent_worker.cancel()
        self._shell_running = False
        self._shell_worker = None
        self._agent_running = False
        self._agent_worker = None
        self._active_turn_is_planner = False
        logger.warning(
            "wecom turn timed out after %.1fs; cancelled active agent/shell worker",
            WECOM_AGENT_TIMEOUT,
        )

    def action_quit_or_interrupt(self) -> None:
        """Handle Ctrl+C - interrupt agent, reject approval, or quit on double press.

        Priority order:
        1. If shell command is running, kill it
        2. If approval menu is active, reject it
        3. If agent is running, interrupt it (preserve input)
        4. If double press (quit_pending), quit
        5. Otherwise show quit hint
        """
        # If shell command is running, cancel the worker
        if self._shell_running and self._shell_worker:
            self._cancel_worker(self._shell_worker)
            self._quit_pending = False
            return

        # If approval menu is active, reject it before cancelling the agent worker.
        # During HITL the agent worker remains active while awaiting approval,
        # so this must be checked before the worker cancellation branch to
        # avoid leaving a stale approval widget interactive after interruption.
        if self._pending_approval_widget:
            self._pending_approval_widget.action_select_reject()
            self._quit_pending = False
            return

        # If ask_user menu is active, cancel it before cancelling the agent
        # worker, following the same pattern as the approval widget above.
        if self._pending_ask_user_widget:
            self._pending_ask_user_widget.action_cancel()
            self._quit_pending = False
            return

        # If agent is running, interrupt it and discard queued messages
        if self._agent_running and self._agent_worker:
            self._cancel_worker(self._agent_worker)
            self._quit_pending = False
            return

        # Double Ctrl+C to quit
        if self._quit_pending:
            self.exit()
        else:
            self._arm_quit_pending("Ctrl+C")

    def _arm_quit_pending(self, shortcut: str) -> None:
        """Set the pending-quit flag and show a matching hint.

        Args:
            shortcut: The key chord to show in the quit hint.
        """
        self._quit_pending = True
        quit_timeout = 3
        self.notify(
            t("app.press_to_quit", shortcut=shortcut), timeout=quit_timeout, markup=False
        )
        self.set_timer(quit_timeout, lambda: setattr(self, "_quit_pending", False))

    def action_interrupt(self) -> None:
        """Handle escape key.

        Priority order:
        1. If modal screen is active, dismiss it
        2. If completion popup is open, dismiss it
        3. If input is in command/shell mode, exit to normal mode
        4. If shell command is running, kill it
        5. If approval menu is active, reject it
        6. If ask-user menu is active, cancel it
        7. If queued messages exist, pop the last one (LIFO)
        8. If agent is running, interrupt it
        """
        from invincat_cli.widgets.thread_selector import ThreadSelectorScreen

        if (
            isinstance(self.screen, ThreadSelectorScreen)
            and self.screen.is_delete_confirmation_open
        ):
            self.screen.action_cancel()
            return

        # If a modal screen is active, let it cancel itself (so it can
        # restore state, e.g. the theme selector reverts the previewed theme).
        # Fall back to a plain dismiss for modals without action_cancel.
        if isinstance(self.screen, ModalScreen):
            cancel = getattr(self.screen, "action_cancel", None)
            if cancel is not None:
                cancel()
            else:
                self.screen.dismiss(None)
            return

        # Close completion popup or exit slash/shell command mode
        if self._chat_input:
            if self._chat_input.dismiss_completion():
                return
            if self._chat_input.exit_mode():
                return

        # If shell command is running, cancel the worker
        if self._shell_running and self._shell_worker:
            self._cancel_worker(self._shell_worker)
            return

        # If approval menu is active, reject it before cancelling the agent worker.
        # During HITL the agent worker remains active while awaiting approval,
        # so this must be checked before the worker cancellation branch to
        # avoid leaving a stale approval widget interactive after interruption.
        if self._pending_approval_widget:
            self._pending_approval_widget.action_select_reject()
            return

        # If ask_user menu is active, cancel it before cancelling the agent
        # worker, following the same pattern as the approval widget above.
        if self._pending_ask_user_widget:
            self._pending_ask_user_widget.action_cancel()
            return

        # If queued messages exist, pop the last one (LIFO) instead of
        # interrupting the agent.  This lets the user retract queued messages
        # one at a time; once the queue is empty the next ESC will interrupt.
        if self._pending_messages:
            self._pop_last_queued_message()
            return

        # If agent is running, interrupt it and discard queued messages
        if self._agent_running and self._agent_worker:
            self._cancel_worker(self._agent_worker)
            return

    def action_quit_app(self) -> None:
        """Handle quit action (Ctrl+D)."""
        from invincat_cli.widgets.thread_selector import (
            DeleteThreadConfirmScreen,
            ThreadSelectorScreen,
        )

        if isinstance(self.screen, ThreadSelectorScreen):
            self.screen.action_delete_thread()
            return
        if isinstance(self.screen, DeleteThreadConfirmScreen):
            if self._quit_pending:
                self.exit()
                return
            self._arm_quit_pending("Ctrl+D")
            return
        self.exit()

    def exit(
        self,
        result: Any = None,  # noqa: ANN401  # Dynamic LangGraph stream result type
        return_code: int = 0,
        message: Any = None,  # noqa: ANN401  # Dynamic LangGraph message type
    ) -> None:
        """Exit the app, restoring iTerm2 cursor guide if applicable.

        Overrides parent to restore iTerm2's cursor guide before Textual's
        cleanup. The atexit handler serves as a fallback for abnormal
        termination.

        Args:
            result: Return value passed to the app runner.
            return_code: Exit code (non-zero for errors).
            message: Optional message to display on exit.
        """
        # Merge in-flight turn stats before any cleanup that might raise.
        # When the agent worker is cancelled (e.g. Ctrl+D during a pending tool
        # call), the worker's finally block will see _inflight_turn_stats is
        # already None and skip the merge.
        inflight = self._inflight_turn_stats
        if inflight is not None:
            self._inflight_turn_stats = None
            if not inflight.wall_time_seconds:
                inflight.wall_time_seconds = (
                    time.monotonic() - self._inflight_turn_start
                )
            self._session_stats.merge(inflight)

        # Discard queued messages so _cleanup_agent_task won't try to
        # process them after the event loop is torn down, and cancel
        # active workers so their subprocesses are terminated
        # (SIGTERM → SIGKILL) instead of being orphaned.
        self._discard_queue()

        if self._shell_running and self._shell_worker:
            self._shell_worker.cancel()
        if self._agent_running and self._agent_worker:
            self._agent_worker.cancel()
        if self._wecom_task and not self._wecom_task.done():
            if self._wecom_bridge is not None:
                self._wecom_bridge.stop()
            self._wecom_task.cancel()

        # Dispatch synchronously — the event loop is about to be torn down by
        # super().exit(), so an async task would never complete.
        from invincat_cli.hooks import _dispatch_hook_sync, _load_hooks

        hooks = _load_hooks()
        if hooks:
            payload = json.dumps(
                {
                    "event": "session.end",
                    "thread_id": getattr(self, "_lc_thread_id", ""),
                }
            ).encode()
            _dispatch_hook_sync("session.end", payload, hooks)

        _write_iterm_escape(_ITERM_CURSOR_GUIDE_ON)
        super().exit(result=result, return_code=return_code, message=message)

    def action_toggle_auto_approve(self) -> None:
        """Toggle auto-approve mode for the current session.

        When enabled, all tool calls (shell execution, file writes/edits,
        web search, URL fetch) run without prompting. Updates the status
        bar indicator and session state.
        """
        from invincat_cli.widgets.thread_selector import ThreadSelectorScreen

        if isinstance(self.screen, ThreadSelectorScreen):
            self.screen.action_focus_previous_filter()
            return
        # shift+tab is reused for navigation inside modal screens (e.g.
        # ModelSelectorScreen); skip the toggle so it doesn't fire through.
        if isinstance(self.screen, ModalScreen):
            return
        # Delegate shift+tab to ask_user navigation when interview is active.
        if self._pending_ask_user_widget is not None:
            self._pending_ask_user_widget.action_previous_question()
            return
        self._auto_approve = not self._auto_approve
        if self._status_bar:
            self._status_bar.set_auto_approve(enabled=self._auto_approve)
        if self._session_state:
            self._session_state.auto_approve = self._auto_approve

    def action_toggle_tool_output(self) -> None:
        """Toggle expand/collapse of the most recent tool output or skill body."""
        # Try skill messages first (most recent collapsible content)
        with suppress(NoMatches):
            skill_messages = list(self.query(SkillMessage))
            for skill_msg in reversed(skill_messages):
                if skill_msg._stripped_body.strip():
                    skill_msg.toggle_body()
                    return
        # Fall back to tool messages with output
        with suppress(NoMatches):
            tool_messages = list(self.query(ToolCallMessage))
            for tool_msg in reversed(tool_messages):
                if tool_msg.has_output:
                    tool_msg.toggle_output()
                    return

    # Approval menu action handlers (delegated from App-level bindings)
    # NOTE: These only activate when approval widget is pending
    # AND input is not focused
    def action_approval_up(self) -> None:
        """Handle up arrow in approval menu."""
        # Only handle if approval is active
        # (input handles its own up for history/completion)
        if self._pending_approval_widget and not self._is_input_focused():
            self._pending_approval_widget.action_move_up()

    def action_approval_down(self) -> None:
        """Handle down arrow in approval menu."""
        if self._pending_approval_widget and not self._is_input_focused():
            self._pending_approval_widget.action_move_down()

    def action_approval_select(self) -> None:
        """Handle enter in approval menu."""
        # Only handle if approval is active AND input is not focused
        if self._pending_approval_widget and not self._is_input_focused():
            self._pending_approval_widget.action_select()

    def _is_input_focused(self) -> bool:
        """Check if the chat input (or its text area) has focus.

        Returns:
            True if the input widget has focus, False otherwise.
        """
        if not self._chat_input:
            return False
        focused = self.focused
        if focused is None:
            return False
        # Check if focused widget is the text area inside chat input
        return focused.id == "chat-input" or focused in self._chat_input.walk_children()

    def action_approval_yes(self) -> None:
        """Handle yes/1 in approval menu."""
        if self._pending_approval_widget:
            self._pending_approval_widget.action_select_approve()

    def action_approval_auto(self) -> None:
        """Handle auto/2 in approval menu."""
        if self._pending_approval_widget:
            self._pending_approval_widget.action_select_auto()

    def action_approval_no(self) -> None:
        """Handle no/3 in approval menu."""
        if self._pending_approval_widget:
            self._pending_approval_widget.action_select_reject()

    def action_approval_escape(self) -> None:
        """Handle escape in approval menu - reject."""
        if self._pending_approval_widget:
            self._pending_approval_widget.action_select_reject()

    async def action_open_editor(self) -> None:
        """Open the current prompt text in an external editor ($VISUAL/$EDITOR)."""
        from invincat_cli.io.editor import open_in_editor

        chat_input = self._chat_input
        if not chat_input or not chat_input._text_area:
            return

        current_text = chat_input._text_area.text or ""

        edited: str | None = None
        try:
            with self.suspend():
                edited = open_in_editor(current_text)
        except Exception:
            logger.warning("External editor failed", exc_info=True)
            self.notify(
                t("app.external_editor_failed"),
                severity="error",
                timeout=5,
            )
            chat_input.focus_input()
            return

        if edited is not None:
            chat_input._text_area.text = edited
            lines = edited.split("\n")
            chat_input._text_area.move_cursor((len(lines) - 1, len(lines[-1])))
        chat_input.focus_input()

    def on_paste(self, event: Paste) -> None:
        """Route unfocused paste events to chat input for drag/drop reliability."""
        if not self._chat_input:
            return
        if (
            self._pending_approval_widget
            or self._pending_ask_user_widget
            or self._is_input_focused()
        ):
            return
        if self._chat_input.handle_external_paste(event.text):
            event.prevent_default()
            event.stop()

    def on_app_focus(self) -> None:
        """Restore chat input focus when the terminal regains OS focus.

        When the user opens a link via `webbrowser.open`, OS focus shifts to
        the browser. On returning to the terminal, Textual fires `AppFocus`
        (requires a terminal that supports FocusIn events). Re-focusing the chat
        input here keeps it ready for typing.
        """
        if not self._chat_input:
            return
        if isinstance(self.screen, ModalScreen):
            return
        if self._pending_approval_widget or self._pending_ask_user_widget:
            return
        self._chat_input.focus_input()

    def on_click(self, _event: Click) -> None:
        """Handle clicks anywhere in the terminal to focus on the command line."""
        if not self._chat_input:
            return
        # Don't steal focus from approval or ask_user widgets
        if self._pending_approval_widget or self._pending_ask_user_widget:
            return
        self.call_after_refresh(self._chat_input.focus_input)

    def on_mouse_up(self, event: MouseUp) -> None:  # noqa: ARG002  # Textual event handler signature
        """Copy selection to clipboard on mouse release."""
        from invincat_cli.io.clipboard import copy_selection_to_clipboard

        copy_selection_to_clipboard(self)

    # =========================================================================
    # Model Switching
    # =========================================================================

    async def _show_model_selector(
        self,
        *,
        target: ModelTarget = "primary",
        extra_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Show interactive model selector as a modal screen.

        Args:
            target: Selection target (`'primary'` or `'memory'`).
            extra_kwargs: Extra constructor kwargs from `--model-params`.
        """
        from functools import partial

        from invincat_cli.config import settings
        from invincat_cli.model_config import ModelSpec
        from invincat_cli.widgets.model_selector import ModelSelectorScreen

        current_primary_spec = None
        if settings.model_provider and settings.model_name:
            current_primary_spec = f"{settings.model_provider}:{settings.model_name}"

        current_memory_spec = self._memory_model_override
        if current_memory_spec is None:
            current_memory_spec = current_primary_spec

        def _parse_spec(spec: str | None) -> tuple[str | None, str | None]:
            if not spec:
                return None, None
            parsed = ModelSpec.try_parse(spec)
            if parsed:
                return parsed.provider, parsed.model
            return None, spec

        current_provider, current_model = _parse_spec(current_primary_spec)
        memory_provider, memory_model = _parse_spec(current_memory_spec)

        def handle_result(result: tuple[str, str, ModelTarget] | None) -> None:
            """Handle the model selector result."""
            if result is not None:
                model_spec, _, selected_target = result
                if self._agent_running or self._shell_running or self._connecting:
                    self._defer_action(
                        DeferredAction(
                            kind=f"model_switch_{selected_target}",
                            execute=partial(
                                self._switch_model,
                                model_spec,
                                target=selected_target,
                                extra_kwargs=extra_kwargs,
                                persist_as_default=True,
                            ),
                        )
                    )
                    self.notify(
                        t("app.model_switch_pending"), timeout=3
                    )
                else:
                    self.call_later(
                        partial(
                            self._switch_model,
                            model_spec,
                            target=selected_target,
                            extra_kwargs=extra_kwargs,
                            persist_as_default=True,
                        )
                    )
            # Refocus input after modal closes
            if self._chat_input:
                self._chat_input.focus_input()

        screen = ModelSelectorScreen(
            current_model=current_model,
            current_provider=current_provider,
            current_memory_model=memory_model,
            current_memory_provider=memory_provider,
            initial_target=target,
            cli_profile_override=self._profile_override,
        )
        self.push_screen(screen, handle_result)

    def _register_custom_themes(self) -> None:
        """Register all custom themes (built-in LC + user-defined) with Textual."""
        for name, entry in theme.ThemeEntry.REGISTRY.items():
            if entry.custom:
                c = entry.colors
                try:
                    self.register_theme(
                        Theme(
                            name=name,
                            primary=c.primary,
                            secondary=c.secondary,
                            accent=c.accent,
                            foreground=c.foreground,
                            background=c.background,
                            surface=c.surface,
                            panel=c.panel,
                            warning=c.warning,
                            error=c.error,
                            success=c.success,
                            dark=entry.dark,
                            variables={
                                "footer-key-foreground": c.primary,
                            },
                        )
                    )
                except Exception:
                    logger.warning(
                        "Failed to register theme '%s'; skipping",
                        name,
                        exc_info=True,
                    )

    async def _show_theme_selector(self) -> None:
        """Show interactive theme selector as a modal screen."""
        from invincat_cli.widgets.theme_selector import ThemeSelectorScreen

        # Capture scroll state.  The submit handler may have already caused
        # a reflow that re-anchored to the bottom, so we save the *current*
        # offset and release the anchor to prevent further drift while the
        # modal is open.
        chat = self.query_one("#chat", VerticalScroll)
        saved_y = chat.scroll_y
        was_anchored = chat.is_anchored
        chat.release_anchor()

        def handle_result(result: str | None) -> None:
            """Handle the theme selector result."""
            if result is not None:
                self.theme = result
                self.refresh_css(animate=False)

                async def _persist() -> None:
                    try:
                        ok = await asyncio.to_thread(save_theme_preference, result)
                        if not ok:
                            self.notify(
                                t("app.theme_not_saved"),
                                severity="warning",
                                timeout=6,
                                markup=False,
                            )
                    except Exception:
                        logger.warning(
                            "Failed to persist theme preference",
                            exc_info=True,
                        )
                        self.notify(
                            t("app.theme_not_saved"),
                            severity="warning",
                            timeout=6,
                            markup=False,
                        )

                self.call_later(_persist)
            # Restore scroll position, then re-anchor if it was anchored.
            chat.scroll_to(y=saved_y, animate=False)
            if was_anchored:
                chat.anchor()
            if self._chat_input:
                self._chat_input.focus_input()

        screen = ThemeSelectorScreen(current_theme=self.theme)
        self.push_screen(screen, handle_result)

    async def _show_language_selector(self) -> None:
        """Show interactive language selector as a modal screen."""
        from invincat_cli.i18n import Language, get_i18n
        from invincat_cli.widgets.language_selector import LanguageSelectorScreen

        chat = self.query_one("#chat", VerticalScroll)
        saved_y = chat.scroll_y
        was_anchored = chat.is_anchored
        chat.release_anchor()

        def handle_result(result: Language | None) -> None:
            """Handle the language selector result."""
            if result is not None:
                i18n = get_i18n()
                lang_name = i18n.get_language_name(result)
                self.notify(
                    t("app.language_changed_to", language=lang_name),
                    severity="information",
                    timeout=3,
                )
                self._refresh_all_ui_text()
            chat.scroll_to(y=saved_y, animate=False)
            if was_anchored:
                chat.anchor()
            if self._chat_input:
                self._chat_input.focus_input()

        i18n = get_i18n()
        screen = LanguageSelectorScreen(current_language=i18n.language)
        self.push_screen(screen, handle_result)

    def _refresh_all_ui_text(self) -> None:
        """Refresh all UI text to reflect language change."""
        from invincat_cli.command_registry import COMMANDS, build_skill_commands

        try:
            banner = self.query_one("#welcome-banner", WelcomeBanner)
            banner.update(banner._build_banner(banner._project_url))
        except NoMatches:
            pass

        try:
            status_bar = self.query_one(StatusBar)
            status_bar.refresh()
        except NoMatches:
            pass

        try:
            if self._chat_input:
                slash_commands = [
                    (cmd.name, cmd.description, cmd.hidden_keywords) for cmd in COMMANDS
                ]
                if self._discovered_skills:
                    cmds = build_skill_commands(self._discovered_skills)
                    merged = slash_commands + cmds
                else:
                    merged = slash_commands
                self._chat_input.update_slash_commands(merged)
        except Exception:
            pass

    async def _show_mcp_viewer(self) -> None:
        """Show read-only MCP server/tool viewer as a modal screen."""
        from invincat_cli.widgets.mcp_viewer import MCPViewerScreen

        screen = MCPViewerScreen(server_info=self._mcp_server_info or [])

        def handle_result(result: None) -> None:  # noqa: ARG001
            if self._chat_input:
                self._chat_input.focus_input()

        self.push_screen(screen, handle_result)

    def _resolve_memory_store_paths(self) -> dict[str, str]:
        """Resolve user/project memory store paths for the current session."""
        from invincat_cli.config import settings

        assistant_id = self._assistant_id or "agent"
        user_store = settings.get_agent_dir(assistant_id) / "memory_user.json"

        store_paths: dict[str, str] = {"user": str(user_store.expanduser().resolve())}
        from invincat_cli.project_utils import find_project_root

        cwd = Path(self._cwd).expanduser().resolve()
        project_root = find_project_root(cwd)
        if project_root is not None:
            project_store_dir = project_root / ".invincat"
        else:
            project_store_dir = cwd / ".invincat"
        store_paths["project"] = str(
            (project_store_dir / "memory_project.json").expanduser().resolve()
        )
        return store_paths

    async def _show_memory_viewer(self) -> None:
        """Show memory manager modal with live store state."""
        from invincat_cli.widgets.memory_viewer import MemoryViewerScreen

        screen = MemoryViewerScreen(
            memory_store_paths=self._resolve_memory_store_paths(),
        )

        def handle_result(result: None) -> None:  # noqa: ARG001
            if self._chat_input:
                self._chat_input.focus_input()

        self.push_screen(screen, handle_result)

    async def _show_thread_selector(self) -> None:
        """Show interactive thread selector as a modal screen."""
        from functools import partial

        from invincat_cli.sessions import get_cached_threads, get_thread_limit
        from invincat_cli.widgets.thread_selector import ThreadSelectorScreen

        current = self._session_state.thread_id if self._session_state else None
        thread_limit = get_thread_limit()

        initial_threads = get_cached_threads(limit=thread_limit, require_message_counts=True)

        def handle_result(result: str | None) -> None:
            """Handle the thread selector result."""
            if result is not None:
                if self._agent_running or self._shell_running or self._connecting:
                    self._defer_action(
                        DeferredAction(
                            kind="thread_switch",
                            execute=partial(self._resume_thread, result),
                        )
                    )
                    self.notify(
                        t("app.thread_switch_pending"), timeout=3
                    )
                else:
                    self.call_later(self._resume_thread, result)
            if self._chat_input:
                self._chat_input.focus_input()

        screen = ThreadSelectorScreen(
            current_thread=current,
            thread_limit=thread_limit,
            initial_threads=initial_threads,
        )
        self.push_screen(screen, handle_result)

    def _update_welcome_banner(
        self,
        thread_id: str,
        *,
        missing_message: str,
        warn_if_missing: bool,
    ) -> None:
        """Update the welcome banner thread ID when the banner is mounted.

        Args:
            thread_id: Thread ID to display on the banner.
            missing_message: Log message template when banner is missing.
            warn_if_missing: Whether to log missing-banner cases at warning level.
        """
        try:
            banner = self.query_one("#welcome-banner", WelcomeBanner)
            banner.update_thread_id(thread_id)
        except NoMatches:
            if warn_if_missing:
                logger.warning(missing_message, thread_id)
            else:
                logger.debug(missing_message, thread_id)

    async def _resume_thread(self, thread_id: str) -> None:
        """Resume a previously saved thread.

        Fetches the selected thread history, then atomically switches UI state.
        Prefetching first avoids clearing the active chat when history loading
        fails.

        Args:
            thread_id: The thread ID to resume.
        """
        block_reason = thread_resume_block_reason(
            has_agent=self._agent is not None,
            has_session=self._session_state is not None,
            current_thread_id=(
                self._session_state.thread_id if self._session_state else None
            ),
            requested_thread_id=thread_id,
            switching=self._thread_switching,
        )
        if block_reason is not None:
            await self._mount_message(
                AppMessage(
                    t(thread_resume_block_message_key(block_reason)).format(
                        thread_id=thread_id
                    )
                )
            )
            return

        assert self._session_state is not None

        # Save previous state for rollback on failure
        prev_thread_id = self._lc_thread_id
        prev_session_thread = self._session_state.thread_id
        self._thread_switching = True
        if self._chat_input:
            self._chat_input.set_cursor_active(active=False)

        prefetched_payload: ThreadHistoryPayload | None = None
        try:
            self._update_status(thread_loading_status(thread_id))
            prefetched_payload = await self._fetch_thread_history_data(thread_id)

            # Clear conversation (similar to /clear, without creating a new thread)
            self._pending_messages.clear()
            self._queued_widgets.clear()
            await self._clear_messages()
            self._context_tokens = 0
            self._tokens_approximate = False
            self._update_tokens(0)
            self._update_status("")

            # Switch to the selected thread
            self._session_state.thread_id = thread_id
            self._lc_thread_id = thread_id

            self._update_welcome_banner(
                thread_id,
                missing_message="Welcome banner not found during thread switch to %s",
                warn_if_missing=False,
            )

            # Load thread history
            await self._load_thread_history(
                thread_id=thread_id,
                preloaded_payload=prefetched_payload,
            )
        except Exception as exc:
            if prefetched_payload is None:
                logger.exception("Failed to prefetch history for thread %s", thread_id)
                await self._mount_message(
                    AppMessage(
                        thread_switch_failed_message(
                            thread_id=thread_id,
                            error=exc,
                        )
                    )
                )
                return
            logger.exception("Failed to switch to thread %s", thread_id)
            # Restore previous thread IDs so the user can retry
            self._session_state.thread_id = prev_session_thread
            self._lc_thread_id = prev_thread_id
            self._update_welcome_banner(
                prev_session_thread,
                missing_message=(
                    "Welcome banner not found during rollback to thread %s; "
                    "banner may display stale thread ID"
                ),
                warn_if_missing=True,
            )
            rollback_restore_failed = False
            # Attempt to restore the previous thread's visible history
            try:
                await self._clear_messages()
                await self._load_thread_history(thread_id=prev_session_thread)
            except Exception:  # Resilient session state saving
                rollback_restore_failed = True
                msg = (
                    "Could not restore previous thread history after failed "
                    "switch to %s"
                )
                logger.warning(msg, thread_id, exc_info=True)
            await self._mount_message(
                AppMessage(
                    thread_switch_failed_message(
                        thread_id=thread_id,
                        error=exc,
                        rollback_restore_failed=rollback_restore_failed,
                    )
                )
            )
        finally:
            self._thread_switching = False
            self._update_status("")
            if self._chat_input:
                self._chat_input.set_cursor_active(active=not self._agent_running)

    async def _switch_model(
        self,
        model_spec: str,
        *,
        target: ModelTarget = "primary",
        extra_kwargs: dict[str, Any] | None = None,
        persist_as_default: bool = False,
    ) -> None:
        """Switch to a new model, preserving conversation history.

        This requires a server-backed interactive session. It sets a model
        override that `ConfigurableModelMiddleware` picks up on the next
        invocation, so the conversation thread stays intact and no server
        restart is required.

        Args:
            model_spec: The model specification to switch to.

                Can be in `provider:model` format
                (e.g., `'anthropic:claude-sonnet-4-5'`) or just the model name
                for auto-detection.
            target: Switch target (`'primary'` for main/planner, `'memory'`
                for memory agent extraction model).
            extra_kwargs: Extra constructor kwargs from `--model-params`.
            persist_as_default: Whether to persist this selected model as the
                target's default preference.
        """
        from invincat_cli.config import create_model, detect_provider, settings
        from invincat_cli.model_config import (
            clear_caches,
            get_credential_env_var,
            get_target_model_params,
            has_provider_credentials,
            save_recent_model,
        )

        logger.info("Switching %s model to %s", target, model_spec)

        if self._model_switching:
            await self._mount_message(AppMessage(t("model.switch_in_progress")))
            return

        self._model_switching = True
        try:
            current_model_name = settings.model_name
            current_model_provider = settings.model_provider

            clear_caches()

            resolved = resolve_model_spec(
                model_spec,
                detect_provider=detect_provider,
            )

            has_creds = (
                has_provider_credentials(resolved.provider)
                if resolved.provider
                else None
            )
            if has_creds is False and resolved.provider is not None:
                detail = missing_credentials_detail(
                    resolved.provider,
                    get_credential_env_var=get_credential_env_var,
                )
                await self._mount_message(
                    ErrorMessage(t("model.missing_credentials").format(detail=detail))
                )
                return
            if has_creds is None and resolved.provider:
                logger.debug(
                    "Credentials for provider '%s' cannot be verified;"
                    " proceeding anyway",
                    resolved.provider,
                )

            target_kwargs = extra_kwargs
            if target_kwargs is None:
                saved_target_kwargs = get_target_model_params(target, resolved.display)
                target_kwargs = saved_target_kwargs or None

            remote_agent = self._remote_agent()
            can_start_deferred_server = (
                target == "primary"
                and self._server_kwargs is not None
                and not self._connecting
            )
            if remote_agent is None and not can_start_deferred_server:
                await self._mount_message(
                    ErrorMessage(t("model.switch_requires_server"))
                )
                return

            if is_target_already_using(
                target=target,
                resolved=resolved,
                current_provider=current_model_provider,
                current_model_name=current_model_name,
                memory_model_override=self._memory_model_override,
            ):
                current = (
                    f"{current_model_provider}:{current_model_name}"
                    if target == "primary"
                    else resolved.display
                )
                await self._mount_message(
                    AppMessage(t("model.already_using").format(model=current))
                )
                return

            try:
                model_result = create_model(
                    resolved.display,
                    extra_kwargs=target_kwargs,
                    profile_overrides=self._profile_override,
                )
            except Exception as exc:
                logger.exception(
                    "Failed to resolve model metadata for %s",
                    resolved.display,
                )
                await self._mount_message(
                    ErrorMessage(t("model.switch_failed").format(error=str(exc)))
                )
                return

            if target == "primary":
                model_result.apply_to_settings()
                self._model_override = resolved.display
                self._model_params_override = target_kwargs
                self._invalidate_planner_agent_cache()
                if remote_agent is None:
                    self._model = model_result.model

                if self._status_bar:
                    self._status_bar.set_model(
                        provider=model_result.provider or "",
                        model=model_result.model_name or "",
                    )
                    if self._memory_model_override is None:
                        self._status_bar.set_memory_model(
                            provider=model_result.provider or "",
                            model=model_result.model_name or "",
                            follow_primary=True,
                        )

                if remote_agent is None and self._server_kwargs is not None:
                    self._server_kwargs["model_name"] = resolved.display
                    self._server_kwargs["model_params"] = target_kwargs
                    self._model_kwargs = None
                    self._defer_server_start = False
                    self._connecting = True
                    with suppress(NoMatches):
                        banner = self.query_one("#welcome-banner", WelcomeBanner)
                        banner.set_connecting()
                    self.run_worker(
                        self._start_server_background,
                        exclusive=True,
                        group="server-startup",
                    )

                if not await asyncio.to_thread(save_recent_model, resolved.display):
                    await self._mount_message(
                        ErrorMessage(
                            t("model.preference_save_failed")
                        )
                    )
                else:
                    await self._mount_message(
                        AppMessage(
                            t("model.switched_to").format(model=resolved.display)
                        )
                    )
                logger.info("Primary model switched to %s", resolved.display)
            else:
                self._memory_model_override = resolved.display
                self._memory_model_params_override = target_kwargs
                if self._status_bar:
                    self._status_bar.set_memory_model(
                        provider=model_result.provider or "",
                        model=model_result.model_name or "",
                        follow_primary=False,
                    )
                await self._mount_message(
                    AppMessage(
                        t("model.memory_switched_to").format(model=resolved.display)
                    )
                )
                logger.info("Memory model switched to %s", resolved.display)

            if persist_as_default:
                await self._set_default_model(
                    resolved.display,
                    target=target,
                    announce=False,
                )

            # Anchor to bottom so the confirmation message is visible
            with suppress(NoMatches, ScreenStackError):
                self.query_one("#chat", VerticalScroll).anchor()
        finally:
            self._model_switching = False

    async def _set_default_model(
        self,
        model_spec: str,
        *,
        target: ModelTarget = "primary",
        announce: bool = True,
        apply_to_session: bool = False,
    ) -> bool:
        """Set the default model target in config without switching session.

        Updates `[models].default` (primary) or `[models].memory_default`
        (memory) in `~/.invincat/config.toml`.

        Args:
            model_spec: The model specification (e.g., `'anthropic:claude-opus-4-6'`).
            target: Which target default to persist (`'primary'` / `'memory'`).
            announce: Whether to emit user-facing success/failure messages.
            apply_to_session: Whether to also apply this default immediately
                to current in-memory session state for the target.
        """
        from invincat_cli.config import detect_provider
        from invincat_cli.model_config import (
            save_default_model,
            save_memory_default_model,
        )

        model_spec = normalize_default_model_spec(
            model_spec,
            detect_provider=detect_provider,
        )

        save_fn = choose_default_model_save_fn(
            target,
            save_default_model=save_default_model,
            save_memory_default_model=save_memory_default_model,
        )
        target_label = t(model_target_translation_key(target))

        if await asyncio.to_thread(save_fn, model_spec):
            if apply_to_session and target == "memory":
                self._memory_model_override = model_spec
                self._memory_model_params_override = None
                if self._status_bar:
                    mem_provider, mem_model = split_model_spec(model_spec)
                    self._status_bar.set_memory_model(
                        provider=mem_provider,
                        model=mem_model,
                        follow_primary=False,
                    )
            if announce:
                await self._mount_message(
                    AppMessage(
                        t("model.default_target_set_to").format(
                            target=target_label, spec=model_spec
                        )
                    )
                )
            return True
        else:
            if announce:
                await self._mount_message(
                    ErrorMessage(
                        t("model.failed_target_save").format(target=target_label)
                    )
                )
            return False

    async def _clear_default_model(self, *, target: ModelTarget = "primary") -> None:
        """Remove default model target from config.

        For primary model, launches fall back to `[models].recent` or
        environment auto-detection. For memory model, launches follow primary.
        """
        from invincat_cli.model_config import (
            clear_default_model,
            clear_memory_default_model,
        )

        clear_fn = choose_default_model_clear_fn(
            target,
            clear_default_model=clear_default_model,
            clear_memory_default_model=clear_memory_default_model,
        )
        target_label = t(model_target_translation_key(target))

        if await asyncio.to_thread(clear_fn):
            await self._mount_message(
                AppMessage(
                    t("model.default_target_cleared").format(target=target_label)
                )
            )
        else:
            await self._mount_message(
                ErrorMessage(
                    t("model.failed_target_clear").format(target=target_label)
                )
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
    """Run the Textual application.

    When `server_kwargs` is provided (and `agent` is `None`), the app starts
    immediately with a "Connecting..." banner and launches the server in the
    background.  Server cleanup is handled automatically after the app exits.

    Args:
        agent: Pre-configured LangGraph agent (optional).
        assistant_id: Agent identifier for memory storage.
        backend: Backend for file operations.
        auto_approve: Whether to start with auto-approve enabled.
        cwd: Current working directory to display.
        thread_id: Thread ID for the session.

            `None` when `resume_thread` is provided (the TUI resolves the final
            ID asynchronously).
        resume_thread: Raw resume intent from `-r` flag. `'__MOST_RECENT__'` for
            bare `-r`, a thread ID string for `-r <id>`, or `None` for new
            sessions.

            Resolved asynchronously during TUI startup.
        initial_prompt: Optional prompt to auto-submit when session starts.
        mcp_server_info: MCP server metadata for the `/mcp` viewer.
        profile_override: Extra profile fields from `--profile-override`,
            retained so later profile-aware behavior stays consistent with
            the CLI override, including model selection details, offload
            budget display, and on-demand `create_model()` calls such
            as `/offload`.
        server_proc: LangGraph server process for the interactive session.
        server_kwargs: Kwargs for deferred `start_server_and_get_agent` call.
        mcp_preload_kwargs: Kwargs for concurrent MCP metadata preload.
        model_kwargs: Kwargs for deferred `create_model()` call.

            When provided, model creation runs in a background worker after
            first paint so the splash screen appears immediately.
        defer_server_start: Keep server startup deferred until a primary model
            is selected.

    Returns:
        An `AppResult` with the return code and final thread ID.
    """
    app = DeepAgentsApp(
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
    )
    try:
        await app.run_async()
    finally:
        # Guarantee server cleanup regardless of how the app exits.
        # Covers both the pre-started server_proc path and the deferred
        # server_kwargs path (where the background worker sets _server_proc).
        if app._server_proc is not None:
            app._server_proc.stop()

    return AppResult(
        return_code=app.return_code or 0,
        thread_id=app._lc_thread_id,
        session_stats=app._session_stats,
        update_available=app._update_available,
    )
