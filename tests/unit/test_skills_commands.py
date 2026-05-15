from __future__ import annotations

import argparse
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from invincat_cli.skills import commands


class _FakeSettings:
    def __init__(self, root: Path, *, project_root: Path | None) -> None:
        self.root = root
        self.project_root = project_root
        self.user_skills = root / "home" / ".invincat" / "agent" / "skills"
        self.user_agent_skills = root / "home" / ".agents" / "skills"
        self.built_in_skills = root / "pkg" / "built_in_skills"
        self.user_claude_skills = root / "home" / ".claude" / "skills"
        self.extra_skills = root / "extra" / "skills"

    def get_user_skills_dir(self, _agent: str) -> Path:
        return self.user_skills

    def ensure_user_skills_dir(self, _agent: str) -> Path:
        self.user_skills.mkdir(parents=True, exist_ok=True)
        return self.user_skills

    def get_project_skills_dir(self) -> Path | None:
        if self.project_root is None:
            return None
        return self.project_root / ".invincat" / "skills"

    def ensure_project_skills_dir(self) -> Path | None:
        skills_dir = self.get_project_skills_dir()
        if skills_dir is not None:
            skills_dir.mkdir(parents=True, exist_ok=True)
        return skills_dir

    def get_user_agent_skills_dir(self) -> Path:
        return self.user_agent_skills

    def get_project_agent_skills_dir(self) -> Path | None:
        if self.project_root is None:
            return None
        return self.project_root / ".agents" / "skills"

    def get_built_in_skills_dir(self) -> Path:
        return self.built_in_skills

    def get_user_claude_skills_dir(self) -> Path:
        return self.user_claude_skills

    def get_project_claude_skills_dir(self) -> Path | None:
        if self.project_root is None:
            return None
        return self.project_root / ".claude" / "skills"

    def get_extra_skills_dirs(self) -> list[Path]:
        return [self.extra_skills]


def _install_fake_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    project: bool = True,
) -> _FakeSettings:
    import invincat_cli.config as config_module

    settings = _FakeSettings(
        tmp_path,
        project_root=(tmp_path / "project") if project else None,
    )
    monkeypatch.setattr(
        config_module.Settings,
        "from_environment",
        staticmethod(lambda: settings),
    )
    return settings


