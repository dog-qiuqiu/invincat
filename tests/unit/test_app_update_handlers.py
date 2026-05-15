from __future__ import annotations

import asyncio
from types import SimpleNamespace

from invincat_cli import update_check
from invincat_cli.app_runtime import update_handlers
from invincat_cli.widgets.messages import AppMessage, ErrorMessage, UserMessage


class DummyApp:
    def __init__(self) -> None:
        self.messages: list[object] = []
        self.notifications: list[tuple[str, dict[str, object]]] = []
        self._update_available = (False, None)

    async def _mount_message(self, message: object) -> None:
        self.messages.append(message)

    def notify(self, message: str, **kwargs: object) -> None:
        self.notifications.append((message, kwargs))


def message_contents(app: DummyApp) -> list[object]:
    return [getattr(message, "_content", None) for message in app.messages]


def test_handle_update_command_reports_up_to_date(monkeypatch) -> None:
    app = DummyApp()
    monkeypatch.setattr(
        update_check,
        "is_update_available",
        lambda **_kwargs: (False, None),
    )

    asyncio.run(update_handlers.handle_update_command(app))

    assert [type(message) for message in app.messages] == [
        UserMessage,
        AppMessage,
        AppMessage,
    ]
    assert message_contents(app)[0] == "/update"


def test_handle_update_command_records_successful_upgrade(monkeypatch) -> None:
    app = DummyApp()

    async def perform_upgrade() -> tuple[bool, str]:
        return (True, "updated")

    monkeypatch.setattr(
        update_check,
        "is_update_available",
        lambda **_kwargs: (True, "9.9.9"),
    )
    monkeypatch.setattr(update_check, "perform_upgrade", perform_upgrade)

    asyncio.run(update_handlers.handle_update_command(app))

    assert app._update_available == (False, None)
    assert isinstance(app.messages[-1], AppMessage)


def test_handle_update_command_reports_upgrade_failure(monkeypatch) -> None:
    app = DummyApp()

    async def perform_upgrade() -> tuple[bool, str]:
        return (False, "failure detail" * 30)

    monkeypatch.setattr(
        update_check,
        "is_update_available",
        lambda **_kwargs: (True, "9.9.9"),
    )
    monkeypatch.setattr(update_check, "perform_upgrade", perform_upgrade)
    monkeypatch.setattr(update_check, "upgrade_command", lambda: "upgrade now")

    asyncio.run(update_handlers.handle_update_command(app))

    assert isinstance(app.messages[-1], AppMessage)
    assert "upgrade now" in str(message_contents(app)[-1])


def test_handle_update_command_mounts_error_message_on_exception(monkeypatch) -> None:
    app = DummyApp()

    def fail(**_kwargs):
        raise RuntimeError("network failed")

    monkeypatch.setattr(update_check, "is_update_available", fail)

    asyncio.run(update_handlers.handle_update_command(app))

    assert isinstance(app.messages[-1], ErrorMessage)
    assert "RuntimeError: network failed" in str(message_contents(app)[-1])


def test_check_for_updates_notifies_when_manual_update_available(monkeypatch) -> None:
    app = DummyApp()
    monkeypatch.setattr(
        update_check,
        "is_update_available",
        lambda: (True, "9.9.9"),
    )
    monkeypatch.setattr(update_check, "is_auto_update_enabled", lambda: False)
    monkeypatch.setattr(update_check, "upgrade_command", lambda: "upgrade now")

    asyncio.run(update_handlers.check_for_updates(app))

    assert app._update_available == (True, "9.9.9")
    assert app.notifications[-1][1]["severity"] == "information"


def test_check_for_updates_returns_quietly_when_no_update(monkeypatch) -> None:
    app = DummyApp()
    monkeypatch.setattr(update_check, "is_update_available", lambda: (False, None))

    asyncio.run(update_handlers.check_for_updates(app))

    assert app._update_available == (False, None)
    assert app.notifications == []


def test_check_for_updates_returns_quietly_when_background_check_fails(
    monkeypatch,
) -> None:
    app = DummyApp()

    def fail() -> tuple[bool, str | None]:
        raise RuntimeError("network failed")

    monkeypatch.setattr(update_check, "is_update_available", fail)

    asyncio.run(update_handlers.check_for_updates(app))

    assert app._update_available == (False, None)
    assert app.notifications == []


def test_check_for_updates_runs_auto_update_success(monkeypatch) -> None:
    app = DummyApp()

    async def perform_upgrade() -> tuple[bool, str]:
        return (True, "updated")

    monkeypatch.setattr(update_check, "is_update_available", lambda: (True, "9.9.9"))
    monkeypatch.setattr(update_check, "is_auto_update_enabled", lambda: True)
    monkeypatch.setattr(update_check, "perform_upgrade", perform_upgrade)

    asyncio.run(update_handlers.check_for_updates(app))

    assert [notice[1]["severity"] for notice in app.notifications] == [
        "information",
        "information",
    ]


