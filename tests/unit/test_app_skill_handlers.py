from __future__ import annotations

import asyncio
from pathlib import Path

from invincat_cli.app_runtime import skill_handlers
from invincat_cli.widgets.messages import AppMessage, SkillMessage, UserMessage


class SkillApp:
    def __init__(self) -> None:
        self._discovered_skills: list[dict[str, object]] = []
        self._skill_allowed_roots: list[str] = []
        self.messages: list[object] = []
        self.sent: list[tuple[str, dict[str, object] | None]] = []
        self.discover_result: tuple[list[dict[str, object]], list[str]] = ([], [])

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    def _discover_skills_and_roots(self) -> tuple[list[dict[str, object]], list[str]]:
        return self.discover_result

    async def _send_to_agent(
        self,
        prompt: str,
        *,
        message_kwargs: dict[str, object] | None = None,
    ) -> None:
        self.sent.append((prompt, message_kwargs))


def message_contents(app: SkillApp) -> list[object]:
    return [getattr(message, "_content", None) for message in app.messages]


def skill(name: str = "demo") -> dict[str, object]:
    return {
        "name": name,
        "path": Path("/tmp/demo/SKILL.md"),
        "description": "Demo skill",
        "source": "test",
    }


def test_handle_skill_command_shows_usage_for_missing_name() -> None:
    app = SkillApp()

    asyncio.run(skill_handlers.handle_skill_command(app, "/skill:"))

    assert isinstance(app.messages[0], UserMessage)
    assert isinstance(app.messages[-1], AppMessage)


def test_handle_skill_command_discovers_missing_cache_and_reports_not_found() -> None:
    app = SkillApp()
    app.discover_result = ([], ["/tmp"])

    asyncio.run(skill_handlers.handle_skill_command(app, "/skill:missing"))

    assert app._discovered_skills == []
    assert app._skill_allowed_roots == ["/tmp"]
    assert isinstance(app.messages[-1], AppMessage)


def test_handle_skill_command_reports_discovery_errors() -> None:
    app = SkillApp()

    def fail():
        raise OSError("disk failed")

    app._discover_skills_and_roots = fail

    asyncio.run(skill_handlers.handle_skill_command(app, "/skill:demo"))

    assert "disk failed" in str(message_contents(app)[-1])

    app = SkillApp()

    def explode():
        raise RuntimeError("boom")

    app._discover_skills_and_roots = explode

    asyncio.run(skill_handlers.handle_skill_command(app, "/skill:demo"))

    assert "RuntimeError: boom" in str(message_contents(app)[-1])


def test_handle_skill_command_reports_load_errors(monkeypatch) -> None:
    for exc in [
        PermissionError("outside root"),
        OSError("missing file"),
        RuntimeError("bad read"),
    ]:
        app = SkillApp()
        app._discovered_skills = [skill()]

        def fail(*_args, **_kwargs):
            raise exc

        monkeypatch.setattr("invincat_cli.skills.load.load_skill_content", fail)

        asyncio.run(skill_handlers.handle_skill_command(app, "/skill:demo"))

        assert isinstance(app.messages[-1], AppMessage)


def test_handle_skill_command_reports_empty_or_unreadable_content(monkeypatch) -> None:
    for content in [None, "   "]:
        app = SkillApp()
        app._discovered_skills = [skill()]
        monkeypatch.setattr(
            "invincat_cli.skills.load.load_skill_content",
            lambda *_args, **_kwargs: content,
        )

        asyncio.run(skill_handlers.handle_skill_command(app, "/skill:demo"))

        assert isinstance(app.messages[-1], AppMessage)


def test_handle_skill_command_mounts_skill_and_sends_prompt(monkeypatch) -> None:
    app = SkillApp()
    cached = skill()
    app._discovered_skills = [cached]
    app._skill_allowed_roots = ["/tmp/demo"]
    monkeypatch.setattr(
        "invincat_cli.skills.load.load_skill_content",
        lambda *_args, **_kwargs: "# Demo\nUse this skill.",
    )
    monkeypatch.setattr(
        skill_handlers,
        "build_skill_invocation_prompt",
        lambda **kwargs: f"prompt:{kwargs['args']}",
    )
    monkeypatch.setattr(
        skill_handlers,
        "build_skill_agent_metadata",
        lambda **kwargs: {"skill": kwargs["skill"]["name"], "args": kwargs["args"]},
    )

    asyncio.run(skill_handlers.handle_skill_command(app, "/skill:demo run it"))

    assert isinstance(app.messages[-1], SkillMessage)
    assert app.sent == [
        (
            "prompt:run it",
            {"additional_kwargs": {"skill": "demo", "args": "run it"}},
        )
    ]
