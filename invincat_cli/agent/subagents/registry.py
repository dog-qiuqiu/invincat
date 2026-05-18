"""Registry helpers for built-in subagents."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

from invincat_cli.agent.subagents.document_worker import (
    DOCUMENT_WORKER_SUBAGENT_NAME,
    build_document_worker_subagent,
)
from invincat_cli.agent.subagents.explorer import (
    EXPLORER_SUBAGENT_NAME,
    build_explorer_subagent,
)
from invincat_cli.agent.subagents.researcher import (
    RESEARCHER_SUBAGENT_NAME,
    build_researcher_subagent,
)

if TYPE_CHECKING:
    from deepagents.middleware.subagents import SubAgent
    from langchain.agents.middleware.types import AgentMiddleware


def _subagent_name(spec: object) -> str:
    if isinstance(spec, dict):
        return str(spec.get("name", "")).strip()
    return ""


def subagent_names(specs: Iterable[object]) -> set[str]:
    """Return normalized names from subagent-like specs."""
    return {name for spec in specs if (name := _subagent_name(spec))}


def build_builtin_subagents(
    *,
    existing_names: Iterable[str] = (),
    explorer_middleware: Sequence[AgentMiddleware] | None = None,
    researcher_middleware: Sequence[AgentMiddleware] | None = None,
    document_worker_middleware: Sequence[AgentMiddleware] | None = None,
) -> list[SubAgent]:
    """Build built-in subagents that are not already provided by the user."""
    names = {name for name in existing_names if name}
    subagents: list[SubAgent] = []
    if EXPLORER_SUBAGENT_NAME not in names:
        subagents.append(
            build_explorer_subagent(middleware=explorer_middleware)
        )
    if RESEARCHER_SUBAGENT_NAME not in names:
        subagents.append(
            build_researcher_subagent(middleware=researcher_middleware)
        )
    if DOCUMENT_WORKER_SUBAGENT_NAME not in names:
        subagents.append(
            build_document_worker_subagent(middleware=document_worker_middleware)
        )
    return subagents
