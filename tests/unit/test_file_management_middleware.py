"""Tests for safe project file management tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from invincat_cli.middleware.file_management import FileManagementMiddleware


def _tools(root: Path):
    middleware = FileManagementMiddleware(allowed_root=root)
    return {tool.name: tool for tool in middleware.tools}


def _json(result: str) -> dict:
    return json.loads(result)


def test_file_management_tools_create_move_copy_and_soft_delete(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    source = tmp_path / "note.md"
    source.write_text("hello", encoding="utf-8")

    mkdir_result = _json(tools["mkdir"].invoke({"path": "docs/archive"}))
    assert mkdir_result == {"ok": True, "action": "mkdir", "path": "docs/archive"}

    copy_result = _json(
        tools["copy_file"].invoke(
            {"source": "note.md", "destination": "docs/archive/copy.md"}
        )
    )
    assert copy_result["source"] == "note.md"
    assert copy_result["destination"] == "docs/archive/copy.md"
    assert (tmp_path / "docs/archive/copy.md").read_text(encoding="utf-8") == "hello"

    move_result = _json(
        tools["move_file"].invoke(
            {"source": "note.md", "destination": "docs/archive/moved.md"}
        )
    )
    assert move_result["action"] == "move_file"
    assert not source.exists()
    assert (tmp_path / "docs/archive/moved.md").read_text(encoding="utf-8") == "hello"

    delete_result = _json(
        tools["delete_file"].invoke({"path": "docs/archive/moved.md"})
    )
    assert delete_result["action"] == "delete_file"
    assert delete_result["permanent"] is False
    assert not (tmp_path / "docs/archive/moved.md").exists()
    assert (tmp_path / delete_result["trashed_to"]).read_text(encoding="utf-8") == "hello"


def test_file_info_reports_missing_and_existing_paths(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    missing = _json(tools["file_info"].invoke({"path": "missing.txt"}))
    assert missing == {"path": "missing.txt", "exists": False}

    file_path = tmp_path / "data.txt"
    file_path.write_text("abc", encoding="utf-8")
    info = _json(tools["file_info"].invoke({"path": "data.txt"}))
    assert info["path"] == "data.txt"
    assert info["exists"] is True
    assert info["type"] == "file"
    assert info["size"] == 3
    assert "modified_at" in info


def test_file_management_rejects_outside_and_protected_paths(tmp_path: Path) -> None:
    tools = _tools(tmp_path)

    with pytest.raises(ValueError, match="inside project root"):
        tools["mkdir"].invoke({"path": "../outside"})

    with pytest.raises(ValueError, match="protected path component"):
        tools["file_info"].invoke({"path": ".git/config"})

    with pytest.raises(ValueError, match="protected path component"):
        tools["mkdir"].invoke({"path": "node_modules/cache"})


def test_file_management_rejects_unsafe_destinations(tmp_path: Path) -> None:
    tools = _tools(tmp_path)
    (tmp_path / "source.txt").write_text("source", encoding="utf-8")
    (tmp_path / "dest.txt").write_text("dest", encoding="utf-8")

    with pytest.raises(ValueError, match="Destination parent does not exist"):
        tools["move_file"].invoke(
            {"source": "source.txt", "destination": "missing/dest.txt"}
        )

    with pytest.raises(ValueError, match="Destination already exists"):
        tools["copy_file"].invoke(
            {"source": "source.txt", "destination": "dest.txt"}
        )

    result = _json(
        tools["copy_file"].invoke(
            {
                "source": "source.txt",
                "destination": "dest.txt",
                "overwrite": True,
            }
        )
    )
    assert result["overwritten"] is True
    assert (tmp_path / "dest.txt").read_text(encoding="utf-8") == "source"


def test_file_management_refuses_directory_delete_and_symlink_copy(
    tmp_path: Path,
) -> None:
    tools = _tools(tmp_path)
    (tmp_path / "dir").mkdir()
    (tmp_path / "target.txt").write_text("target", encoding="utf-8")
    (tmp_path / "link.txt").symlink_to(tmp_path / "target.txt")

    with pytest.raises(ValueError, match="not a regular file"):
        tools["delete_file"].invoke({"path": "dir"})

    with pytest.raises(ValueError, match="does not copy symlinks"):
        tools["copy_file"].invoke(
            {"source": "link.txt", "destination": "link-copy.txt"}
        )
