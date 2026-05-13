"""Tests for app service wiring and construction boundaries."""

from __future__ import annotations

from invincat_cli.app import DeepAgentsApp
from invincat_cli.app_runtime.services import AppServices


def test_app_construction_does_not_initialize_scheduler_store() -> None:
    initialized = False

    def _factory() -> object:
        nonlocal initialized
        initialized = True
        raise AssertionError("scheduler store should be lazy")

    DeepAgentsApp(
        agent=None,
        assistant_id="agent",
        backend=None,
        services=AppServices(scheduler_store_factory=_factory),
    )

    assert initialized is False
