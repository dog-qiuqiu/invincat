"""Tests for update checking and update preference helpers."""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import time
from types import SimpleNamespace

import pytest

from invincat_cli import update_check
from invincat_cli.core.env_vars import AUTO_UPDATE, NO_UPDATE_CHECK


def test_latest_from_releases_filters_empty_invalid_and_prerelease_entries() -> None:
    releases = {
        "0.9.0": [{}],
        "1.0.0a1": [{}],
        "not-a-version": [{}],
        "1.0.0": [],
        "1.1.0": [{}],
    }

    assert (
        update_check._latest_from_releases(releases, include_prereleases=False)
        == "1.1.0"
    )
    assert (
        update_check._latest_from_releases(releases, include_prereleases=True)
        == "1.1.0"
    )


def test_get_latest_version_uses_fresh_cache(monkeypatch, tmp_path) -> None:
    cache_file = tmp_path / "latest.json"
    cache_file.write_text(
        json.dumps(
            {
                "version": "1.2.3",
                "version_prerelease": "1.3.0a1",
                "checked_at": time.time(),
            }
        )
    )
    monkeypatch.setattr(update_check, "CACHE_FILE", cache_file)

    assert update_check.get_latest_version() == "1.2.3"
    assert update_check.get_latest_version(include_prereleases=True) == "1.3.0a1"


def test_get_latest_version_fetches_and_caches_pypi_response(
    monkeypatch, tmp_path
) -> None:
    cache_file = tmp_path / "latest.json"
    monkeypatch.setattr(update_check, "CACHE_FILE", cache_file)

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "info": {"version": "1.2.3"},
                "releases": {"1.2.3": [{}], "1.3.0a1": [{}]},
            }

    fake_requests = SimpleNamespace(
        RequestException=Exception,
        get=lambda *_args, **_kwargs: Response(),
    )
    monkeypatch.setitem(sys.modules, "requests", fake_requests)

    assert update_check.get_latest_version(bypass_cache=True) == "1.2.3"
    assert json.loads(cache_file.read_text())["version_prerelease"] == "1.3.0a1"


def test_get_latest_version_handles_cache_fetch_and_write_failures(
    monkeypatch, tmp_path
) -> None:
    cache_file = tmp_path / "latest.json"
    cache_file.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr(update_check, "CACHE_FILE", cache_file)

    class Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"info": {"version": "1.2.3"}, "releases": {}}

    fake_requests = SimpleNamespace(
        RequestException=Exception,
        get=lambda *_args, **_kwargs: Response(),
    )
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    assert update_check.get_latest_version() == "1.2.3"

    fake_requests.get = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        Exception("network")
    )
    assert update_check.get_latest_version(bypass_cache=True) is None

    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("not a dir", encoding="utf-8")
    monkeypatch.setattr(update_check, "CACHE_FILE", blocked_parent / "latest.json")
    fake_requests.get = lambda *_args, **_kwargs: Response()
    assert update_check.get_latest_version(bypass_cache=True) == "1.2.3"


def test_get_latest_version_returns_none_when_requests_missing(monkeypatch) -> None:
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "requests":
            raise ImportError("missing")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    monkeypatch.delitem(sys.modules, "requests", raising=False)

    assert update_check.get_latest_version(bypass_cache=True) is None


def test_is_update_available_compares_versions(monkeypatch) -> None:
    monkeypatch.setattr(update_check, "__version__", "1.0.0")
    monkeypatch.setattr(update_check, "get_latest_version", lambda **_kwargs: "1.1.0")

    assert update_check.is_update_available() == (True, "1.1.0")

    monkeypatch.setattr(update_check, "get_latest_version", lambda **_kwargs: "0.9.0")

    assert update_check.is_update_available() == (False, None)

    monkeypatch.setattr(update_check, "get_latest_version", lambda **_kwargs: None)
    assert update_check.is_update_available() == (False, None)

    monkeypatch.setattr(update_check, "__version__", "not-a-version")
    assert update_check.is_update_available() == (False, None)

    monkeypatch.setattr(update_check, "__version__", "1.0.0")
    monkeypatch.setattr(update_check, "get_latest_version", lambda **_kwargs: "bad")
    assert update_check.is_update_available() == (False, None)