def _capture_json(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    import invincat_cli.io.output as output_module

    calls: list[tuple[str, Any]] = []
    monkeypatch.setattr(
        output_module,
        "write_json",
        lambda label, payload: calls.append((label, payload)),
    )
    return calls


def _skill(path: Path, *, name: str = "demo", source: str = "user") -> dict[str, Any]:
    return {
        "name": name,
        "description": f"{name} description",
        "path": str(path),
        "source": source,
        "metadata": {},
    }


def _patch_list_skills(
    monkeypatch: pytest.MonkeyPatch, skills: list[dict[str, Any]]
) -> None:
    import invincat_cli.skills.load as load_module

    monkeypatch.setattr(load_module, "list_skills", lambda **_kwargs: skills)


def test_validate_skill_name_accepts_spec_names() -> None:
    assert commands._validate_name("web-research") == (True, "")
    assert commands._validate_name("über-tool") == (True, "")
    assert commands._validate_name("skill9") == (True, "")


@pytest.mark.parametrize(
    ("name", "error"),
    [
        ("", "cannot be empty"),
        ("bad/name", "cannot contain path components"),
        ("bad\\name", "cannot contain path components"),
        ("../bad", "cannot contain path components"),
        ("bad--name", "must be lowercase alphanumeric"),
        ("Bad", "must be lowercase alphanumeric"),
        ("bad_name", "must be lowercase alphanumeric"),
        ("a" * 65, "cannot exceed 64 characters"),
    ],
)
def test_validate_skill_name_rejects_invalid_names(name: str, error: str) -> None:
    is_valid, message = commands._validate_name(name)

    assert not is_valid
    assert error in message


def test_validate_agent_name_allows_underscores_but_not_paths() -> None:
    assert commands._validate_agent_name("agent_one") == (True, "")

    assert commands._validate_agent_name("")[0] is False
    assert commands._validate_agent_name("a" * 65)[0] is False
    assert commands._validate_agent_name("-agent")[0] is False
    assert commands._validate_agent_name("agent/one")[0] is False
    assert commands._validate_agent_name("Agent")[0] is False


def test_validate_skill_path_requires_child_path(tmp_path: Path) -> None:
    base_dir = tmp_path / "skills"
    skill_dir = base_dir / "demo"
    skill_dir.mkdir(parents=True)

    assert commands._validate_skill_path(skill_dir, base_dir) == (True, "")

    outside = tmp_path / "other"
    outside.mkdir()
    is_valid, error = commands._validate_skill_path(outside, base_dir)
    assert not is_valid
    assert "within" in error


def test_validate_skill_path_reports_resolve_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_resolve = Path.resolve
    skill_dir = tmp_path / "skills" / "demo"
    base_dir = tmp_path / "skills"

    def fake_resolve(self: Path) -> Path:
        if self == skill_dir:
            raise OSError("broken path")
        return real_resolve(self)

    monkeypatch.setattr(Path, "resolve", fake_resolve)

    is_valid, error = commands._validate_skill_path(skill_dir, base_dir)

    assert not is_valid
    assert "broken path" in error


def test_find_containing_skills_dir_supports_alias_paths(tmp_path: Path) -> None:
    user_dir = tmp_path / ".invincat" / "agent" / "skills"
    alias_dir = tmp_path / ".agents" / "skills"
    skill_dir = alias_dir / "demo"
    skill_dir.mkdir(parents=True)

    assert (
        commands._find_containing_skills_dir(skill_dir, [user_dir, alias_dir])
        == alias_dir
    )
    assert (
        commands._find_containing_skills_dir(
            tmp_path / "outside", [user_dir, alias_dir]
        )
        is None
    )
    assert (
        commands._find_containing_skills_dir(skill_dir, [None, alias_dir]) == alias_dir
    )


def test_format_info_fields_includes_optional_metadata() -> None:
    skill = {
        "license": "Apache-2.0",
        "compatibility": "CLI",
        "allowed_tools": ["Bash", "Read"],
        "metadata": {"owner": "team"},
    }

    assert commands._format_info_fields(skill) == [
        ("License", "Apache-2.0"),
        ("Compatibility", "CLI"),
        ("Allowed Tools", "Bash, Read"),
        ("Metadata", "owner=team"),
    ]


def test_generate_template_contains_skill_name_and_frontmatter() -> None:
    template = commands._generate_template("code-review")

    assert "name: code-review" in template
    assert "# Code Review" in template
    assert "allowed-tools:" in template


def test_list_json_writes_discovered_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_settings(monkeypatch, tmp_path)
    skill_path = (
        tmp_path / "home" / ".invincat" / "agent" / "skills" / "demo" / "SKILL.md"
    )
    skills = [_skill(skill_path)]
    _patch_list_skills(monkeypatch, skills)
    json_calls = _capture_json(monkeypatch)

    commands._list("agent", output_format="json")

    assert json_calls == [("skills list", skills)]


def test_list_project_json_returns_empty_without_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_settings(monkeypatch, tmp_path, project=False)
    json_calls = _capture_json(monkeypatch)

    commands._list("agent", project=True, output_format="json")

    assert json_calls == [("skills list", [])]


def test_list_project_text_reports_missing_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_settings(monkeypatch, tmp_path, project=False)

    commands._list("agent", project=True)


def test_list_project_json_returns_empty_when_project_dirs_have_no_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    settings.get_project_skills_dir().mkdir(parents=True)
    settings.get_project_agent_skills_dir().mkdir(parents=True)
    json_calls = _capture_json(monkeypatch)

    commands._list("agent", project=True, output_format="json")

    assert json_calls == [("skills list", [])]


def test_list_project_text_reports_empty_project_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    settings.get_project_skills_dir().mkdir(parents=True)
    settings.get_project_agent_skills_dir().mkdir(parents=True)

    commands._list("agent", project=True)


def test_list_text_handles_no_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_settings(monkeypatch, tmp_path)
    _patch_list_skills(monkeypatch, [])

    commands._list("agent")


def test_list_text_groups_user_project_and_builtin_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_settings(monkeypatch, tmp_path)
    skills = [
        _skill(tmp_path / "user" / "demo" / "SKILL.md", name="user-skill"),
        _skill(
            tmp_path / "project" / "demo" / "SKILL.md",
            name="project-skill",
            source="project",
        ),
        _skill(
            tmp_path / "built-in" / "demo" / "SKILL.md",
            name="builtin-skill",
            source="built-in",
        ),
    ]
    _patch_list_skills(monkeypatch, skills)

    commands._list("agent")


def test_list_project_json_loads_project_skills_when_alias_dir_has_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    project_agent_skills_dir = settings.get_project_agent_skills_dir()
    assert project_agent_skills_dir is not None
    marker = project_agent_skills_dir / "demo"
    marker.mkdir(parents=True)
    skills = [_skill(marker / "SKILL.md", source="project")]
    _patch_list_skills(monkeypatch, skills)
    json_calls = _capture_json(monkeypatch)

    commands._list("agent", project=True, output_format="json")

    assert json_calls == [("skills list", skills)]


def test_list_project_text_loads_project_skills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    project_skills_dir = settings.get_project_skills_dir()
    assert project_skills_dir is not None
    marker = project_skills_dir / "demo"
    marker.mkdir(parents=True)
    _patch_list_skills(monkeypatch, [_skill(marker / "SKILL.md", source="project")])

    commands._list("agent", project=True)


def test_create_user_skill_writes_template_and_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    json_calls = _capture_json(monkeypatch)

    commands._create("demo-skill", "agent", output_format="json")

    skill_dir = settings.user_skills / "demo-skill"
    skill_md = skill_dir / "SKILL.md"
    assert skill_md.exists()
    assert "name: demo-skill" in skill_md.read_text(encoding="utf-8")
    assert json_calls == [
        (
            "skills create",
            {"name": "demo-skill", "path": str(skill_dir), "project": False},
        )
    ]


def test_create_rejects_invalid_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_settings(monkeypatch, tmp_path)

    with pytest.raises(SystemExit):
        commands._create("Bad_Name", "agent")


def test_create_existing_skill_json_reports_already_existed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    skill_dir = settings.user_skills / "demo"
    skill_dir.mkdir(parents=True)
    json_calls = _capture_json(monkeypatch)

    commands._create("demo", "agent", output_format="json")

    assert json_calls == [
        (
            "skills create",
            {
                "name": "demo",
                "path": str(skill_dir),
                "project": False,
                "already_existed": True,
            },
        )
    ]


def test_create_existing_skill_text_reports_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    skill_dir = settings.user_skills / "demo"
    skill_dir.mkdir(parents=True)

    commands._create("demo", "agent")


def test_create_user_skill_text_reports_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)

    commands._create("demo", "agent")

    assert (settings.user_skills / "demo" / "SKILL.md").exists()


