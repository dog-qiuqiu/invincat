"""Safe project-scoped file management tools."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.tools import tool

PROTECTED_PATH_PARTS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".env",
        ".venv",
        ".invincat",
        "env",
        "node_modules",
        "venv",
    }
)
TRASH_DIR = ".invincat/trash"


class FileManagementMiddleware(AgentMiddleware):
    """Expose safe project-scoped file management tools."""

    def __init__(self, *, allowed_root: str | Path) -> None:
        super().__init__()
        self._allowed_root = Path(allowed_root).expanduser().resolve()

        @tool(description=_FILE_INFO_DESCRIPTION)
        def file_info(path: str) -> str:
            """Return metadata for a project path."""
            target = self._resolve_user_path(path, must_exist=False)
            return _json(self._build_info(target))

        @tool(description=_MKDIR_DESCRIPTION)
        def mkdir(
            path: str,
            parents: bool = True,
            exist_ok: bool = True,
        ) -> str:
            """Create a directory inside the current project."""
            target = self._resolve_user_path(path, must_exist=False)
            if target.exists() and not target.is_dir():
                raise ValueError(f"Path exists and is not a directory: {path}")
            target.mkdir(parents=parents, exist_ok=exist_ok)
            return _json(
                {
                    "ok": True,
                    "action": "mkdir",
                    "path": self._relative(target),
                }
            )

        @tool(description=_MOVE_FILE_DESCRIPTION)
        def move_file(
            source: Annotated[str, "Existing project file to move or rename."],
            destination: Annotated[str, "Destination project file path."],
            overwrite: bool = False,
        ) -> str:
            """Move or rename a file inside the current project."""
            src = self._resolve_existing_file(source)
            dst = self._resolve_destination(destination, overwrite=overwrite)
            shutil.move(str(src), str(dst))
            return _json(
                {
                    "ok": True,
                    "action": "move_file",
                    "source": self._relative(src),
                    "destination": self._relative(dst),
                    "overwritten": overwrite,
                }
            )

        @tool(description=_COPY_FILE_DESCRIPTION)
        def copy_file(
            source: Annotated[str, "Existing project file to copy."],
            destination: Annotated[str, "Destination project file path."],
            overwrite: bool = False,
        ) -> str:
            """Copy a file inside the current project."""
            src = self._resolve_existing_file(source)
            if src.is_symlink():
                raise ValueError("copy_file does not copy symlinks")
            dst = self._resolve_destination(destination, overwrite=overwrite)
            shutil.copy2(src, dst)
            return _json(
                {
                    "ok": True,
                    "action": "copy_file",
                    "source": self._relative(src),
                    "destination": self._relative(dst),
                    "overwritten": overwrite,
                }
            )

        @tool(description=_DELETE_FILE_DESCRIPTION)
        def delete_file(path: str) -> str:
            """Move a project file to the project trash directory."""
            src = self._resolve_existing_file(path)
            trash_dir = self._resolve_internal_path(TRASH_DIR)
            trash_dir.mkdir(parents=True, exist_ok=True)
            deleted_at = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            dst = trash_dir / f"{deleted_at}-{src.name}"
            counter = 1
            while dst.exists():
                dst = trash_dir / f"{deleted_at}-{counter}-{src.name}"
                counter += 1
            shutil.move(str(src), str(dst))
            return _json(
                {
                    "ok": True,
                    "action": "delete_file",
                    "path": path,
                    "trashed_to": self._relative(dst),
                    "permanent": False,
                }
            )

        self.tools = [file_info, mkdir, move_file, copy_file, delete_file]

    def _resolve_user_path(self, path: str, *, must_exist: bool) -> Path:
        if not path or not path.strip():
            raise ValueError("Path must not be empty")
        raw = Path(path).expanduser()
        candidate = raw if raw.is_absolute() else self._allowed_root / raw
        resolved = candidate.resolve(strict=must_exist)
        self._assert_inside_root(resolved)
        self._assert_not_protected(resolved)
        actual = candidate.absolute()
        if actual.is_symlink():
            self._assert_inside_root(actual)
            self._assert_not_protected(actual)
            return actual
        return resolved

    def _resolve_internal_path(self, path: str) -> Path:
        resolved = (self._allowed_root / path).resolve(strict=False)
        self._assert_inside_root(resolved)
        return resolved

    def _resolve_existing_file(self, path: str) -> Path:
        resolved = self._resolve_user_path(path, must_exist=True)
        if not resolved.is_file() and not resolved.is_symlink():
            raise ValueError(f"Path is not a regular file: {path}")
        return resolved

    def _resolve_destination(self, path: str, *, overwrite: bool) -> Path:
        resolved = self._resolve_user_path(path, must_exist=False)
        parent = resolved.parent
        if not parent.exists():
            raise ValueError(f"Destination parent does not exist: {self._relative(parent)}")
        if not parent.is_dir():
            raise ValueError(f"Destination parent is not a directory: {self._relative(parent)}")
        if resolved.exists():
            if not overwrite:
                raise ValueError(f"Destination already exists: {self._relative(resolved)}")
            if not resolved.is_file() and not resolved.is_symlink():
                raise ValueError("Can only overwrite a regular file or symlink")
        return resolved

    def _assert_inside_root(self, path: Path) -> None:
        try:
            path.relative_to(self._allowed_root)
        except ValueError as exc:
            raise ValueError(
                f"Path must stay inside project root: {self._allowed_root}"
            ) from exc

    def _assert_not_protected(self, path: Path) -> None:
        relative_parts = path.relative_to(self._allowed_root).parts
        for part in relative_parts:
            if part in PROTECTED_PATH_PARTS:
                raise ValueError(f"Refusing to manage protected path component: {part}")

    def _relative(self, path: Path) -> str:
        return str(path.relative_to(self._allowed_root))

    def _build_info(self, path: Path) -> dict[str, Any]:
        exists = path.exists() or path.is_symlink()
        info: dict[str, Any] = {
            "path": self._relative(path),
            "exists": exists,
        }
        if not exists:
            return info

        stat = path.lstat()
        if path.is_symlink():
            kind = "symlink"
        elif path.is_dir():
            kind = "directory"
        elif path.is_file():
            kind = "file"
        else:
            kind = "other"
        info.update(
            {
                "type": kind,
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=UTC
                ).isoformat(),
            }
        )
        if path.is_symlink():
            info["target"] = str(path.readlink())
        return info


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


_FILE_INFO_DESCRIPTION = """Return metadata for a file or directory inside the current project.

This is read-only. It reports whether the path exists and, when it does, its
type, size, modified time, and symlink target.
"""

_MKDIR_DESCRIPTION = """Create a directory inside the current project.

Use this instead of shell commands such as `mkdir` when creating project
directories. Protected directories such as `.git`, `.env`, `.venv`,
`node_modules`, and `.invincat` are rejected.
"""

_MOVE_FILE_DESCRIPTION = """Move or rename a regular file inside the current project.

Use this instead of shell commands such as `mv` for project file organization.
The source and destination must stay inside the project root. Destination
parents must already exist; create them first with `mkdir` when needed.
"""

_COPY_FILE_DESCRIPTION = """Copy a regular file inside the current project.

Use this instead of shell commands such as `cp` for project file organization.
The source and destination must stay inside the project root. Destination
parents must already exist; create them first with `mkdir` when needed.
"""

_DELETE_FILE_DESCRIPTION = """Safely delete a regular file inside the current project.

This tool does not permanently remove files. It moves the file into
`.invincat/trash` so the user can recover it if needed. Protected paths and
directories are rejected.
"""