def test_check_for_updates_notifies_on_auto_update_failure(monkeypatch) -> None:
    app = DummyApp()

    async def perform_upgrade() -> tuple[bool, str]:
        return (False, "failed")

    monkeypatch.setattr(update_check, "is_update_available", lambda: (True, "9.9.9"))
    monkeypatch.setattr(update_check, "is_auto_update_enabled", lambda: True)
    monkeypatch.setattr(update_check, "perform_upgrade", perform_upgrade)
    monkeypatch.setattr(update_check, "upgrade_command", lambda: "upgrade now")

    asyncio.run(update_handlers.check_for_updates(app))

    assert app.notifications[-1][1]["severity"] == "warning"


def test_check_for_updates_notifies_when_auto_update_branch_raises(monkeypatch) -> None:
    app = DummyApp()
    monkeypatch.setattr(update_check, "is_update_available", lambda: (True, "9.9.9"))

    def fail_enabled() -> bool:
        raise RuntimeError("config failed")

    monkeypatch.setattr(update_check, "is_auto_update_enabled", fail_enabled)

    asyncio.run(update_handlers.check_for_updates(app))

    assert app._update_available == (True, "9.9.9")
    assert app.notifications[-1][1]["severity"] == "warning"


def test_show_whats_new_mounts_banner_and_marks_seen(monkeypatch) -> None:
    app = DummyApp()
    seen: list[str] = []
    monkeypatch.setattr(update_check, "should_show_whats_new", lambda: True)
    monkeypatch.setattr(update_check, "mark_version_seen", seen.append)

    asyncio.run(update_handlers.show_whats_new(app))

    assert isinstance(app.messages[-1], AppMessage)
    assert seen


def test_show_whats_new_is_quiet_when_not_needed(monkeypatch) -> None:
    app = DummyApp()
    monkeypatch.setattr(update_check, "should_show_whats_new", lambda: False)

    asyncio.run(update_handlers.show_whats_new(app))

    assert app.messages == []


def test_show_whats_new_is_quiet_when_check_fails(monkeypatch) -> None:
    app = DummyApp()

    def fail() -> bool:
        raise RuntimeError("check failed")

    monkeypatch.setattr(update_check, "should_show_whats_new", fail)

    asyncio.run(update_handlers.show_whats_new(app))

    assert app.messages == []


def test_show_whats_new_is_quiet_when_banner_mount_fails(monkeypatch) -> None:
    class BrokenApp(DummyApp):
        async def _mount_message(self, message: object) -> None:
            raise RuntimeError("mount failed")

    app = BrokenApp()
    monkeypatch.setattr(update_check, "should_show_whats_new", lambda: True)

    asyncio.run(update_handlers.show_whats_new(app))

    assert app.messages == []


def test_show_whats_new_ignores_seen_marker_failure(monkeypatch) -> None:
    app = DummyApp()
    monkeypatch.setattr(update_check, "should_show_whats_new", lambda: True)

    def fail_seen(_version: str) -> None:
        raise RuntimeError("write failed")

    monkeypatch.setattr(update_check, "mark_version_seen", fail_seen)

    asyncio.run(update_handlers.show_whats_new(app))

    assert isinstance(app.messages[-1], AppMessage)


def test_handle_auto_update_toggle_flips_preference(monkeypatch) -> None:
    app = DummyApp()
    state = SimpleNamespace(enabled=False)

    monkeypatch.setattr("invincat_cli.config._is_editable_install", lambda: False)
    monkeypatch.setattr(update_check, "is_auto_update_enabled", lambda: state.enabled)
    monkeypatch.setattr(
        update_check,
        "set_auto_update",
        lambda enabled: setattr(state, "enabled", enabled),
    )

    asyncio.run(update_handlers.handle_auto_update_toggle(app))

    assert state.enabled is True
    assert app.notifications[-1][1]["severity"] == "information"


def test_handle_auto_update_toggle_warns_for_editable_install(monkeypatch) -> None:
    app = DummyApp()
    monkeypatch.setattr("invincat_cli.config._is_editable_install", lambda: True)

    asyncio.run(update_handlers.handle_auto_update_toggle(app))

    assert app.notifications[-1][1]["severity"] == "warning"


def test_handle_auto_update_toggle_notifies_on_exception(monkeypatch) -> None:
    app = DummyApp()

    def fail() -> bool:
        raise RuntimeError("config failed")

    monkeypatch.setattr("invincat_cli.config._is_editable_install", fail)

    asyncio.run(update_handlers.handle_auto_update_toggle(app))

    assert app.notifications[-1][1]["severity"] == "warning"
    assert "RuntimeError" in app.notifications[-1][0]