def test_create_project_skill_requires_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_settings(monkeypatch, tmp_path, project=False)

    with pytest.raises(SystemExit):
        commands._create("demo", "agent", project=True)


def test_create_project_exits_when_directory_cannot_be_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "ensure_project_skills_dir", lambda: None)

    with pytest.raises(SystemExit):
        commands._create("demo", "agent", project=True)


def test_create_rejects_invalid_skill_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_settings(monkeypatch, tmp_path)
    monkeypatch.setattr(
        commands,
        "_validate_skill_path",
        lambda _skill_dir, _base_dir: (False, "outside"),
    )

    with pytest.raises(SystemExit):
        commands._create("demo", "agent")


def test_info_json_returns_skill_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_settings(monkeypatch, tmp_path)
    skill_path = (
        tmp_path / "home" / ".invincat" / "agent" / "skills" / "demo" / "SKILL.md"
    )
    skill = _skill(skill_path)
    _patch_list_skills(monkeypatch, [skill])
    json_calls = _capture_json(monkeypatch)

    commands._info("demo", output_format="json")

    assert json_calls == [("skills info", skill)]


def test_info_project_requires_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_settings(monkeypatch, tmp_path, project=False)

    with pytest.raises(SystemExit):
        commands._info("demo", project=True)


def test_info_project_json_uses_project_only_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    project_skills_dir = settings.get_project_skills_dir()
    assert project_skills_dir is not None
    skill = _skill(project_skills_dir / "demo" / "SKILL.md", source="project")
    calls: list[dict[str, Any]] = []

    import invincat_cli.skills.load as load_module

    def fake_list_skills(**kwargs: Any) -> list[dict[str, Any]]:
        calls.append(kwargs)
        return [skill]

    monkeypatch.setattr(load_module, "list_skills", fake_list_skills)
    json_calls = _capture_json(monkeypatch)

    commands._info("demo", project=True, output_format="json")

    assert calls[0]["user_skills_dir"] is None
    assert calls[0]["project_skills_dir"] == project_skills_dir
    assert json_calls == [("skills info", skill)]


