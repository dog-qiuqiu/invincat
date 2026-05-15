"""Textual TUI launcher for the CLI."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from invincat_cli.app import AppResult


async def run_textual_cli_async(
    assistant_id: str,
    *,
    auto_approve: bool = False,
    sandbox_type: str = "none",
    sandbox_id: str | None = None,
    sandbox_setup: str | None = None,
    model_name: str | None = None,
    model_params: dict[str, Any] | None = None,
    profile_override: dict[str, Any] | None = None,
    thread_id: str | None = None,
    resume_thread: str | None = None,
    initial_prompt: str | None = None,
    mcp_config_path: str | None = None,
    no_mcp: bool = False,
    trust_project_mcp: bool | None = None,
) -> AppResult:
    """Run the Textual CLI interface."""
    from rich.text import Text

    from invincat_cli import main as _main
    from invincat_cli.app import AppResult, run_textual_app
    from invincat_cli.config import (
        _get_default_model_spec,
        detect_provider,
        settings,
    )
    from invincat_cli.model_config import ModelConfigError, ModelSpec

    resolved_spec: str | None
    defer_server_start = False
    try:
        resolved_spec = model_name or _get_default_model_spec()
    except ModelConfigError as e:
        if model_name:
            from rich.markup import escape

            from invincat_cli.config import console

            console.print(
                f"[bold red]Error:[/bold red] {escape(str(e))}", highlight=False
            )
            return AppResult(return_code=1, thread_id=None)
        resolved_spec = None
        defer_server_start = True

    if resolved_spec:
        parsed = ModelSpec.try_parse(resolved_spec)
        if parsed:
            settings.model_provider = parsed.provider
            settings.model_name = parsed.model
        else:
            settings.model_name = resolved_spec
            settings.model_provider = detect_provider(resolved_spec) or ""
    else:
        settings.model_name = ""
        settings.model_provider = ""

    model_kwargs: dict[str, Any] | None = None
    if resolved_spec:
        model_kwargs = {
            "model_spec": resolved_spec,
            "extra_kwargs": model_params,
            "profile_overrides": profile_override,
        }

    server_kwargs: dict[str, Any] = {
        "assistant_id": assistant_id,
        "model_name": resolved_spec,
        "model_params": model_params,
        "sandbox_type": sandbox_type,
        "sandbox_id": sandbox_id,
        "sandbox_setup": sandbox_setup,
        "enable_ask_user": True,
        "mcp_config_path": mcp_config_path,
        "no_mcp": no_mcp,
        "trust_project_mcp": trust_project_mcp,
        "interactive": True,
    }

    mcp_preload_kwargs: dict[str, Any] | None = None
    if not no_mcp:
        mcp_preload_kwargs = {
            "mcp_config_path": mcp_config_path,
            "no_mcp": no_mcp,
            "trust_project_mcp": trust_project_mcp,
        }

    try:
        result = await run_textual_app(
            assistant_id=assistant_id,
            backend=None,
            auto_approve=auto_approve,
            cwd=Path.cwd(),
            thread_id=thread_id,
            resume_thread=resume_thread,
            initial_prompt=initial_prompt,
            profile_override=profile_override,
            server_kwargs=server_kwargs,
            mcp_preload_kwargs=mcp_preload_kwargs,
            model_kwargs=model_kwargs,
            defer_server_start=defer_server_start,
        )
    except Exception as e:
        _main.logger.debug("App error", exc_info=True)
        from invincat_cli.config import console

        error_text = Text("Application error: ", style="red")
        error_text.append(str(e))
        console.print(error_text)
        if _main.logger.isEnabledFor(_main.logging.DEBUG):
            console.print(Text(_main.traceback.format_exc(), style="dim"))
        return AppResult(return_code=1, thread_id=None)

    return result