def test_detect_install_method_and_upgrade_command(monkeypatch) -> None:
    monkeypatch.setattr(update_check.sys, "prefix", "/Users/me/.local/share/uv/tools/x")

    assert update_check.detect_install_method() == "uv"
    monkeypatch.setattr(update_check.sys, "prefix", "/opt/homebrew/Cellar/x")
    assert update_check.detect_install_method() == "brew"
    monkeypatch.setattr(update_check.sys, "prefix", "/venv")
    monkeypatch.setattr("invincat_cli.config._is_editable_install", lambda: True)
    assert update_check.detect_install_method() == "unknown"
    monkeypatch.setattr("invincat_cli.config._is_editable_install", lambda: False)
    assert update_check.detect_install_method() == "pip"
    assert update_check.upgrade_command() == "pip install --upgrade invincat-cli"
    assert update_check.upgrade_command("brew") == "brew upgrade invincat-cli"
    assert (
        update_check.upgrade_command("unknown")
        == "pip install --upgrade invincat-cli"
    )


def test_perform_upgrade_handles_methods_and_process_results(monkeypatch) -> None:
    monkeypatch.setattr(update_check, "detect_install_method", lambda: "unknown")
    assert asyncio.run(update_check.perform_upgrade()) == (
        False,
        "Editable install detected — skipping auto-update.",
    )

    monkeypatch.setattr(update_check, "detect_install_method", lambda: "brew")
    monkeypatch.setattr(update_check.shutil, "which", lambda _name: None)
    assert asyncio.run(update_check.perform_upgrade()) == (
        False,
        "brew not found on PATH.",
    )

    class Proc:
        def __init__(self, returncode: int) -> None:
            self.returncode = returncode
            self.killed = False
            self.waited = False

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"out", b"err"

        def kill(self) -> None:
            self.killed = True

        async def wait(self) -> None:
            self.waited = True

    async def create_success(*_args, **_kwargs) -> Proc:
        return Proc(0)

    monkeypatch.setattr(update_check, "detect_install_method", lambda: "pip")
    monkeypatch.setattr(update_check.asyncio, "create_subprocess_shell", create_success)
    assert asyncio.run(update_check.perform_upgrade()) == (True, "outerr")

    async def create_failure(*_args, **_kwargs) -> Proc:
        return Proc(2)

    monkeypatch.setattr(update_check.asyncio, "create_subprocess_shell", create_failure)
    assert asyncio.run(update_check.perform_upgrade()) == (False, "outerr")

    timeout_proc = Proc(0)

    async def create_timeout(*_args, **_kwargs) -> Proc:
        return timeout_proc

    async def wait_timeout(awaitable, *_args, **_kwargs):
        awaitable.close()
        raise TimeoutError

    monkeypatch.setattr(update_check.asyncio, "create_subprocess_shell", create_timeout)
    monkeypatch.setattr(update_check.asyncio, "wait_for", wait_timeout)
    ok, message = asyncio.run(update_check.perform_upgrade())
    assert not ok
    assert "timed out" in message
    assert timeout_proc.killed
    assert timeout_proc.waited

    async def create_oserror(*_args, **_kwargs):
        raise OSError

    monkeypatch.setattr(update_check.asyncio, "create_subprocess_shell", create_oserror)
    assert asyncio.run(update_check.perform_upgrade()) == (
        False,
        "Failed to execute: pip install --upgrade invincat-cli",
    )


