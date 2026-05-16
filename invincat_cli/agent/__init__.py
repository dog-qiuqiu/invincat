"""Agent management and creation for the CLI."""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, LocalShellBackend
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware import SkillsMiddleware

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from deepagents.backends.sandbox import SandboxBackendProtocol
    from deepagents.middleware.async_subagents import AsyncSubAgent
    from deepagents.middleware.subagents import SubAgent
    from langchain.agents.middleware import InterruptOnConfig
    from langchain.tools import BaseTool
    from langchain_core.language_models import BaseChatModel
    from langgraph.checkpoint.base import BaseCheckpointSaver
    from langgraph.pregel import Pregel

    from invincat_cli.mcp.tools import MCPServerInfo

from langchain.agents.middleware.types import AgentMiddleware

from invincat_cli import theme as theme  # noqa: F401
from invincat_cli.config import (
    _ShellAllowAll,
    config,
    console,  # noqa: F401
    get_glyphs,  # noqa: F401
    settings,
)
from invincat_cli.configurable_model import ConfigurableModelMiddleware
from invincat_cli.integrations.sandbox_factory import (
    get_default_working_dir as get_default_working_dir,  # noqa: F401
)
from invincat_cli.local_context import (
    LocalContextMiddleware,
    _AsyncExecutableBackend,
    _ExecutableBackend,
)
from invincat_cli.project_utils import ProjectContext
from invincat_cli.project_utils import (
    get_server_project_context as get_server_project_context,  # noqa: F401
)

logger = logging.getLogger(__name__)

DEFAULT_AGENT_NAME = "agent"
"""The default agent name used when no `-a` flag is provided."""

REQUIRE_COMPACT_TOOL_APPROVAL: bool = True
"""When `True`, `compact_conversation` requires HITL approval like other gated tools."""


from invincat_cli.agent.catalog import (  # noqa: E402, F401
    list_agents,
    load_async_subagents,
)
from invincat_cli.agent.middleware import (  # noqa: E402, F401
    MemoryFileGuardMiddleware,
    ShellAllowListMiddleware,
    _path_targets_memory_file,
)
from invincat_cli.agent.prompt import (  # noqa: E402, F401
    MODEL_IDENTITY_RE,
    build_model_identity_section,
    get_system_prompt,
)
from invincat_cli.agent.subagents import (  # noqa: E402, F401
    DOCUMENT_WORKER_DESCRIPTION,
    DOCUMENT_WORKER_SUBAGENT_NAME,
    DOCUMENT_WORKER_SYSTEM_PROMPT,
    RESEARCHER_DESCRIPTION,
    RESEARCHER_SUBAGENT_NAME,
    RESEARCHER_SYSTEM_PROMPT,
    build_builtin_subagents,
    build_document_worker_subagent,
    build_researcher_subagent,
)
from invincat_cli.agent.tool_descriptions import (  # noqa: E402, F401
    _add_interrupt_on,
    _format_edit_file_description,
    _format_execute_description,
    _format_fetch_url_description,
    _format_task_description,
    _format_web_search_description,
    _format_write_file_description,
)


