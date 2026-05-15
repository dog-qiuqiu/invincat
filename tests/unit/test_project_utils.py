"""Tests for project_utils.find_project_root."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from invincat_cli.core.env_vars import SERVER_ENV_PREFIX
from invincat_cli.project_utils import (
    ProjectContext,
    find_project_root,
    get_server_project_context,
)


def test_project_context_rejects_relative_paths() -> None:
    with pytest.raises(ValueError, match="user_cwd"):
        ProjectContext(user_cwd=Path("relative"))

    with pytest.raises(ValueError, match="project_root"):
        ProjectContext(user_cwd=Path("/tmp"), project_root=Path("relative"))


def test_project_context_from_user_cwd_resolves_root_and_user_paths(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    nested = project / "src"
    nested.mkdir(parents=True)
    (project / "pyproject.toml").touch()

    context = ProjectContext.from_user_cwd(nested)

    assert context.user_cwd == nested.resolve()
    assert context.project_root == project.resolve()
    assert context.resolve_user_path("README.md") == (nested / "README.md").resolve()
    assert (
        context.resolve_user_path(project / "README.md")
        == (project / "README.md").resolve()
    )
    assert context.project_skills_dir() == project.resolve() / ".invincat" / "skills"
    assert (
        context.project_agent_skills_dir() == project.resolve() / ".agents" / "skills"
    )


def test_project_context_without_project_root_has_no_project_skill_dirs(
    tmp_path: Path,
) -> None:
    context = ProjectContext(user_cwd=tmp_path.resolve())

    assert context.project_skills_dir() is None
    assert context.project_agent_skills_dir() is None


def test_get_server_project_context_uses_transport_project_root(tmp_path: Path) -> None:
    cwd = tmp_path / "work"
    root = tmp_path / "root"
    cwd.mkdir()
    root.mkdir()
    env = {
        f"{SERVER_ENV_PREFIX}CWD": str(cwd),
        f"{SERVER_ENV_PREFIX}PROJECT_ROOT": str(root),
    }

    context = get_server_project_context(env)

    assert context == ProjectContext(
        user_cwd=cwd.resolve(), project_root=root.resolve()
    )


def test_get_server_project_context_falls_back_to_detected_root(
    tmp_path: Path,
) -> None:
    cwd = tmp_path / "project" / "src"
    cwd.mkdir(parents=True)
    (tmp_path / "project" / "pyproject.toml").touch()
    env = {f"{SERVER_ENV_PREFIX}CWD": str(cwd)}

    context = get_server_project_context(env)

    assert context == ProjectContext(
        user_cwd=cwd.resolve(),
        project_root=(tmp_path / "project").resolve(),
    )


def test_get_server_project_context_returns_none_when_paths_cannot_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_resolve(self: Path) -> Path:
        raise OSError(f"cannot resolve {self}")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    assert get_server_project_context({f"{SERVER_ENV_PREFIX}CWD": "/tmp/work"}) is None


def test_get_server_project_context_returns_none_without_cwd() -> None:
    assert get_server_project_context({}) is None


class TestFindProjectRoot:
    def test_returns_none_when_no_markers(self, tmp_path: Path) -> None:
        subdir = tmp_path / "a" / "b"
        subdir.mkdir(parents=True)
        assert find_project_root(subdir) is None

    def test_detects_git_in_cwd(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        assert find_project_root(tmp_path) == tmp_path

    def test_detects_git_in_parent(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        subdir = tmp_path / "sub"
        subdir.mkdir()
        assert find_project_root(subdir) == tmp_path

    def test_detects_invincat_in_project(self, tmp_path: Path) -> None:
        (tmp_path / ".invincat").mkdir()
        assert find_project_root(tmp_path) == tmp_path

    def test_does_not_use_home_invincat_as_project_root(self, tmp_path: Path) -> None:
        """~/.invincat is user-level storage and must never be treated as a project root."""
        # Simulate: home = tmp_path, project dir is a subdirectory with no markers.
        home = tmp_path
        (home / ".invincat").mkdir()  # user-level storage exists in home
        project_dir = home / "myproject"
        project_dir.mkdir()

        with patch("invincat_cli.project_utils.Path.home", return_value=home):
            result = find_project_root(project_dir)

        assert result is None, (
            f"Expected None because ~/.invincat is user storage, got {result}"
        )

    def test_invincat_in_non_home_dir_is_still_a_marker(self, tmp_path: Path) -> None:
        """A .invincat directory outside home is a valid project marker."""
        home = tmp_path / "home"
        home.mkdir()
        project = tmp_path / "projects" / "myproject"
        project.mkdir(parents=True)
        (project / ".invincat").mkdir()

        with patch("invincat_cli.project_utils.Path.home", return_value=home):
            result = find_project_root(project)

        assert result == project

    def test_git_in_home_still_detected(self, tmp_path: Path) -> None:
        """Other markers in home directory are still valid (e.g. a bare repo at ~)."""
        home = tmp_path
        (home / ".invincat").mkdir()
        (home / ".git").mkdir()
        subdir = home / "work"
        subdir.mkdir()

        with patch("invincat_cli.project_utils.Path.home", return_value=home):
            result = find_project_root(subdir)

        assert result == home

    def test_nearest_marker_wins(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        inner = tmp_path / "pkg"
        inner.mkdir()
        (inner / "pyproject.toml").touch()

        assert find_project_root(inner) == inner

    @pytest.mark.parametrize(
        "marker", ["pyproject.toml", "package.json", "go.mod", "Cargo.toml"]
    )
    def test_detects_other_markers(self, tmp_path: Path, marker: str) -> None:
        (tmp_path / marker).touch()
        assert find_project_root(tmp_path) == tmp_path