def test_info_text_reads_content_supporting_files_and_shadow_notice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    project_skills_dir = settings.get_project_skills_dir()
    assert project_skills_dir is not None
    project_skill_dir = project_skills_dir / "demo"
    project_skill_dir.mkdir(parents=True)
    skill_path = project_skill_dir / "SKILL.md"
    skill_path.write_text("---\nname: demo\n---\nbody", encoding="utf-8")
    (project_skill_dir / "script.py").write_text("print('ok')", encoding="utf-8")
    project_skill = _skill(skill_path, source="project")
    user_skill = _skill(settings.user_skills / "demo" / "SKILL.md")

    import invincat_cli.skills.load as load_module

    def fake_list_skills(**kwargs: Any) -> list[dict[str, Any]]:
        if kwargs.get("project_skills_dir") is None:
            return [user_skill]
        return [project_skill]

    monkeypatch.setattr(load_module, "list_skills", fake_list_skills)

    commands._info("demo")


def test_info_text_handles_shadow_detection_and_supporting_file_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    project_skills_dir = settings.get_project_skills_dir()
    assert project_skills_dir is not None
    skill_dir = project_skills_dir / "demo"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("---\nname: demo\n---\nbody", encoding="utf-8")
    skill = {
        **_skill(skill_path, source="project"),
        "license": "MIT",
        "compatibility": "CLI",
        "allowed_tools": ["Read"],
        "metadata": {"owner": "team"},
    }

    import invincat_cli.skills.load as load_module

    calls = 0

    def fake_list_skills(**_kwargs: Any) -> list[dict[str, Any]]:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("cosmetic failure")
        return [skill]

    def fake_iterdir(self: Path) -> list[Path]:
        if self == skill_dir:
            raise OSError("cannot list")
        return []

    monkeypatch.setattr(load_module, "list_skills", fake_list_skills)
    monkeypatch.setattr(Path, "iterdir", fake_iterdir)

    commands._info("demo")


@pytest.mark.parametrize(
    "failure",
    [PermissionError("outside"), OSError("read failed"), None],
)
def test_info_text_exits_on_content_read_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception | None
) -> None:
    _install_fake_settings(monkeypatch, tmp_path)
    skill_path = (
        tmp_path / "home" / ".invincat" / "agent" / "skills" / "demo" / "SKILL.md"
    )
    _patch_list_skills(monkeypatch, [_skill(skill_path)])
    import invincat_cli.skills.load as load_module

    def fake_load_skill_content(_path: str, *, allowed_roots: list[Path]) -> str | None:
        if failure is not None:
            raise failure
        return None

    monkeypatch.setattr(load_module, "load_skill_content", fake_load_skill_content)

    with pytest.raises(SystemExit):
        commands._info("demo")


def test_info_missing_skill_lists_available_and_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_settings(monkeypatch, tmp_path)
    _patch_list_skills(
        monkeypatch,
        [_skill(tmp_path / "skills" / "other" / "SKILL.md", name="other")],
    )

    with pytest.raises(SystemExit):
        commands._info("demo")


