"""Built-in subagent specifications for the CLI agent."""

from __future__ import annotations

from invincat_cli.agent.subagents.registry import (
    build_builtin_subagents,
    subagent_names,
)
from invincat_cli.agent.subagents.researcher import (
    RESEARCHER_DESCRIPTION,
    RESEARCHER_SUBAGENT_NAME,
    RESEARCHER_SYSTEM_PROMPT,
    build_researcher_subagent,
)

__all__ = [
    "RESEARCHER_DESCRIPTION",
    "RESEARCHER_SUBAGENT_NAME",
    "RESEARCHER_SYSTEM_PROMPT",
    "build_builtin_subagents",
    "build_researcher_subagent",
    "subagent_names",
]
