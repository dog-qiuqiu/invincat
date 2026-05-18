"""Built-in subagent specifications for the CLI agent."""

from __future__ import annotations

from invincat_cli.agent.subagents.document_worker import (
    DOCUMENT_WORKER_DESCRIPTION,
    DOCUMENT_WORKER_SUBAGENT_NAME,
    DOCUMENT_WORKER_SYSTEM_PROMPT,
    build_document_worker_subagent,
)
from invincat_cli.agent.subagents.explorer import (
    EXPLORER_DESCRIPTION,
    EXPLORER_SUBAGENT_NAME,
    EXPLORER_SYSTEM_PROMPT,
    build_explorer_subagent,
)
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
from invincat_cli.agent.subagents.worker import (
    WORKER_DESCRIPTION,
    WORKER_SUBAGENT_NAME,
    WORKER_SYSTEM_PROMPT,
    build_worker_subagent,
)

__all__ = [
    "DOCUMENT_WORKER_DESCRIPTION",
    "DOCUMENT_WORKER_SUBAGENT_NAME",
    "DOCUMENT_WORKER_SYSTEM_PROMPT",
    "EXPLORER_DESCRIPTION",
    "EXPLORER_SUBAGENT_NAME",
    "EXPLORER_SYSTEM_PROMPT",
    "RESEARCHER_DESCRIPTION",
    "RESEARCHER_SUBAGENT_NAME",
    "RESEARCHER_SYSTEM_PROMPT",
    "WORKER_DESCRIPTION",
    "WORKER_SUBAGENT_NAME",
    "WORKER_SYSTEM_PROMPT",
    "build_builtin_subagents",
    "build_document_worker_subagent",
    "build_explorer_subagent",
    "build_researcher_subagent",
    "build_worker_subagent",
    "subagent_names",
]