def test_perform_upgrade_reports_unknown_detected_method(monkeypatch) -> None:
    monkeypatch.setattr(update_check, "detect_install_method", lambda: "conda")

    assert asyncio.run(update_check.perform_upgrade()) == (
        False,
        "No upgrade command for install method: conda",
    )


def test_update_config_helpers_read_env_and_config(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[update]\ncheck = false\nauto_update = true\n")
    monkeypatch.setattr(update_check, "DEFAULT_CONFIG_PATH", config_path)
    monkeypatch.delenv(NO_UPDATE_CHECK, raising=False)
    monkeypatch.delenv(AUTO_UPDATE, raising=False)
    monkeypatch.setattr("invincat_cli.config._is_editable_install", lambda: False)

    assert not update_check.is_update_check_enabled()
    assert update_check.is_auto_update_enabled()

    monkeypatch.setenv(NO_UPDATE_CHECK, "1")
    assert not update_check.is_update_check_enabled()

    monkeypatch.setenv(AUTO_UPDATE, "yes")
    assert update_check.is_auto_update_enabled()

    monkeypatch.setattr("invincat_cli.config._is_editable_install", lambda: True)
    assert not update_check.is_auto_update_enabled()

    missing = tmp_path / "missing.toml"
    monkeypatch.setattr(update_check, "DEFAULT_CONFIG_PATH", missing)
    monkeypatch.delenv(NO_UPDATE_CHECK, raising=False)
    assert update_check.is_update_check_enabled()

    bad = tmp_path / "bad.toml"
    bad.write_text("[update", encoding="utf-8")
    monkeypatch.setattr(update_check, "DEFAULT_CONFIG_PATH", bad)
    assert update_check._read_update_config() == {}


def test_set_auto_update_persists_preference(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[other]\nvalue = true\n", encoding="utf-8")
    monkeypatch.setattr(update_check, "DEFAULT_CONFIG_PATH", config_path)

    update_check.set_auto_update(False)

    assert "auto_update = false" in config_path.read_text()
    assert "[other]" in config_path.read_text()


def test_set_auto_update_creates_missing_config(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(update_check, "DEFAULT_CONFIG_PATH", config_path)

    update_check.set_auto_update(True)

    assert "auto_update = true" in config_path.read_text()


def test_set_auto_update_removes_temp_file_on_replace_failure(
    monkeypatch, tmp_path
) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(update_check, "DEFAULT_CONFIG_PATH", config_path)
    real_replace = pathlib.Path.replace
    tmp_files_before = set(tmp_path.glob("*.tmp"))

    def fake_replace(self, target):
        if self.suffix == ".tmp":
            raise OSError("replace failed")
        return real_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", fake_replace)

    with pytest.raises(OSError):
        update_check.set_auto_update(True)

    assert set(tmp_path.glob("*.tmp")) == tmp_files_before


def test_seen_version_tracking(monkeypatch, tmp_path) -> None:
    seen_file = tmp_path / "seen.json"
    monkeypatch.setattr(update_check, "SEEN_VERSION_FILE", seen_file)
    monkeypatch.setattr(update_check, "__version__", "1.2.0")

    assert update_check.get_seen_version() is None
    assert not update_check.should_show_whats_new()
    assert update_check.get_seen_version() == "1.2.0"

    seen_file.write_text(json.dumps({"version": "1.1.0"}))
    assert update_check.should_show_whats_new()

    seen_file.write_text("{broken", encoding="utf-8")
    assert update_check.get_seen_version() is None

    monkeypatch.setattr(update_check, "__version__", "bad")
    seen_file.write_text(json.dumps({"version": "1.2.0"}), encoding="utf-8")
    assert not update_check.should_show_whats_new()

    blocked_parent = tmp_path / "blocked"
    blocked_parent.write_text("not a dir", encoding="utf-8")
    monkeypatch.setattr(update_check, "SEEN_VERSION_FILE", blocked_parent / "seen.json")
    update_check.mark_version_seen("1.2.0")
