"""Thread-scoped durable storage for goal mode."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from invincat_cli.goal_mode.models import GoalState
from invincat_cli.project_utils import ProjectContext

logger = logging.getLogger(__name__)


def resolve_goal_store_dir(cwd: str | Path) -> Path:
    """Resolve the project-local goal store directory for a user cwd."""
    context = ProjectContext.from_user_cwd(cwd)
    root = context.project_root or context.user_cwd
    return root / ".invincat" / "goals"


class GoalStore:
    """Small JSON-file store keyed by thread id."""

    def __init__(self, root_dir: str | Path) -> None:
        self.root_dir = Path(root_dir).expanduser().resolve()

    @classmethod
    def from_cwd(cls, cwd: str | Path) -> GoalStore:
        return cls(resolve_goal_store_dir(cwd))

    def path_for_thread(self, thread_id: str) -> Path:
        safe_thread_id = "".join(
            ch if ch.isalnum() or ch in {"-", "_", "."} else "_"
            for ch in thread_id.strip()
        )
        return self.root_dir / f"{safe_thread_id or 'thread'}.json"

    def load(self, thread_id: str) -> GoalState | None:
        path = self.path_for_thread(thread_id)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            goal = GoalState.from_dict(raw)
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning("Failed to load goal state from %s", path, exc_info=True)
            return None
        if goal.thread_id != thread_id:
            logger.warning("Ignoring goal state with mismatched thread id: %s", path)
            return None
        return goal

    def save(self, goal: GoalState) -> Path:
        path = self.path_for_thread(goal.thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(goal.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def delete(self, thread_id: str) -> bool:
        path = self.path_for_thread(thread_id)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        except OSError:
            logger.warning("Failed to delete goal state at %s", path, exc_info=True)
            return False
        return True
