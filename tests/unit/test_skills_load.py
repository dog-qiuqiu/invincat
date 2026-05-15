from __future__ import annotations

from pathlib import Path

import pytest

from invincat_cli.skills import load


class FakeFilesystemBackend:
    def __init__(self, *, root_dir: str) -> None:
        self.root_dir = root_dir


def _skill(name: str, path: Path, description: str = "desc") -> dict[str, object]:
    return {
        "name": name,
        "description": description,
        "path": str(path / name / "SKILL.md"),
        "metadata": {"existing": "value"},
    }


def test_list_skills_merges_sources_by_precedence(monkeypatch, tmp_path: Path) -> None:
    built_in = tmp_path / "built_in"
    user = tmp_path / "user"
    user_agent = tmp_path / "user_agent"
    project = tmp_path / "project"
    project_agent = tmp_path / "project_agent"
    claude_user = tmp_path / "claude_user"
    claude_project = tmp_path / "claude_project"
    for directory in (
        built_in,
        user,
        user_agent,
        project,
        project_agent,
        claude_user,
        claude_project,
    ):
        directory.mkdir()

    skills_by_root = {
        str(built_in): [_skill("shared", built_in, "built")],
        str(user): [_skill("shared", user, "user")],
        str(user_agent): [_skill("alias-only", user_agent)],
        str(project): [_skill("shared", project, "project")],
        str(project_agent): [_skill("project-alias", project_agent)],
        str(claude_user): [_skill("claude", claude_user)],
        str(claude_project): [_skill("claude", claude_project, "higher")],
    }
    monkeypatch.setattr(load, "FilesystemBackend", FakeFilesystemBackend)
    monkeypatch.setattr(
        load,
        "list_skills_from_backend",
        lambda backend, source_path: skills_by_root[backend.root_dir],
    )

    skills = load.list_skills(
        built_in_skills_dir=built_in,
        user_skills_dir=user,
        project_skills_dir=project,
        user_agent_skills_dir=user_agent,
        project_agent_skills_dir=project_agent,
        user_claude_skills_dir=claude_user,
        project_claude_skills_dir=claude_project,
    )
    by_name = {skill["name"]: skill for skill in skills}

    assert by_name["shared"]["source"] == "project"
    assert by_name["shared"]["description"] == "project"
    assert by_name["alias-only"]["source"] == "user"
    assert by_name["project-alias"]["source"] == "project"
    assert by_name["claude"]["source"] == "claude (experimental)"
    assert by_name["claude"]["description"] == "higher"


def test_list_skills_adds_cli_version_to_built_in_metadata(
    monkeypatch,
    tmp_path: Path,
) -> None:
    built_in = tmp_path / "built_in"
    built_in.mkdir()
    monkeypatch.setattr(load, "FilesystemBackend", FakeFilesystemBackend)
    monkeypatch.setattr(
        load,
        "list_skills_from_backend",
        lambda backend, source_path: [_skill("builtin", Path(backend.root_dir))],
    )

    [skill] = load.list_skills(built_in_skills_dir=built_in)

    assert skill["source"] == "built-in"
    assert skill["metadata"]["existing"] == "value"
    assert skill["metadata"]["deepagents-cli-version"] == load._cli_version


def test_list_skills_skips_missing_and_broken_sources(
    monkeypatch, tmp_path: Path
) -> None:
    good = tmp_path / "good"
    broken = tmp_path / "broken"
    good.mkdir()
    broken.mkdir()
    monkeypatch.setattr(load, "FilesystemBackend", FakeFilesystemBackend)

    def list_or_fail(backend: FakeFilesystemBackend, source_path: str) -> list[dict]:
        if backend.root_dir == str(broken):
            raise OSError("unreadable")
        return [_skill("good", Path(backend.root_dir))]

    monkeypatch.setattr(load, "list_skills_from_backend", list_or_fail)

    skills = load.list_skills(
        user_skills_dir=good,
        project_skills_dir=broken,
        built_in_skills_dir=tmp_path / "missing",
    )

    assert [skill["name"] for skill in skills] == ["good"]


def test_load_skill_content_reads_allowed_files(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_file = root / "demo" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("content", encoding="utf-8")

    assert load.load_skill_content(str(skill_file), allowed_roots=[root.resolve()]) == (
        "content"
    )
    assert load.load_skill_content(str(skill_file)) == "content"


def test_load_skill_content_rejects_paths_outside_allowed_roots(tmp_path: Path) -> None:
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside" / "SKILL.md"
    allowed.mkdir()
    outside.parent.mkdir()
    outside.write_text("content", encoding="utf-8")

    with pytest.raises(PermissionError, match="outside all allowed skill"):
        load.load_skill_content(str(outside), allowed_roots=[allowed.resolve()])


def test_load_skill_content_returns_none_for_read_failures(
    monkeypatch,
    tmp_path: Path,
) -> None:
    skill_file = tmp_path / "SKILL.md"
    skill_file.write_text("content", encoding="utf-8")

    def fail_read_text(self: Path, *, encoding: str) -> str:
        raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")

    monkeypatch.setattr(Path, "read_text", fail_read_text)

    assert load.load_skill_content(str(skill_file)) is None
