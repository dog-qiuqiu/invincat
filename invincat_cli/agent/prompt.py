"""System prompt construction for CLI agents."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

MODEL_IDENTITY_RE = re.compile(r"### Model Identity\n\n.*?(?=###|\Z)", re.DOTALL)
"""Matches the `### Model Identity` section in the system prompt, up to the
next heading or end of string."""


def build_model_identity_section(
    name: str | None,
    provider: str | None = None,
    context_limit: int | None = None,
    unsupported_modalities: frozenset[str] = frozenset(),
) -> str:
    """Build the `### Model Identity` section for the system prompt."""
    if not name:
        return ""
    section = f"### Model Identity\n\nYou are running as model `{name}`"
    if provider:
        section += f" (provider: {provider})"
    section += ".\n"
    if context_limit:
        section += f"Your context window is {context_limit:,} tokens.\n"
    if unsupported_modalities:
        items = sorted(unsupported_modalities)
        if len(items) == 1:
            joined = items[0]
        elif len(items) == 2:  # noqa: PLR2004
            joined = f"{items[0]} and {items[1]}"
        else:
            joined = ", ".join(items[:-1]) + f", and {items[-1]}"
        section += (
            f"{joined.capitalize()} input may not be available for this model. "
            "Do not attempt to read or process these content types.\n"
        )
    section += "\n"
    return section


def _mode_sections(interactive: bool) -> tuple[str, str, str]:
    if interactive:
        return (
            "an interactive CLI on the user's computer",
            (
                "The user sends you messages and you respond with text and tool "
                "calls. Your tools run on the user's machine. The user can see "
                "your responses and tool outputs in real time, so keep them "
                "informed — but don't over-explain."
            ),
            (
                "- If the request is ambiguous, ask questions before acting.\n"
                "- If asked how to approach something, explain first, then act."
            ),
        )

    return (
        (
            "non-interactive (headless) mode — there is no human operator "
            "monitoring your output in real time"
        ),
        (
            "You received a single task and must complete it fully and "
            "autonomously. There is no human available to answer follow-up "
            "questions, so do NOT ask for clarification — make reasonable "
            "assumptions and proceed."
        ),
        (
            "- Do NOT ask clarifying questions — there is no human to answer "
            "them. Make reasonable assumptions and proceed.\n"
            "- If you encounter ambiguity, choose the most reasonable "
            "interpretation and note your assumption briefly.\n"
            "- Always use non-interactive command variants — no human is "
            "available to respond to prompts. Examples: `npm init -y` not "
            "`npm init`, `apt-get install -y` not `apt-get install`, "
            "`yes |` or `--no-input`/`--non-interactive` flags where "
            "available. Never run commands that block waiting for stdin."
        ),
    )


def _current_time_section() -> str:
    now_local = datetime.now().astimezone()
    return (
        "### Current Date and Time\n\n"
        f"Local time is `{now_local.isoformat(timespec='seconds')}`.\n\n"
        "Use this timestamp as the reference for relative scheduling phrases "
        "such as today, tomorrow, tonight, later, in N minutes/hours/days, "
        "今天, 明天, 今晚, 稍后, and N 分钟/小时/天后.\n\n"
    )


def _working_dir_section(sandbox_type: str | None, cwd: str | Path | None) -> str:
    from invincat_cli import agent as _agent

    if sandbox_type:
        working_dir = _agent.get_default_working_dir(sandbox_type)
        return (
            f"### Current Working Directory\n\n"
            f"You are operating in a **remote Linux sandbox** at `{working_dir}`.\n\n"
            f"All code execution and file operations happen in this sandbox "
            f"environment.\n\n"
            f"**Important:**\n"
            f"- The CLI is running locally on the user's machine, but you execute "
            f"code remotely\n"
            f"- Use `{working_dir}` as your working directory for all operations\n"
            f"- **You do NOT have access to the user's local filesystem.** Paths "
            f"like `/Users/...`, `/home/<local-user>/...`, `C:\\...`, etc. do not "
            f"exist in this sandbox. Never reference or attempt to read/write local "
            f"paths — all files must be within the sandbox at `{working_dir}`\n"
            f"- When delegating to subagents, ensure they also use sandbox paths "
            f"(`{working_dir}/...`), not local paths\n\n"
        )

    if cwd is not None:
        resolved_cwd = _agent.Path(cwd)
    else:
        try:
            resolved_cwd = _agent.Path.cwd()
        except OSError:
            _agent.logger.warning(
                "Could not determine working directory for system prompt",
                exc_info=True,
            )
            resolved_cwd = _agent.Path()

    return (
        f"### Current Working Directory\n\n"
        f"The filesystem backend is currently operating in: `{resolved_cwd}`\n\n"
        f"### File System and Paths\n\n"
        f"**IMPORTANT - Path Handling:**\n"
        f"- All file paths must be absolute paths (e.g., `{resolved_cwd}/file.txt`)\n"
        f"- Use the working directory to construct absolute paths\n"
        f"- Example: To create a file in your working directory, "
        f"use `{resolved_cwd}/research_project/file.md`\n"
        f"- Never use relative paths - always construct full absolute paths\n\n"
    )


def get_system_prompt(
    assistant_id: str,
    sandbox_type: str | None = None,
    *,
    interactive: bool = True,
    cwd: str | Path | None = None,
) -> str:
    """Get the base system prompt for the agent."""
    from invincat_cli import agent as _agent

    template = (_agent.Path(__file__).parent / "system_prompt.md").read_text()
    mode_description, interactive_preamble, ambiguity_guidance = _mode_sections(
        interactive
    )
    model_identity_section = build_model_identity_section(
        _agent.settings.model_name,
        provider=_agent.settings.model_provider,
        context_limit=_agent.settings.model_context_limit,
        unsupported_modalities=_agent.settings.model_unsupported_modalities,
    )

    result = (
        template.replace("{mode_description}", mode_description)
        .replace("{interactive_preamble}", interactive_preamble)
        .replace("{ambiguity_guidance}", ambiguity_guidance)
        .replace("{model_identity_section}", model_identity_section)
        .replace("{current_time_section}", _current_time_section())
        .replace("{working_dir_section}", _working_dir_section(sandbox_type, cwd))
        .replace("{skills_path}", f"~/.invincat/{assistant_id}/skills")
    )

    unreplaced = re.findall(r"\{[a-z_]+\}", result)
    if unreplaced:
        _agent.logger.warning(
            "System prompt contains unreplaced placeholders: %s", unreplaced
        )

    return result