def create_cli_agent(
    model: str | BaseChatModel,
    assistant_id: str,
    *,
    tools: Sequence[BaseTool | Callable | dict[str, Any]] | None = None,
    sandbox: SandboxBackendProtocol | None = None,
    sandbox_type: str | None = None,
    system_prompt: str | None = None,
    interactive: bool = True,
    auto_approve: bool = False,
    interrupt_shell_only: bool = False,
    shell_allow_list: list[str] | None = None,
    enable_ask_user: bool = True,
    enable_memory: bool = True,
    enable_skills: bool = True,
    enable_shell: bool = True,
    checkpointer: BaseCheckpointSaver | None = None,
    mcp_server_info: list[MCPServerInfo] | None = None,
    cwd: str | Path | None = None,
    project_context: ProjectContext | None = None,
    scheduler_cwd_scope: str | Path | None = None,
    async_subagents: list[AsyncSubAgent] | None = None,
    extra_middleware: Sequence[AgentMiddleware] | None = None,
    approve_plan_system_prompt: str | None = None,
) -> tuple[Pregel, CompositeBackend]:
    """Create a CLI-configured agent with flexible options.

    This is the main entry point for creating a deepagents CLI agent, usable
    both internally and from external code (e.g., benchmarking frameworks).

    Args:
        model: LLM model to use (e.g., `'anthropic:claude-sonnet-4-6'`)
        assistant_id: Agent identifier for memory/state storage
        tools: Additional tools to provide to agent
        sandbox: Optional sandbox backend for remote execution
            (e.g., `ModalSandbox`).

            If `None`, uses local filesystem + shell.
        sandbox_type: Type of sandbox provider
            (`'agentcore'`, `'daytona'`, `'langsmith'`, `'modal'`, `'runloop'`).
            Used for system prompt generation.
        system_prompt: Override the default system prompt.

            If `None`, generates one based on `sandbox_type`, `assistant_id`,
            and `interactive`.
        interactive: When `False`, the auto-generated system prompt is
            tailored for headless non-interactive execution. Ignored when
            `system_prompt` is provided explicitly.
        auto_approve: If `True`, no tools trigger human-in-the-loop
            interrupts — all calls (shell execution, file writes/edits,
            web search, URL fetch) run automatically.

            If `False`, tools pause for user confirmation via the approval menu.
            See `_add_interrupt_on` for the full list of gated tools.
        interrupt_shell_only: If `True`, all HITL interrupts are disabled;
            shell commands are validated inline by `ShellAllowListMiddleware`
            against the configured allow-list instead.

            Used in non-interactive mode with a restrictive shell allow-list
            to avoid splitting traces into multiple LangSmith runs.

            Has no effect when `auto_approve` is `True` (interrupts are already
            disabled) or when `shell_allow_list` is `SHELL_ALLOW_ALL`.
        shell_allow_list: Explicit restrictive shell allow-list forwarded from
            the CLI process. When provided (and `interrupt_shell_only` is
            `True`), used directly instead of reading `settings.shell_allow_list`
            (which may not be set in the server subprocess environment).
        enable_ask_user: Enable `AskUserMiddleware` so the agent can ask
            clarifying questions.

            Disabled in non-interactive mode.
        enable_memory: Enable `MemoryMiddleware` for persistent memory
        enable_skills: Enable `SkillsMiddleware` for custom agent skills
        enable_shell: Enable shell execution via `LocalShellBackend`
            (only in local mode). When enabled, the `execute` tool is available.
        checkpointer: Optional checkpointer for session persistence.
            When `None`, the graph is compiled without a checkpointer.
        approve_plan_system_prompt: Optional override for the `approve_plan`
            middleware system prompt (useful for planner-specific behavior).
        mcp_server_info: MCP server metadata to surface in the system prompt.
        cwd: Override the working directory for the agent's filesystem backend
            and system prompt.
        project_context: Explicit project path context for project-sensitive
            behavior such as skills, subagents, and MCP trust.
        scheduler_cwd_scope: Optional cwd used to scope scheduler management
            tools. When set, schedule tools cannot list or load tasks from other
            working directories.
        async_subagents: Remote LangGraph deployments to expose as async subagent tools.

            Loaded from `[async_subagents]` in `config.toml` or passed directly.
        extra_middleware: Optional middleware appended at the end of the CLI
            middleware stack. Useful for mode-specific guardrails.

    Returns:
        2-tuple of `(agent_graph, backend)`

            - `agent_graph`: Configured LangGraph Pregel instance ready
                for execution
            - `composite_backend`: `CompositeBackend` for file operations
    """
    tools = tools or []
    effective_cwd = (
        Path(cwd)
        if cwd is not None
        else (project_context.user_cwd if project_context is not None else None)
    )

    # Setup agent directory for persistent memory/skills (if enabled).
    if enable_memory or enable_skills:
        settings.ensure_agent_dir(assistant_id)

    # Skills directories (if enabled)
    skills_dir = None
    user_agent_skills_dir = None
    project_skills_dir = None
    project_agent_skills_dir = None
    if enable_skills:
        skills_dir = settings.ensure_user_skills_dir(assistant_id)
        user_agent_skills_dir = settings.ensure_user_agent_skills_dir()
        project_skills_dir = (
            project_context.project_skills_dir()
            if project_context is not None
            else settings.get_project_skills_dir()
        )
        project_agent_skills_dir = (
            project_context.project_agent_skills_dir()
            if project_context is not None
            else settings.get_project_agent_skills_dir()
        )

    restrictive_shell_allow_list: list[str] | None = None
    if interrupt_shell_only and not auto_approve:
        # Prefer the explicitly forwarded allow-list (set by the CLI process
        # and passed through ServerConfig).  Fall back to settings only for
        # direct callers (e.g. benchmarking frameworks) that don't go through
        # the server subprocess path.
        if shell_allow_list:
            restrictive_shell_allow_list = list(shell_allow_list)
        elif settings.shell_allow_list and not isinstance(
            settings.shell_allow_list, _ShellAllowAll
        ):
            restrictive_shell_allow_list = list(settings.shell_allow_list)
        else:
            logger.warning(
                "interrupt_shell_only=True but no restrictive shell allow-list "
                "available; falling back to standard HITL interrupts"
            )

    configured_subagent_names = {
        str(spec.get("name", "")).strip()
        for spec in (async_subagents or [])
        if isinstance(spec, dict)
    }
    runtime_subagents: list[SubAgent] = []
    if restrictive_shell_allow_list is not None:
        from deepagents.middleware.subagents import (
            GENERAL_PURPOSE_SUBAGENT,
        )
        from deepagents.middleware.subagents import (
            SubAgent as RuntimeSubAgent,
        )

        if GENERAL_PURPOSE_SUBAGENT["name"] not in configured_subagent_names:
            general_purpose_subagent: RuntimeSubAgent = {
                "name": GENERAL_PURPOSE_SUBAGENT["name"],
                "description": GENERAL_PURPOSE_SUBAGENT["description"],
                "system_prompt": GENERAL_PURPOSE_SUBAGENT["system_prompt"],
                "middleware": [
                    ShellAllowListMiddleware(
                        restrictive_shell_allow_list,
                        cwd=effective_cwd,
                    )
                ],
            }
            runtime_subagents.append(general_purpose_subagent)

    builtin_subagent_middleware: list[AgentMiddleware] = []
    if restrictive_shell_allow_list is not None:
        builtin_subagent_middleware.append(
            ShellAllowListMiddleware(restrictive_shell_allow_list, cwd=effective_cwd)
        )
    runtime_subagents.extend(
        build_builtin_subagents(
            existing_names={
                *configured_subagent_names,
                *(str(spec.get("name", "")).strip() for spec in runtime_subagents),
            },
            researcher_middleware=builtin_subagent_middleware,
            document_worker_middleware=builtin_subagent_middleware,
        )
    )

    # Build middleware stack based on enabled features
    agent_middleware = []
    agent_middleware.append(ConfigurableModelMiddleware())

    # Token state: adds _context_tokens to graph state (checkpointed, not
    # passed to model).  Must be registered before any middleware that might
    # read the channel.
    from invincat_cli.middleware.token_state import TokenStateMiddleware

    agent_middleware.append(TokenStateMiddleware())

    # Micro-compact: trim old tool outputs before every model call.
    # Pure rule-based, zero LLM cost, runs before memory/context middleware
    # so those layers operate on an already-reduced message list.
    from invincat_cli.middleware.micro_compact import MicroCompactMiddleware

    agent_middleware.append(MicroCompactMiddleware())

    # Add ask_user middleware (must be early so its tool is available)
    if enable_ask_user:
        from invincat_cli.middleware.ask_user import AskUserMiddleware

        agent_middleware.append(AskUserMiddleware())

    # Add approve_plan middleware for plan confirmation
    from invincat_cli.middleware.approve_plan import ApprovePlanMiddleware
    from invincat_cli.wecom.file import WeComFileMiddleware

    if approve_plan_system_prompt is not None:
        agent_middleware.append(
            ApprovePlanMiddleware(system_prompt=approve_plan_system_prompt)
        )
    else:
        agent_middleware.append(ApprovePlanMiddleware())

    agent_middleware.append(
        WeComFileMiddleware(allowed_root=effective_cwd or Path.cwd())
    )

    from invincat_cli.scheduler.store import CwdScopedSchedulerStore, SchedulerStore
    from invincat_cli.scheduler.tool import ScheduleMiddleware

    scheduler_store = (
        CwdScopedSchedulerStore(scheduler_cwd_scope)
        if scheduler_cwd_scope is not None
        else SchedulerStore()
    )
    agent_middleware.append(ScheduleMiddleware(store=scheduler_store))

    # Add memory middleware
    if enable_memory:
        # Guard must be registered before any memory middleware so it runs first.
        agent_middleware.append(MemoryFileGuardMiddleware())

        # Resolve project store directory: prefer detected project root, fall back
        # to the effective cwd so project memory is always available regardless
        # of whether a project-root marker (.git, pyproject.toml, …) exists.
        if project_context is not None and project_context.project_root:
            _project_store_dir = project_context.project_root / ".invincat"
        else:
            _cwd_base = effective_cwd if effective_cwd is not None else Path.cwd()
            _project_store_dir = _cwd_base / ".invincat"

        from invincat_cli.middleware.auto_memory import RefreshableMemoryMiddleware

        user_store_path = str(settings.get_agent_dir(assistant_id) / "memory_user.json")
        project_store_path = str(_project_store_dir / "memory_project.json")
        memory_store_paths = {"user": user_store_path, "project": project_store_path}

        agent_middleware.append(
            RefreshableMemoryMiddleware(
                backend=FilesystemBackend(),
                memory_store_paths=memory_store_paths,
            )
        )

        # Add memory agent middleware (dedicated per-turn memory extraction)
        from invincat_cli.memory.agent import MemoryAgentMiddleware

        agent_middleware.append(
            MemoryAgentMiddleware(
                memory_store_paths=memory_store_paths,
            )
        )

    # Add skills middleware
    if enable_skills:
        # Lowest to highest precedence:
        # built-in -> user .invincat -> user .agents
        # -> project .invincat -> project .agents
        # -> user .claude (experimental) -> project .claude (experimental)
        sources = [str(settings.get_built_in_skills_dir())]
        sources.extend([str(skills_dir), str(user_agent_skills_dir)])
        if project_skills_dir:
            sources.append(str(project_skills_dir))
        if project_agent_skills_dir:
            sources.append(str(project_agent_skills_dir))

        # Experimental: Claude Code skill directories
        user_claude_skills_dir = settings.get_user_claude_skills_dir()
        if user_claude_skills_dir.exists():
            sources.append(str(user_claude_skills_dir))
        project_claude_skills_dir = settings.get_project_claude_skills_dir()
        if project_claude_skills_dir:
            sources.append(str(project_claude_skills_dir))

        agent_middleware.append(
            SkillsMiddleware(
                backend=FilesystemBackend(),
                sources=sources,
            )
        )

    # CONDITIONAL SETUP: Local vs Remote Sandbox
    if sandbox is None:
        # ========== LOCAL MODE ==========
        root_dir = effective_cwd if effective_cwd is not None else Path.cwd()
        if enable_shell:
            # Create environment for shell commands
            # Restore user's original LANGSMITH_PROJECT so their code traces separately
            shell_env = os.environ.copy()
            if settings.user_langchain_project:
                shell_env["LANGSMITH_PROJECT"] = settings.user_langchain_project

            # Use LocalShellBackend for filesystem + shell execution.
            # The SDK's FilesystemMiddleware exposes per-command timeout
            # on the execute tool natively.
            backend = LocalShellBackend(
                root_dir=root_dir,
                inherit_env=True,
                env=shell_env,
            )
        else:
            # No shell access - use plain FilesystemBackend
            backend = FilesystemBackend(root_dir=root_dir)
    else:
        # ========== REMOTE SANDBOX MODE ==========
        backend = sandbox  # Remote sandbox (ModalSandbox, etc.)
        # Note: Shell middleware not used in sandbox mode
        # File operations and execute tool are provided by the sandbox backend

    # Local context middleware (git info, directory tree, etc.).
    if isinstance(backend, (_ExecutableBackend, _AsyncExecutableBackend)):
        agent_middleware.append(
            LocalContextMiddleware(backend=backend, mcp_server_info=mcp_server_info)
        )

    # Add shell allow-list middleware when interrupt_shell_only is active.
    shell_middleware_added = False
    if restrictive_shell_allow_list is not None:
        agent_middleware.append(
            ShellAllowListMiddleware(restrictive_shell_allow_list, cwd=effective_cwd)
        )
        shell_middleware_added = True

    # Get or use custom system prompt
    if system_prompt is None:
        system_prompt = get_system_prompt(
            assistant_id=assistant_id,
            sandbox_type=sandbox_type,
            interactive=interactive,
            cwd=effective_cwd,
        )

    # Configure interrupt_on based on auto_approve / shell_middleware_added
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None
    if auto_approve or shell_middleware_added:  # noqa: SIM108  # if-else clearer than ternary for dual-path config
        # No HITL interrupts — tools run automatically.
        # When shell_middleware_added is True, shell validation is handled by
        # ShellAllowListMiddleware (added above) which rejects disallowed
        # commands inline as error ToolMessages, keeping the entire run in
        # a single LangSmith trace.
        interrupt_on = {}
    else:
        # Full HITL for destructive operations
        interrupt_on = _add_interrupt_on(mcp_server_info)  # type: ignore[assignment]  # InterruptOnConfig is compatible at runtime

    # Set up composite backend with routing
    # For local FilesystemBackend, route large tool results to /tmp to avoid polluting
    # the working directory. For sandbox backends, no special routing is needed.
    if sandbox is None:
        # Local mode: Route large results to a unique temp directory
        large_results_backend = FilesystemBackend(
            root_dir=tempfile.mkdtemp(prefix="deepagents_large_results_"),
            virtual_mode=True,
        )
        conversation_history_backend = FilesystemBackend(
            root_dir=tempfile.mkdtemp(prefix="deepagents_conversation_history_"),
            virtual_mode=True,
        )
        composite_backend = CompositeBackend(
            default=backend,
            routes={
                "/large_tool_results/": large_results_backend,
                "/conversation_history/": conversation_history_backend,
            },
        )
    else:
        # Sandbox mode: No special routing needed
        composite_backend = CompositeBackend(
            default=backend,
            routes={},
        )

    from deepagents.middleware.summarization import create_summarization_tool_middleware

    agent_middleware.append(
        create_summarization_tool_middleware(model, composite_backend)
    )
    if extra_middleware:
        agent_middleware.extend(extra_middleware)

    # Create the agent
    all_subagents: list[SubAgent | AsyncSubAgent] = [
        *runtime_subagents,
        *(async_subagents or []),
    ]
    agent = create_deep_agent(
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        backend=composite_backend,
        middleware=agent_middleware,
        interrupt_on=interrupt_on,
        checkpointer=checkpointer,
        subagents=all_subagents or None,
    ).with_config(config)
    return agent, composite_backend
