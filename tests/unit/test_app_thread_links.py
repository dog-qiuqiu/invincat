"""Tests for thread link helpers."""

from __future__ import annotations

import asyncio

from textual.content import Content

import invincat_cli.config as config
from invincat_cli.app_runtime.thread_links import build_thread_message


def test_build_thread_message_links_thread_id() -> None:
    message = asyncio.run(
        build_thread_message(
            "Resumed thread",
            "thread-1",
            build_url=lambda thread_id: f"https://example.test/{thread_id}",
        )
    )

    assert isinstance(message, Content)
    assert message.plain == "Resumed thread: thread-1"


def test_build_thread_message_uses_default_url_builder(monkeypatch) -> None:
    monkeypatch.setattr(
        config,
        "build_langsmith_thread_url",
        lambda thread_id: f"https://example.test/default/{thread_id}",
    )

    message = asyncio.run(build_thread_message("Resumed thread", "thread-1"))

    assert isinstance(message, Content)
    assert message.plain == "Resumed thread: thread-1"


def test_build_thread_message_falls_back_when_url_missing() -> None:
    message = asyncio.run(
        build_thread_message(
            "Resumed thread",
            "thread-1",
            build_url=lambda _thread_id: None,
        )
    )

    assert message == "Resumed thread: thread-1"


def test_build_thread_message_falls_back_when_url_builder_fails() -> None:
    def _raise(_thread_id: str) -> str:
        raise RuntimeError("no tracing")

    message = asyncio.run(
        build_thread_message(
            "Resumed thread",
            "thread-1",
            build_url=_raise,
        )
    )

    assert message == "Resumed thread: thread-1"
