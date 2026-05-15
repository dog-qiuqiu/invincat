"""File discovery and fuzzy ranking helpers for autocomplete."""

from __future__ import annotations

import shutil

# S404: subprocess is required for git ls-files to get project file list
import subprocess  # noqa: S404
from collections.abc import Callable
from difflib import SequenceMatcher
from pathlib import Path

_MAX_FALLBACK_FILES = 1000
"""Hard cap on files returned by the non-git glob fallback."""

_MIN_FUZZY_SCORE = 15
"""Minimum score to include in file-completion results."""

_MIN_FUZZY_RATIO = 0.4
"""SequenceMatcher threshold for filename-only fuzzy matches."""


def _get_git_executable() -> str | None:
    """Get full path to git executable using shutil.which().

    Returns:
        Full path to git executable, or None if not found.
    """
    return shutil.which("git")


def _get_project_files(
    root: Path,
    *,
    get_git_executable: Callable[[], str | None] = _get_git_executable,
    max_fallback_files: int = _MAX_FALLBACK_FILES,
    run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> list[str]:
    """Get project files using git ls-files or fallback to glob.

    Returns:
        List of relative file paths from project root.
    """
    git_path = get_git_executable()
    if git_path:
        try:
            # S603: git_path is validated via shutil.which(), args are hardcoded
            result = run_command(  # noqa: S603
                [git_path, "ls-files"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode == 0:
                files = result.stdout.strip().split("\n")
                return [f for f in files if f]  # Filter empty strings
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            pass

    # Fallback: simple glob (limited depth to avoid slowness)
    files = []
    try:
        for pattern in ["*", "*/*", "*/*/*", "*/*/*/*"]:
            for p in root.glob(pattern):
                if p.is_file() and not any(part.startswith(".") for part in p.parts):
                    files.append(p.relative_to(root).as_posix())
                if len(files) >= max_fallback_files:
                    break
            if len(files) >= max_fallback_files:
                break
    except OSError:
        pass
    return files


def _fuzzy_score(
    query: str,
    candidate: str,
    *,
    min_fuzzy_ratio: float = _MIN_FUZZY_RATIO,
) -> float:
    """Score a candidate against query. Higher = better match.

    Returns:
        Score value where higher indicates better match quality.
    """
    query_lower = query.lower()
    # Normalize path separators for cross-platform support
    candidate_normalized = candidate.replace("\\", "/")
    candidate_lower = candidate_normalized.lower()

    # Extract filename for matching (prioritize filename over full path)
    filename = candidate_normalized.rsplit("/", 1)[-1].lower()
    filename_start = candidate_lower.rfind("/") + 1

    # Check filename first (higher priority)
    if query_lower in filename:
        idx = filename.find(query_lower)
        # Bonus for being at start of filename
        if idx == 0:
            return 150 + (1 / len(candidate))
        # Bonus for word boundary in filename
        if idx > 0 and filename[idx - 1] in "_-.":
            return 120 + (1 / len(candidate))
        return 100 + (1 / len(candidate))

    # Check full path
    if query_lower in candidate_lower:
        idx = candidate_lower.find(query_lower)
        # At start of filename
        if idx == filename_start:  # pragma: no cover - filename match is handled first
            return 80 + (1 / len(candidate))
        # At word boundary in path
        if idx == 0 or candidate[idx - 1] in "/_-.":
            return 60 + (1 / len(candidate))
        return 40 + (1 / len(candidate))

    # Fuzzy match on filename only (more relevant)
    filename_ratio = SequenceMatcher(None, query_lower, filename).ratio()
    if filename_ratio > min_fuzzy_ratio:
        return filename_ratio * 30

    # Fallback: fuzzy on full path
    ratio = SequenceMatcher(None, query_lower, candidate_lower).ratio()
    return ratio * 15


def _is_dotpath(path: str) -> bool:
    """Check if path contains dotfiles/dotdirs (e.g., .github/...).

    Returns:
        True if path contains hidden directories or files.
    """
    return any(part.startswith(".") for part in path.split("/"))


def _path_depth(path: str) -> int:
    """Get depth of path (number of / separators).

    Returns:
        Number of path separators in the path.
    """
    return path.count("/")


def _fuzzy_search(
    query: str,
    candidates: list[str],
    limit: int = 10,
    *,
    include_dotfiles: bool = False,
    min_fuzzy_score: int = _MIN_FUZZY_SCORE,
    fuzzy_score: Callable[[str, str], float] = _fuzzy_score,
) -> list[str]:
    """Return top matches sorted by score.

    Args:
        query: Search query
        candidates: List of file paths to search
        limit: Max results to return
        include_dotfiles: Whether to include dotfiles (default False)

    Returns:
        List of matching file paths sorted by relevance score.
    """
    # Filter dotfiles unless explicitly searching for them
    filtered = (
        candidates
        if include_dotfiles
        else [c for c in candidates if not _is_dotpath(c)]
    )

    if not query:
        # Empty query: show root-level files first, sorted by depth then name
        sorted_files = sorted(filtered, key=lambda p: (_path_depth(p), p.lower()))
        return sorted_files[:limit]

    scored = [
        (score, c)
        for c in filtered
        if (score := fuzzy_score(query, c)) >= min_fuzzy_score
    ]
    scored.sort(key=lambda x: -x[0])
    return [c for _, c in scored[:limit]]
