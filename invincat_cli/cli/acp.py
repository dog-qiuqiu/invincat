"""ACP mode runner for the CLI."""

from __future__ import annotations

from typing import Any


async def _run_acp_cli_async(
    assistant_id: str,
    *,
    run_acp_agent: Any,
    agent_server_cls: type[Any],
    model_name: str | None = None,
    model_params: dict[str, Any] | None = None,
    profile_override: dict[str, Any] | None = None,
    mcp_config_path: str | None = None,
    no_mcp: bool = False,
    trust_project_mcp: bool | None = None,
) -> int:
    """Run ACP server mode and return a process exit code."""
    from invincat_cli import main as _main
    from invincat_cli.agent import create_cli_agent, load_async_subagents
    from invincat_cli.config import create_model, settings
    from invincat_cli.model_config import ModelConfigError, save_recent_model
    from invincat_cli.tools import fetch_url, web_search

    try:
        model_result = create_model(
            model_name,
            extra_kwargs=model_params,
            profile_overrides=profile_override,
        )
    except ModelConfigError as exc:
        _main.sys.stderr.write(f"Error: {exc}\n")
        _main.sys.stderr.flush()
        return 1
    model_result.apply_to_settings()

    save_recent_model(f"{model_result.provider}:{model_result.model_name}")

    tools: list[Any] = [fetch_url]
    if settings.has_tavily:
        tools.append(web_search)

    mcp_session_manager = None
    mcp_server_info = None
    try:
        from invincat_cli.mcp.tools import resolve_and_load_mcp_tools

        (
            mcp_tools,
            mcp_session_manager,
            mcp_server_info,
        ) = await resolve_and_load_mcp_tools(
            explicit_config_path=mcp_config_path,
            no_mcp=no_mcp,
            trust_project_mcp=trust_project_mcp,
        )
        tools.extend(mcp_tools)
    except FileNotFoundError as exc:
        msg = f"Error: MCP config file not found: {exc}\n"
        _main.sys.stderr.write(msg)
        _main.sys.stderr.flush()
        return 1
    except RuntimeError as exc:
        msg = f"Error: Failed to load MCP tools: {exc}\n"
        _main.sys.stderr.write(msg)
        _main.sys.stderr.flush()
        return 1

    async_subagents = load_async_subagents() or None

    try:
        from langgraph.checkpoint.memory import InMemorySaver

        agent_graph, _backend = create_cli_agent(
            model=model_result.model,
            assistant_id=assistant_id,
            tools=tools,
            mcp_server_info=mcp_server_info,
            checkpointer=InMemorySaver(),
            async_subagents=async_subagents,
        )
    except Exception as exc:
        _main.sys.stderr.write(f"Error: failed to create agent: {exc}\n")
        _main.sys.stderr.flush()
        _main.logger.debug("ACP agent creation failed", exc_info=True)
        return 1

    server = agent_server_cls(agent_graph)
    exit_code = 0
    try:
        await run_acp_agent(server)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        _main.sys.stderr.write(f"Error: ACP server failed: {exc}\n")
        _main.sys.stderr.flush()
        _main.logger.exception("ACP server crashed")
        exit_code = 1
    finally:
        if mcp_session_manager is not None:
            try:
                await mcp_session_manager.cleanup()
            except Exception:
                _main.logger.warning("MCP session cleanup failed", exc_info=True)
    return exit_code
