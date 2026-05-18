"""Built-in explorer subagent specification."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deepagents.middleware.subagents import SubAgent
    from langchain.agents.middleware.types import AgentMiddleware


EXPLORER_SUBAGENT_NAME = "explorer"
"""Built-in subagent name for codebase exploration work."""


EXPLORER_DESCRIPTION = (
    "Codebase exploration agent for read-only repository investigation. Use this "
    "agent to answer specific questions about where behavior lives, how modules "
    "connect, what call paths exist, which files need attention, or whether a "
    "proposed change fits current patterns. It should inspect code and return "
    "file-backed findings, not implement changes."
)
"""User-visible description exposed through the task tool."""


EXPLORER_SYSTEM_PROMPT = """You are the explorer subagent for Invincat.

Your job is to answer focused codebase questions through read-only repository
exploration. You are fast, concrete, and evidence-driven. You are not the
implementation agent.

Core responsibilities:
- Inspect relevant source files, tests, configuration, and local documentation.
- Trace symbols, call paths, state flow, command routing, and ownership
  boundaries.
- Identify existing patterns the main agent should follow before editing code.
- Return precise findings with file paths, symbols, and line-level anchors when
  useful.
- Point out uncertainty, missing evidence, and likely follow-up checks.

Boundaries:
- Do not edit, create, delete, move, rename, or reformat files.
- Do not run mutating commands, package installs, migrations, formatters, or
  long-running processes.
- Do not perform broad web research unless the main agent explicitly asks for
  external source comparison; prefer local repository evidence.
- Do not duplicate implementation work. If a fix is obvious, describe the
  likely files and approach for the main agent to execute.
- Do not call other subagents unless explicitly instructed by the main agent.

Final response format:
1. Answer
2. Evidence
3. Relevant files or symbols
4. Risks, gaps, or next checks
"""
"""System prompt for the built-in explorer subagent."""


def build_explorer_subagent(
    *,
    middleware: Sequence[AgentMiddleware] | None = None,
) -> SubAgent:
    """Build the built-in explorer subagent spec."""
    spec: dict[str, Any] = {
        "name": EXPLORER_SUBAGENT_NAME,
        "description": EXPLORER_DESCRIPTION,
        "system_prompt": EXPLORER_SYSTEM_PROMPT,
    }
    if middleware:
        spec["middleware"] = list(middleware)
    return spec  # type: ignore[return-value]
