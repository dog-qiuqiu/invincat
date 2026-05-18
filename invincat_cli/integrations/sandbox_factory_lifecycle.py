"""Sandbox lifecycle helpers for the sandbox factory facade."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import shlex
import string
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markup import escape as escape_markup

from invincat_cli.config import console, get_glyphs

if TYPE_CHECKING:
    from collections.abc import Generator

    from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)

_PROVIDER_TO_WORKING_DIR = {
    "agentcore": "/tmp",  # noqa: S108 # AgentCore Code Interpreter working directory
    "daytona": "/home/daytona",
    "langsmith": "/tmp",  # noqa: S108  # LangSmith sandbox working directory
    "modal": "/workspace",
    "runloop": "/home/user",
}
"""Map of sandbox provider names to their default working directories."""


def _run_sandbox_setup(backend: SandboxBackendProtocol, setup_script_path: str) -> None:
    """Run users setup script in sandbox with env var expansion."""
    script_path = Path(setup_script_path)
    if not script_path.exists():
        msg = f"Setup script not found: {setup_script_path}"
        raise FileNotFoundError(msg)

    console.print(
        f"[dim]Running setup script: {escape_markup(setup_script_path)}...[/dim]"
    )

    script_content = script_path.read_text(encoding="utf-8")
    template = string.Template(script_content)
    expanded_script = template.safe_substitute(os.environ)
    result = backend.execute(f"bash -c {shlex.quote(expanded_script)}")

    if result.exit_code != 0:
        console.print(f"[red]Setup script failed (exit {result.exit_code}):[/red]")
        console.print(f"[dim]{escape_markup(result.output)}[/dim]")
        msg = "Setup failed - aborting"
        raise RuntimeError(msg)

    console.print(f"[green]{get_glyphs().checkmark} Setup complete[/green]")


@contextmanager
def create_sandbox(
    provider: str,
    *,
    sandbox_id: str | None = None,
    setup_script_path: str | None = None,
) -> Generator[SandboxBackendProtocol, None, None]:
    """Create or connect to a sandbox of the specified provider."""
    from invincat_cli.integrations import sandbox_factory as _factory

    provider_obj = _factory._get_provider(provider)
    should_cleanup = sandbox_id is None

    console.print(f"[yellow]Starting {provider} sandbox...[/yellow]")
    backend = provider_obj.get_or_create(sandbox_id=sandbox_id)
    glyphs = get_glyphs()
    console.print(
        f"[green]{glyphs.checkmark} {provider.capitalize()} sandbox ready: "
        f"{backend.id}[/green]"
    )

    if setup_script_path:
        _factory._run_sandbox_setup(backend, setup_script_path)

    try:
        yield backend
    finally:
        if should_cleanup:
            try:
                console.print(
                    f"[dim]Terminating {provider} sandbox {backend.id}...[/dim]"
                )
                provider_obj.delete(sandbox_id=backend.id)
                glyphs = get_glyphs()
                console.print(
                    f"[dim]{glyphs.checkmark} {provider.capitalize()} sandbox "
                    f"{backend.id} terminated[/dim]"
                )
            except Exception as e:  # noqa: BLE001  # Cleanup errors should not mask the original sandbox failure
                warning = get_glyphs().warning
                console.print(
                    f"[yellow]{warning} Cleanup failed for {provider} sandbox "
                    f"{backend.id}: {e}[/yellow]"
                )


def _get_available_sandbox_types() -> list[str]:
    """Get list of available sandbox provider types."""
    return sorted(_PROVIDER_TO_WORKING_DIR.keys())


def get_default_working_dir(provider: str) -> str:
    """Get the default working directory for a given sandbox provider."""
    if provider in _PROVIDER_TO_WORKING_DIR:
        return _PROVIDER_TO_WORKING_DIR[provider]
    msg = f"Unknown sandbox provider: {provider}"
    raise ValueError(msg)


def verify_sandbox_deps(provider: str) -> None:
    """Check that the required packages for a sandbox provider are installed."""
    if not provider or provider in {"none", "langsmith"}:
        return

    backend_modules: dict[str, tuple[str, str]] = {
        "agentcore": ("langchain_agentcore_codeinterpreter", "agentcore"),
        "daytona": ("langchain_daytona", "daytona"),
        "modal": ("langchain_modal", "modal"),
        "runloop": ("langchain_runloop", "runloop"),
    }

    entry = backend_modules.get(provider)
    if entry is None:
        logger.debug(
            "No backend_modules entry for provider %r; skipping pre-flight check",
            provider,
        )
        return

    module_name, extra = entry
    try:
        found = importlib.util.find_spec(module_name) is not None
    except (ImportError, ValueError):
        found = False

    if not found:
        msg = (
            f"Missing dependencies for '{provider}' sandbox. "
            f"Install with: pip install 'invincat-cli[{extra}]'"
        )
        raise ImportError(msg)
