from __future__ import annotations

from types import SimpleNamespace

from invincat_cli.widgets import _links


class DummyEvent:
    def __init__(self, link: str | None, app: object | None = None) -> None:
        self.style = SimpleNamespace(link=link)
        self.app = app
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class DummyApp:
    def __init__(self) -> None:
        self.notifications: list[tuple[str, dict[str, object]]] = []

    def notify(self, message: str, **kwargs: object) -> None:
        self.notifications.append((message, kwargs))


def test_open_style_link_opens_safe_url_and_stops_event(monkeypatch) -> None:
    opened: list[str] = []
    event = DummyEvent("https://example.com")

    monkeypatch.setattr(_links.webbrowser, "open", opened.append)

    _links.open_style_link(event)

    assert opened == ["https://example.com"]
    assert event.stopped is True


def test_open_style_link_ignores_missing_url(monkeypatch) -> None:
    opened: list[str] = []
    event = DummyEvent(None)

    monkeypatch.setattr(_links.webbrowser, "open", opened.append)

    _links.open_style_link(event)

    assert opened == []
    assert event.stopped is False


def test_open_style_link_blocks_unsafe_url_and_notifies(monkeypatch) -> None:
    app = DummyApp()
    event = DummyEvent("https://evil.example/\u202etxt", app=app)
    opened: list[str] = []

    monkeypatch.setattr(_links.webbrowser, "open", opened.append)
    monkeypatch.setattr(
        _links,
        "check_url_safety",
        lambda _url: SimpleNamespace(safe=False, warnings=["hidden text"]),
    )
    monkeypatch.setattr(_links, "strip_dangerous_unicode", lambda url: url)

    _links.open_style_link(event)

    assert opened == []
    assert event.stopped is False
    assert app.notifications == [
        (
            "Blocked suspicious URL: https://evil.example/\u202etxt\nhidden text",
            {"severity": "warning", "markup": False},
        )
    ]


def test_open_style_link_tolerates_notification_failure(monkeypatch) -> None:
    class FailingApp:
        def notify(self, *_args: object, **_kwargs: object) -> None:
            raise TypeError("notify failed")

    event = DummyEvent("https://evil.example", app=FailingApp())
    monkeypatch.setattr(
        _links,
        "check_url_safety",
        lambda _url: SimpleNamespace(safe=False, warnings=[]),
    )

    _links.open_style_link(event)

    assert event.stopped is False


def test_open_style_link_leaves_event_open_when_browser_fails(monkeypatch) -> None:
    def fail(_url: str) -> None:
        raise RuntimeError("browser unavailable")

    event = DummyEvent("https://example.com")
    monkeypatch.setattr(_links.webbrowser, "open", fail)

    _links.open_style_link(event)

    assert event.stopped is False