def test_delete_user_agent_alias_skill_json_deletes_with_correct_base(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    skill_dir = settings.user_agent_skills / "demo"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("---\nname: demo\n---\n", encoding="utf-8")
    _patch_list_skills(monkeypatch, [_skill(skill_path, source="user")])
    json_calls = _capture_json(monkeypatch)

    commands._delete("demo", force=True, output_format="json")

    assert not skill_dir.exists()
    assert json_calls == [
        (
            "skills delete",
            {"name": "demo", "path": str(skill_dir), "deleted": True},
        )
    ]


def test_delete_project_agent_alias_dry_run_json_does_not_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    project_agent_skills_dir = settings.get_project_agent_skills_dir()
    assert project_agent_skills_dir is not None
    skill_dir = project_agent_skills_dir / "demo"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("---\nname: demo\n---\n", encoding="utf-8")
    _patch_list_skills(monkeypatch, [_skill(skill_path, source="project")])
    json_calls = _capture_json(monkeypatch)

    commands._delete("demo", project=True, dry_run=True, output_format="json")

    assert skill_dir.exists()
    assert json_calls == [
        (
            "skills delete",
            {"name": "demo", "path": str(skill_dir), "dry_run": True},
        )
    ]


def test_delete_missing_skill_exits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_settings(monkeypatch, tmp_path)
    _patch_list_skills(monkeypatch, [])

    with pytest.raises(SystemExit):
        commands._delete("demo")


def test_delete_missing_skill_lists_project_and_user_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_settings(monkeypatch, tmp_path)
    _patch_list_skills(
        monkeypatch,
        [
            _skill(tmp_path / "user" / "SKILL.md", name="user-skill"),
            _skill(
                tmp_path / "project" / "SKILL.md",
                name="project-skill",
                source="project",
            ),
        ],
    )

    with pytest.raises(SystemExit):
        commands._delete("missing")


def test_delete_rejects_invalid_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_settings(monkeypatch, tmp_path)

    with pytest.raises(SystemExit):
        commands._delete("Bad_Name")


def test_delete_project_requires_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_settings(monkeypatch, tmp_path, project=False)

    with pytest.raises(SystemExit):
        commands._delete("demo", project=True)


def test_delete_rejects_skill_outside_known_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_fake_settings(monkeypatch, tmp_path)
    outside_skill = tmp_path / "outside" / "demo" / "SKILL.md"
    _patch_list_skills(monkeypatch, [_skill(outside_skill)])

    with pytest.raises(SystemExit):
        commands._delete("demo", force=True)


def test_delete_dry_run_text_does_not_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    skill_dir = settings.user_skills / "demo"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("---\nname: demo\n---\n", encoding="utf-8")
    _patch_list_skills(monkeypatch, [_skill(skill_path)])

    commands._delete("demo", dry_run=True)

    assert skill_dir.exists()


def test_delete_unconfirmed_text_mode_leaves_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    skill_dir = settings.user_skills / "demo"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("---\nname: demo\n---\n", encoding="utf-8")
    _patch_list_skills(monkeypatch, [_skill(skill_path)])
    monkeypatch.setattr("builtins.input", lambda: "n")

    commands._delete("demo")

    assert skill_dir.exists()


def test_delete_eof_confirmation_cancels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    skill_dir = settings.user_skills / "demo"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("---\nname: demo\n---\n", encoding="utf-8")
    _patch_list_skills(monkeypatch, [_skill(skill_path)])

    def fake_input() -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", fake_input)

    commands._delete("demo")

    assert skill_dir.exists()


def test_delete_text_force_success_deletes_skill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    skill_dir = settings.user_skills / "demo"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("---\nname: demo\n---\n", encoding="utf-8")
    _patch_list_skills(monkeypatch, [_skill(skill_path)])

    commands._delete("demo", force=True)

    assert not skill_dir.exists()


def test_delete_text_reports_unavailable_file_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    skill_dir = settings.user_skills / "demo"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("---\nname: demo\n---\n", encoding="utf-8")
    _patch_list_skills(monkeypatch, [_skill(skill_path)])

    def fake_rglob(self: Path, pattern: str) -> list[Path]:
        if self == skill_dir and pattern == "*":
            raise OSError("cannot count")
        return []

    monkeypatch.setattr(Path, "rglob", fake_rglob)

    commands._delete("demo", force=True)

    assert not skill_dir.exists()


def test_delete_rejects_symlink_skill_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    skill_dir = settings.user_skills / "demo"
    skill_dir.parent.mkdir(parents=True)
    try:
        skill_dir.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unsupported: {exc}")
    skill_path = skill_dir / "SKILL.md"
    _patch_list_skills(monkeypatch, [_skill(skill_path)])

    with pytest.raises(SystemExit):
        commands._delete("demo", force=True)


def test_delete_rejects_symlink_after_confirmation_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    skill_dir = settings.user_skills / "demo"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("---\nname: demo\n---\n", encoding="utf-8")
    _patch_list_skills(monkeypatch, [_skill(skill_path)])

    def fake_is_symlink(self: Path) -> bool:
        return self == skill_dir

    monkeypatch.setattr(Path, "is_symlink", fake_is_symlink)

    with pytest.raises(SystemExit):
        commands._delete("demo", force=True)


def test_delete_revalidates_known_root_before_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    skill_dir = settings.user_skills / "demo"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("---\nname: demo\n---\n", encoding="utf-8")
    _patch_list_skills(monkeypatch, [_skill(skill_path)])
    calls = 0

    def fake_find(_skill_dir: Path, _candidate_dirs: list[Path | None]) -> Path | None:
        nonlocal calls
        calls += 1
        return settings.user_skills if calls == 1 else None

    monkeypatch.setattr(commands, "_find_containing_skills_dir", fake_find)

    with pytest.raises(SystemExit):
        commands._delete("demo", force=True)


def test_delete_reports_rmtree_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _install_fake_settings(monkeypatch, tmp_path)
    skill_dir = settings.user_skills / "demo"
    skill_dir.mkdir(parents=True)
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("---\nname: demo\n---\n", encoding="utf-8")
    _patch_list_skills(monkeypatch, [_skill(skill_path)])

    def fake_rmtree(_path: Path) -> None:
        raise OSError("boom")

    monkeypatch.setattr(commands.shutil, "rmtree", fake_rmtree)

    with pytest.raises(SystemExit):
        commands._delete("demo", force=True)


def test_execute_skills_command_dispatches_subcommands(monkeypatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        commands,
        "_list",
        lambda **kwargs: calls.append(("list", kwargs)),
    )
    monkeypatch.setattr(
        commands,
        "_create",
        lambda name, **kwargs: calls.append(("create", {"name": name, **kwargs})),
    )
    monkeypatch.setattr(
        commands,
        "_info",
        lambda name, **kwargs: calls.append(("info", {"name": name, **kwargs})),
    )
    monkeypatch.setattr(
        commands,
        "_delete",
        lambda name, **kwargs: calls.append(("delete", {"name": name, **kwargs})),
    )

    commands.execute_skills_command(
        SimpleNamespace(
            skills_command="ls",
            agent="agent_one",
            project=True,
            output_format="json",
        )
    )
    commands.execute_skills_command(
        SimpleNamespace(
            skills_command="create",
            name="demo",
            agent="agent",
            project=False,
            output_format="text",
        )
    )
    commands.execute_skills_command(
        SimpleNamespace(
            skills_command="info",
            name="demo",
            agent="agent",
            project=False,
            output_format="text",
        )
    )
    commands.execute_skills_command(
        SimpleNamespace(
            skills_command="delete",
            name="demo",
            agent="agent",
            project=False,
            force=True,
            dry_run=True,
            output_format="json",
        )
    )

    assert calls == [
        ("list", {"agent": "agent_one", "project": True, "output_format": "json"}),
        (
            "create",
            {
                "name": "demo",
                "agent": "agent",
                "project": False,
                "output_format": "text",
            },
        ),
        (
            "info",
            {
                "name": "demo",
                "agent": "agent",
                "project": False,
                "output_format": "text",
            },
        ),
        (
            "delete",
            {
                "name": "demo",
                "agent": "agent",
                "project": False,
                "force": True,
                "dry_run": True,
                "output_format": "json",
            },
        ),
    ]


def test_execute_skills_command_rejects_invalid_agent_name() -> None:
    with pytest.raises(SystemExit):
        commands.execute_skills_command(
            SimpleNamespace(
                skills_command="list",
                agent="../bad",
                project=False,
                output_format="text",
            )
        )


def test_setup_skills_parser_adds_subcommands_and_output_args() -> None:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")

    def make_help_action(_show_help: object) -> type[argparse.Action]:
        class HelpAction(argparse.Action):
            def __call__(self, *args: object, **kwargs: object) -> None:
                raise SystemExit(0)

        return HelpAction

    def add_output_args(value: argparse.ArgumentParser) -> None:
        value.add_argument("--json", action="store_true", dest="json_output")

    commands.setup_skills_parser(
        subparsers,
        make_help_action=make_help_action,
        add_output_args=add_output_args,
    )

    parsed = parser.parse_args(["skills", "delete", "demo", "--force", "--json"])

    assert parsed.command == "skills"
    assert parsed.skills_command == "delete"
    assert parsed.name == "demo"
    assert parsed.force is True
    assert parsed.json_output is True


def test_setup_skills_parser_lazy_help_action_invokes_ui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.presentation.help as ui

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    shown: list[str] = []
    monkeypatch.setattr(ui, "show_skills_list_help", lambda: shown.append("list"))

    def make_help_action(show_help: object) -> type[argparse.Action]:
        class HelpAction(argparse.Action):
            def __init__(self, *args: object, **kwargs: object) -> None:
                kwargs["nargs"] = 0
                super().__init__(*args, **kwargs)

            def __call__(self, *args: object, **kwargs: object) -> None:
                show_help()
                raise SystemExit(0)

        return HelpAction

    commands.setup_skills_parser(subparsers, make_help_action=make_help_action)

    with pytest.raises(SystemExit):
        parser.parse_args(["skills", "list", "--help"])

    assert shown == ["list"]


def test_execute_skills_command_without_subcommand_shows_help(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.presentation.help as ui

    shown: list[str] = []
    monkeypatch.setattr(ui, "show_skills_help", lambda: shown.append("help"))

    commands.execute_skills_command(
        SimpleNamespace(skills_command=None, agent="agent", output_format="text")
    )

    assert shown == ["help"]
