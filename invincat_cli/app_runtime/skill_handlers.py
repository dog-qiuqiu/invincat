"""App-bound `/skill:` command handler."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from invincat_cli.app_runtime.skill import (
    build_skill_agent_metadata,
    build_skill_invocation_prompt,
    find_skill,
)
from invincat_cli.i18n import t
from invincat_cli.widgets.messages import AppMessage, SkillMessage, UserMessage

logger = logging.getLogger(__name__)


async def handle_skill_command(app: Any, command: str) -> None:  # noqa: ANN401
    """Handle a `/skill:<name>` command by loading and invoking a skill."""
    from invincat_cli.commands.registry import parse_skill_command
    from invincat_cli.skills.load import load_skill_content

    skill_name, args = parse_skill_command(command)
    if not skill_name:
        await app._mount_message(UserMessage(command))
        await app._mount_message(AppMessage(t("skill.usage")))
        return

    cached = find_skill(app._discovered_skills, skill_name)
    allowed_roots = app._skill_allowed_roots

    if cached is None:
        try:
            skills, allowed_roots = await asyncio.to_thread(
                app._discover_skills_and_roots
            )
            app._discovered_skills = skills
            app._skill_allowed_roots = allowed_roots
            cached = find_skill(skills, skill_name)
        except OSError as exc:
            logger.warning(
                "Filesystem error loading skill %r", skill_name, exc_info=True
            )
            await app._mount_message(UserMessage(command))
            await app._mount_message(
                AppMessage(
                    t("skill.load_filesystem_error").format(
                        skill=skill_name,
                        error=str(exc),
                    )
                )
            )
            return
        except Exception as exc:
            logger.warning("Error searching for skill %r", skill_name, exc_info=True)
            await app._mount_message(UserMessage(command))
            await app._mount_message(
                AppMessage(
                    t("skill.load_unexpected_error").format(
                        skill=skill_name,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
            )
            return

    if cached is None:
        await app._mount_message(UserMessage(command))
        await app._mount_message(
            AppMessage(t("skill.not_found").format(skill=skill_name))
        )
        return

    skill_path = cached["path"]

    def _load() -> str | None:
        return load_skill_content(str(skill_path), allowed_roots=allowed_roots)

    try:
        content = await asyncio.to_thread(_load)
    except PermissionError as exc:
        logger.warning(
            "Containment check failed for skill %r", skill_name, exc_info=True
        )
        await app._mount_message(UserMessage(command))
        await app._mount_message(
            AppMessage(
                t("skill.load_permission_error").format(
                    skill=skill_name,
                    error=str(exc),
                )
            )
        )
        return
    except OSError as exc:
        logger.warning("Filesystem error loading skill %r", skill_name, exc_info=True)
        await app._mount_message(UserMessage(command))
        await app._mount_message(
            AppMessage(
                t("skill.load_filesystem_error").format(
                    skill=skill_name,
                    error=str(exc),
                )
            )
        )
        return
    except Exception as exc:
        logger.warning("Error reading skill %r", skill_name, exc_info=True)
        await app._mount_message(UserMessage(command))
        await app._mount_message(
            AppMessage(
                t("skill.load_unexpected_error").format(
                    skill=skill_name,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
        )
        return

    if content is None:
        await app._mount_message(UserMessage(command))
        await app._mount_message(
            AppMessage(t("skill.content_unreadable").format(skill=skill_name))
        )
        return

    if not content.strip():
        await app._mount_message(UserMessage(command))
        await app._mount_message(
            AppMessage(t("skill.content_empty").format(skill=skill_name))
        )
        return

    prompt = build_skill_invocation_prompt(
        skill=cached,
        content=content,
        args=args,
    )

    await app._mount_message(
        SkillMessage(
            skill_name=cached["name"],
            description=str(cached.get("description", "")),
            source=str(cached.get("source", "")),
            body=content,
            args=args,
        )
    )
    await app._send_to_agent(
        prompt,
        message_kwargs={
            "additional_kwargs": build_skill_agent_metadata(
                skill=cached,
                args=args,
            ),
        },
    )
