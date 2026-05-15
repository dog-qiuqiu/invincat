"""Tests for project MCP trust persistence."""

from __future__ import annotations

import hashlib

from invincat_cli.mcp import trust
from invincat_cli.mcp.trust import (
    compute_config_fingerprint,
    is_project_mcp_trusted,
    revoke_project_mcp_trust,
    trust_project_mcp,
)


def test_compute_config_fingerprint_sorts_paths_before_hashing(tmp_path) -> None:
    first = tmp_path / "b.toml"
    second = tmp_path / "a.toml"
    first.write_text("server = 'b'\n")
    second.write_text("server = 'a'\n")

    expected = hashlib.sha256(second.read_bytes() + first.read_bytes()).hexdigest()

    assert compute_config_fingerprint([first, second]) == f"sha256:{expected}"


def test_compute_config_fingerprint_ignores_unreadable_paths(tmp_path) -> None:
    missing = tmp_path / "missing.toml"
    expected = hashlib.sha256().hexdigest()

    assert compute_config_fingerprint([missing]) == f"sha256:{expected}"


def test_load_config_handles_missing_invalid_and_unreadable_files(
    tmp_path,
    monkeypatch,
) -> None:
    missing = tmp_path / "missing.toml"
    assert trust._load_config(missing) == {}

    invalid = tmp_path / "invalid.toml"
    invalid.write_text("[broken")
    assert trust._load_config(invalid) == {}

    readable = tmp_path / "config.toml"
    readable.write_text("[mcp_trust.projects]\nroot = 'fp'\n")
    assert trust._load_config(readable)["mcp_trust"]["projects"]["root"] == "fp"

    def fail_open(*_args: object, **_kwargs: object) -> object:
        raise OSError("no read")

    monkeypatch.setattr(trust.Path, "open", fail_open)
    assert trust._load_config(readable) == {}


def test_save_config_cleans_temp_file_on_failure(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "config.toml"

    def fail_dump(_data: object, _file: object) -> None:
        raise ValueError("bad data")

    monkeypatch.setattr("tomli_w.dump", fail_dump)

    assert trust._save_config({"x": object()}, config_path) is False
    assert list(tmp_path.glob("*.tmp")) == []


def test_default_config_path_can_be_overridden(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(trust, "_DEFAULT_CONFIG_PATH", config_path)

    assert trust_project_mcp("root", "sha256:abc")
    assert is_project_mcp_trusted("root", "sha256:abc")
    assert revoke_project_mcp_trust("root")
    assert not is_project_mcp_trusted("root", "sha256:abc")


def test_project_mcp_trust_can_be_saved_checked_and_revoked(tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    project_root = str(tmp_path / "project")
    fingerprint = "sha256:abc"

    assert not is_project_mcp_trusted(
        project_root, fingerprint, config_path=config_path
    )
    assert trust_project_mcp(project_root, fingerprint, config_path=config_path)
    assert is_project_mcp_trusted(project_root, fingerprint, config_path=config_path)
    assert not is_project_mcp_trusted(
        project_root, "sha256:changed", config_path=config_path
    )
    assert revoke_project_mcp_trust(project_root, config_path=config_path)
    assert not is_project_mcp_trusted(
        project_root, fingerprint, config_path=config_path
    )


def test_revoke_project_mcp_trust_is_noop_for_missing_project(tmp_path) -> None:
    config_path = tmp_path / "config.toml"

    assert revoke_project_mcp_trust("missing", config_path=config_path)


def test_trust_and_revoke_report_save_failure(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "config.toml"
    monkeypatch.setattr(trust, "_save_config", lambda _data, _path: False)

    assert not trust_project_mcp("root", "sha256:abc", config_path=config_path)

    config_path.write_text("[mcp_trust.projects]\nroot = 'sha256:abc'\n")

    assert not revoke_project_mcp_trust("root", config_path=config_path)
