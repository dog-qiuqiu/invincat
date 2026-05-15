"""Tests for app service wiring and construction boundaries."""

from __future__ import annotations

import invincat_cli.scheduler.store as scheduler_store
from invincat_cli.app import DeepAgentsApp
from invincat_cli.app_runtime.services import (
    AppServices,
    LazyService,
    _default_scheduler_store,
)


def test_lazy_service_initializes_once_on_first_attribute_access() -> None:
    class Service:
        value = "ready"

    calls = 0

    def _factory() -> Service:
        nonlocal calls
        calls += 1
        return Service()

    service = LazyService(_factory)

    assert calls == 0
    assert service.value == "ready"
    assert service.instance.value == "ready"
    assert calls == 1


def test_app_services_returns_lazy_scheduler_store_proxy() -> None:
    store = object()
    services = AppServices(scheduler_store_factory=lambda: store)

    lazy_store = services.lazy_scheduler_store()

    assert isinstance(lazy_store, LazyService)
    assert lazy_store.instance is store


def test_default_scheduler_store_constructs_scheduler_store(monkeypatch) -> None:
    created = object()

    monkeypatch.setattr(scheduler_store, "SchedulerStore", lambda: created)

    assert _default_scheduler_store() is created


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
