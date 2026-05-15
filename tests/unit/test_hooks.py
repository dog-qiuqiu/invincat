"""Tests for external hook loading and dispatch."""

from __future__ import annotations

import asyncio
import json
import subprocess

from invincat_cli import hooks


def test_load_hooks_returns_empty_when_config_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("invincat_cli.model_config.DEFAULT_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hooks, "_hooks_config", None)

    assert hooks._load_hooks() == []
    assert hooks._load_hooks() == []


def test_load_hooks_reads_valid_config(monkeypatch, tmp_path) -> None:
    config = {
        "hooks": [
            {"command": ["echo", "ok"], "events": ["session.start"]},
        ]
    }
    (tmp_path / "hooks.json").write_text(json.dumps(config))
    monkeypatch.setattr("invincat_cli.model_config.DEFAULT_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hooks, "_hooks_config", None)

    assert hooks._load_hooks() == config["hooks"]


def test_load_hooks_ignores_malformed_config(monkeypatch, tmp_path) -> None:
    (tmp_path / "hooks.json").write_text("{bad json")
    monkeypatch.setattr("invincat_cli.model_config.DEFAULT_CONFIG_DIR", tmp_path)
    monkeypatch.setattr(hooks, "_hooks_config", None)

    assert hooks._load_hooks() == []


def test_load_hooks_rejects_non_object_or_non_list_hooks(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("invincat_cli.model_config.DEFAULT_CONFIG_DIR", tmp_path)

    (tmp_path / "hooks.json").write_text("[]")
    monkeypatch.setattr(hooks, "_hooks_config", None)

    assert hooks._load_hooks() == []

    (tmp_path / "hooks.json").write_text(json.dumps({"hooks": "bad"}))
    monkeypatch.setattr(hooks, "_hooks_config", None)

    assert hooks._load_hooks() == []


def test_run_single_hook_handles_success_and_failures(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def run_success(*_args: object, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(hooks.subprocess, "run", run_success)

    hooks._run_single_hook(["echo", "ok"], "session.start", b"{}")

    assert calls[0]["input"] == b"{}"
    assert calls[0]["start_new_session"] is True
    assert calls[0]["timeout"] == 5

    def timeout(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired("hook", 5)

    monkeypatch.setattr(hooks.subprocess, "run", timeout)
    hooks._run_single_hook(["sleep"], "session.start", b"{}")

    def missing(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("missing")

    monkeypatch.setattr(hooks.subprocess, "run", missing)
    hooks._run_single_hook(["missing"], "session.start", b"{}")

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(hooks.subprocess, "run", unexpected)
    hooks._run_single_hook(["bad"], "session.start", b"{}")


def test_dispatch_hook_sync_filters_events_and_invalid_commands(monkeypatch) -> None:
    ran: list[tuple[list[str], str, bytes]] = []
    monkeypatch.setattr(
        hooks,
        "_run_single_hook",
        lambda command, event, payload: ran.append((command, event, payload)),
    )
    payload = b"{}"

    hooks._dispatch_hook_sync(
        "session.start",
        payload,
        [
            {"command": ["all-events"]},
            {"command": ["matching"], "events": ["session.start"]},
            {"command": ["other"], "events": ["session.end"]},
            {"command": [], "events": ["session.start"]},
            {"command": "not-a-list", "events": ["session.start"]},
        ],
    )

    assert ran == [
        (["all-events"], "session.start", payload),
        (["matching"], "session.start", payload),
    ]


def test_dispatch_hook_sync_handles_no_match_single_and_multiple(monkeypatch) -> None:
    ran: list[list[str]] = []
    monkeypatch.setattr(
        hooks,
        "_run_single_hook",
        lambda command, _event, _payload: ran.append(command),
    )

    hooks._dispatch_hook_sync(
        "session.start",
        b"{}",
        [{"command": ["other"], "events": ["session.end"]}],
    )

    assert ran == []

    hooks._dispatch_hook_sync("session.start", b"{}", [{"command": ["one"]}])

    assert ran == [["one"]]

    hooks._dispatch_hook_sync(
        "session.start",
        b"{}",
        [{"command": ["two"]}, {"command": ["three"]}],
    )

    assert ran == [["one"], ["two"], ["three"]]


def test_dispatch_hook_serializes_event_and_payload(monkeypatch) -> None:
    dispatched: list[tuple[str, bytes, list[dict]]] = []
    monkeypatch.setattr(hooks, "_load_hooks", lambda: [{"command": ["echo"]}])
    monkeypatch.setattr(
        hooks,
        "_dispatch_hook_sync",
        lambda event, payload, config: dispatched.append((event, payload, config)),
    )

    asyncio.run(hooks.dispatch_hook("session.start", {"thread_id": "t1"}))

    event, payload, config = dispatched[0]
    assert event == "session.start"
    assert json.loads(payload) == {"event": "session.start", "thread_id": "t1"}
    assert config == [{"command": ["echo"]}]


def test_dispatch_hook_returns_without_hooks_and_swallows_errors(monkeypatch) -> None:
    async def fail_to_thread(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("thread failed")

    monkeypatch.setattr(hooks, "_load_hooks", lambda: [])
    asyncio.run(hooks.dispatch_hook("session.start", {}))

    monkeypatch.setattr(hooks, "_load_hooks", lambda: [{"command": ["echo"]}])
    monkeypatch.setattr(hooks.asyncio, "to_thread", fail_to_thread)
    asyncio.run(hooks.dispatch_hook("session.start", {}))


def test_dispatch_hook_fire_and_forget_tracks_background_task(monkeypatch) -> None:
    async def _fake_dispatch(_event: str, _payload: dict) -> None:
        await asyncio.sleep(0)

    async def _run() -> None:
        monkeypatch.setattr(hooks, "dispatch_hook", _fake_dispatch)
        hooks._background_tasks.clear()

        hooks.dispatch_hook_fire_and_forget("session.start", {})
        assert len(hooks._background_tasks) == 1
        task = next(iter(hooks._background_tasks))
        await task
        await asyncio.sleep(0)
        assert hooks._background_tasks == set()

    asyncio.run(_run())


def test_dispatch_hook_fire_and_forget_skips_without_running_loop() -> None:
    hooks.dispatch_hook_fire_and_forget("session.start", {})
