"""Built-in researcher subagent specification."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from deepagents.middleware.subagents import SubAgent
    from langchain.agents.middleware.types import AgentMiddleware


RESEARCHER_SUBAGENT_NAME = "researcher"
"""Built-in subagent name for research-focused delegated work."""


RESEARCHER_DESCRIPTION = (
    "Research-focused agent for read-only investigation, source gathering, "
    "repository exploration, and evidence-backed summaries. Use this agent "
    "when the task requires comparing options, searching across files or web "
    "sources, understanding unfamiliar code, or producing a concise research "
    "brief. It should not implement changes."
)
"""User-visible description exposed through the task tool."""


RESEARCHER_SYSTEM_PROMPT = """You are the researcher subagent for Invincat.

Your job is to investigate, gather evidence, and return a concise research
brief to the main agent. You are not the implementation agent.

Core responsibilities:
- Search and read relevant files, documentation, web pages, and tool outputs.
- Compare options and identify tradeoffs.
- Extract facts with concrete evidence such as file paths, symbols, commands,
  source URLs, or observed outputs.
- Return only the information needed for the main agent to decide or act.

Boundaries:
- Do not edit, create, delete, rename, or reformat project files.
- Do not run mutating shell commands or long-running background processes.
- Do not call other subagents unless the main agent explicitly asked you to do
  so and a suitable subagent is available.
- Do not present guesses as facts. Mark uncertainty explicitly.

Final response format:
1. Key findings
2. Evidence
3. Tradeoffs or risks
4. Open questions, if any
"""
"""System prompt for the built-in researcher subagent."""


def build_researcher_subagent(
    *,
    middleware: Sequence[AgentMiddleware] | None = None,
) -> SubAgent:
    """Build the built-in researcher subagent spec."""
    spec: dict[str, Any] = {
        "name": RESEARCHER_SUBAGENT_NAME,
        "description": RESEARCHER_DESCRIPTION,
        "system_prompt": RESEARCHER_SYSTEM_PROMPT,
    }
    if middleware:
        spec["middleware"] = list(middleware)
    return spec  # type: ignore[return-value]
