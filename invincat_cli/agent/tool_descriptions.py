"""Human approval descriptions for agent tool calls."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from invincat_cli.unicode_security import (
    check_url_safety,
    detect_dangerous_unicode,
    format_warning_detail,
    render_with_unicode_markers,
    strip_dangerous_unicode,
    summarize_issues,
)

if TYPE_CHECKING:
    from langchain.agents.middleware import InterruptOnConfig
    from langchain.agents.middleware.types import AgentState
    from langchain.messages import ToolCall
    from langgraph.runtime import Runtime

    from invincat_cli.mcp.tools import MCPServerInfo


def _format_write_file_description(
    tool_call: ToolCall, _state: AgentState[Any], _runtime: Runtime[Any]
) -> str:
    """Format write_file tool call for approval prompt."""
    from invincat_cli import agent as _agent

    args = tool_call["args"]
    file_path = args.get("file_path", "unknown")
    action = "Overwrite" if _agent.Path(file_path).exists() else "Create"
    return f"Action: {action} file"


def _format_edit_file_description(
    tool_call: ToolCall, _state: AgentState[Any], _runtime: Runtime[Any]
) -> str:
    """Format edit_file tool call for approval prompt."""
    args = tool_call["args"]
    replace_all = bool(args.get("replace_all", False))
    scope = "all occurrences" if replace_all else "single occurrence"
    return f"Action: Replace text ({scope})"


def _format_web_search_description(
    tool_call: ToolCall, _state: AgentState[Any], _runtime: Runtime[Any]
) -> str:
    """Format web_search tool call for approval prompt."""
    from invincat_cli import agent as _agent

    args = tool_call["args"]
    query = args.get("query", "unknown")
    max_results = args.get("max_results", 5)
    return (
        f"Query: {query}\nMax results: {max_results}\n\n"
        f"{_agent.get_glyphs().warning}  This will use Tavily API credits"
    )


def _format_fetch_url_description(
    tool_call: ToolCall, _state: AgentState[Any], _runtime: Runtime[Any]
) -> str:
    """Format fetch_url tool call for approval prompt."""
    from invincat_cli import agent as _agent

    args = tool_call["args"]
    url = str(args.get("url", "unknown"))
    display_url = strip_dangerous_unicode(url)
    timeout = args.get("timeout", 30)
    safety = check_url_safety(url)

    warning_lines: list[str] = []
    if not safety.safe:
        detail = format_warning_detail(safety.warnings)
        warning_lines.append(f"{_agent.get_glyphs().warning}  URL warning: {detail}")
    if safety.decoded_domain:
        warning_lines.append(
            f"{_agent.get_glyphs().warning}  Decoded domain: {safety.decoded_domain}"
        )

    warning_block = "\n".join(warning_lines)
    if warning_block:
        warning_block = f"\n{warning_block}"

    return (
        f"URL: {display_url}\nTimeout: {timeout}s\n\n"
        f"{_agent.get_glyphs().warning}  Will fetch and convert web content to markdown"
        f"{warning_block}"
    )


def _format_task_description(
    tool_call: ToolCall, _state: AgentState[Any], _runtime: Runtime[Any]
) -> str:
    """Format task (subagent) tool call for approval prompt."""
    from invincat_cli import agent as _agent

    args = tool_call["args"]
    description = args.get("description", "unknown")
    subagent_type = args.get("subagent_type", "unknown")

    description_preview = description
    if len(description) > 500:  # noqa: PLR2004  # Subagent description threshold
        description_preview = description[:500] + "..."

    glyphs = _agent.get_glyphs()
    separator = glyphs.box_horizontal * 40
    warning_msg = "Subagent will have access to file operations and shell commands"
    return (
        f"Subagent Type: {subagent_type}\n\n"
        f"{glyphs.warning} {warning_msg} {glyphs.warning}\n\n"
        f"Task Instructions:\n"
        f"{separator}\n"
        f"{description_preview}"
    )


def _format_execute_description(
    tool_call: ToolCall, _state: AgentState[Any], _runtime: Runtime[Any]
) -> str:
    """Format execute tool call for approval prompt."""
    from invincat_cli import agent as _agent

    args = tool_call["args"]
    command_raw = str(args.get("command", "N/A"))
    command = strip_dangerous_unicode(command_raw)
    project_context = _agent.get_server_project_context()
    effective_cwd = (
        str(project_context.user_cwd)
        if project_context is not None
        else str(_agent.Path.cwd())
    )
    lines = [f"Execute Command: {command}", f"Working Directory: {effective_cwd}"]

    issues = detect_dangerous_unicode(command_raw)
    if issues:
        summary = summarize_issues(issues)
        lines.append(f"{_agent.get_glyphs().warning}  Hidden Unicode detected: {summary}")
        raw_marked = render_with_unicode_markers(command_raw)
        if len(raw_marked) > 220:  # noqa: PLR2004  # UI display truncation threshold
            raw_marked = raw_marked[:220] + "..."
        lines.append(f"Raw: {raw_marked}")

    return "\n".join(lines)


def _add_interrupt_on(
    mcp_server_info: list[MCPServerInfo] | None = None,
) -> dict[str, InterruptOnConfig]:
    """Configure human-in-the-loop interrupt settings for all gated tools."""
    from invincat_cli import agent as _agent

    execute_interrupt_config: InterruptOnConfig = {
        "allowed_decisions": ["approve", "reject"],
        "description": _format_execute_description,  # type: ignore[typeddict-item]
    }
    write_file_interrupt_config: InterruptOnConfig = {
        "allowed_decisions": ["approve", "reject"],
        "description": _format_write_file_description,  # type: ignore[typeddict-item]
    }
    edit_file_interrupt_config: InterruptOnConfig = {
        "allowed_decisions": ["approve", "reject"],
        "description": _format_edit_file_description,  # type: ignore[typeddict-item]
    }
    web_search_interrupt_config: InterruptOnConfig = {
        "allowed_decisions": ["approve", "reject"],
        "description": _format_web_search_description,  # type: ignore[typeddict-item]
    }
    fetch_url_interrupt_config: InterruptOnConfig = {
        "allowed_decisions": ["approve", "reject"],
        "description": _format_fetch_url_description,  # type: ignore[typeddict-item]
    }
    task_interrupt_config: InterruptOnConfig = {
        "allowed_decisions": ["approve", "reject"],
        "description": _format_task_description,  # type: ignore[typeddict-item]
    }
    async_subagent_interrupt_config: InterruptOnConfig = {
        "allowed_decisions": ["approve", "reject"],
        "description": "Launch, update, or cancel a remote async subagent.",
    }

    interrupt_map: dict[str, InterruptOnConfig] = {
        "execute": execute_interrupt_config,
        "write_file": write_file_interrupt_config,
        "edit_file": edit_file_interrupt_config,
        "web_search": web_search_interrupt_config,
        "fetch_url": fetch_url_interrupt_config,
        "task": task_interrupt_config,
        "launch_async_subagent": async_subagent_interrupt_config,
        "update_async_subagent": async_subagent_interrupt_config,
        "cancel_async_subagent": async_subagent_interrupt_config,
    }

    if mcp_server_info:
        mcp_interrupt_config: InterruptOnConfig = {
            "allowed_decisions": ["approve", "reject"],
            "description": "Call an MCP tool provided by a configured MCP server.",
        }
        for server in mcp_server_info:
            for tool in server.tools:
                interrupt_map[tool.name] = mcp_interrupt_config

    if _agent.REQUIRE_COMPACT_TOOL_APPROVAL:
        interrupt_map["compact_conversation"] = {
            "allowed_decisions": ["approve", "reject"],
            "description": (
                "Offloads older messages to backend storage and "
                "replaces them with a summary, freeing context "
                "window space. Recent messages are kept as-is. "
                "Full history remains available for retrieval."
            ),
        }

    return interrupt_map
