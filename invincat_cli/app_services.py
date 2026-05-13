"""Runtime service wiring for the Textual app.

The app owns UI orchestration, but several services have filesystem or process
side effects when constructed. Keeping those behind factories makes tests and
alternate runtimes inject isolated implementations without changing UI logic.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


class LazyService:
    """Initialize a service only when one of its attributes is used."""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self._factory = factory
        self._instance: Any | None = None

    @property
    def instance(self) -> Any:
        """Return the concrete service, constructing it on first use."""
        if self._instance is None:
            self._instance = self._factory()
        return self._instance

    def __getattr__(self, name: str) -> Any:
        """Forward unknown attributes to the concrete service."""
        return getattr(self.instance, name)


def _default_scheduler_store() -> Any:
    from invincat_cli.scheduler.store import SchedulerStore

    return SchedulerStore()


@dataclass(slots=True)
class AppServices:
    """Factories and service instances used by `DeepAgentsApp`."""

    scheduler_store_factory: Callable[[], Any] = _default_scheduler_store

    def lazy_scheduler_store(self) -> LazyService:
        """Return a lazy scheduler store proxy."""
        return LazyService(self.scheduler_store_factory)
