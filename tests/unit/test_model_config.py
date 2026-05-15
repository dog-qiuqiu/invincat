"""Unit tests for model configuration helpers."""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from invincat_cli import model_config as mc
from invincat_cli.model_config import ModelConfig, ModelSpec


@pytest.fixture(autouse=True)
def clear_model_config_caches():
    mc.clear_caches()
    yield
    mc.clear_caches()


def test_resolve_env_var_prefixed_values_shadow_canonical(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "canonical")
    monkeypatch.setenv("DEEPAGENTS_CLI_OPENAI_API_KEY", "prefixed")

    assert mc.resolve_env_var("OPENAI_API_KEY") == "prefixed"

    monkeypatch.setenv("DEEPAGENTS_CLI_OPENAI_API_KEY", "")
    assert mc.resolve_env_var("OPENAI_API_KEY") is None

    monkeypatch.setenv("DEEPAGENTS_CLI_CUSTOM_KEY", "already-prefixed")
    assert mc.resolve_env_var("DEEPAGENTS_CLI_CUSTOM_KEY") == "already-prefixed"


def test_model_spec_parse_and_validation() -> None:
    assert str(ModelSpec.parse("openai:gpt-5.2")) == "openai:gpt-5.2"
    assert ModelSpec.try_parse("missing-colon") is None

    with pytest.raises(ValueError, match="provider:model"):
        ModelSpec.parse("missing-colon")
    with pytest.raises(ValueError, match="Provider cannot be empty"):
        ModelSpec.parse(":gpt-5.2")
    with pytest.raises(ValueError, match="Model cannot be empty"):
        ModelSpec.parse("openai:")


def test_model_config_load_handles_missing_invalid_and_cached_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.toml"
    assert ModelConfig.load(missing) == ModelConfig()

    invalid = tmp_path / "invalid.toml"
    invalid.write_text("[models\n", encoding="utf-8")
    assert ModelConfig.load(invalid) == ModelConfig()

    default_path = tmp_path / "config.toml"
    default_path.write_text(
        '[models]\ndefault = "openai:gpt-5.2"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(mc, "DEFAULT_CONFIG_PATH", default_path)

    first = ModelConfig.load()
    default_path.write_text(
        '[models]\ndefault = "anthropic:claude-sonnet"\n',
        encoding="utf-8",
    )
    second = ModelConfig.load()

    assert first is second
    assert second.default_model == "openai:gpt-5.2"

    unreadable = tmp_path / "unreadable.toml"
    unreadable.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        Path,
        "open",
        lambda _self, *_a, **_k: (_ for _ in ()).throw(OSError("no read")),
    )
    assert ModelConfig.load(unreadable) == ModelConfig()

    monkeypatch.setattr(mc, "DEFAULT_CONFIG_PATH", unreadable)
    mc.clear_caches()
    assert ModelConfig.load() == ModelConfig()
    assert ModelConfig.load() is ModelConfig.load()


def test_model_config_loads_fields_and_warns_on_inconsistent_config(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[models]
default = "gpt"
recent = "claude"
memory_default = "memory"

[models.providers.openai]
enabled = "yes"
models = ["gpt-5.2"]
api_key_env = "CUSTOM_OPENAI_KEY"
base_url = "https://api.example.com"
class_path = "invalid"

[models.providers.openai.params]
temperature = 0.2

[models.providers.openai.params."gpt-5.2"]
temperature = 0.7
extra_body = { thinking = { type = "enabled" } }

[models.providers.openai.params.unknown]
top_p = 0.5

[models.providers.openai.profile]
tool_calling = false

[models.providers.openai.profile."gpt-5.2"]
max_input_tokens = 123

[models.target_params.primary."openai:gpt-5.2"]
reasoning_effort = "medium"
""".strip(),
        encoding="utf-8",
    )

    caplog.set_level(logging.WARNING)
    config = ModelConfig.load(path)

    assert config.default_model == "gpt"
    assert config.recent_model == "claude"
    assert config.memory_default_model == "memory"
    assert config.is_provider_enabled("missing") is True
    assert config.is_provider_enabled("openai") is True
    assert config.get_all_models() == [("gpt-5.2", "openai")]
    assert config.get_provider_for_model("gpt-5.2") == "openai"
    assert config.get_provider_for_model("missing") is None
    assert config.get_base_url("openai") == "https://api.example.com"
    assert config.get_api_key_env("openai") == "CUSTOM_OPENAI_KEY"
    assert config.get_class_path("openai") == "invalid"
    assert config.get_kwargs("missing") == {}
    assert config.get_kwargs("openai", model_name="gpt-5.2") == {
        "temperature": 0.7,
        "extra_body": {"thinking": {"type": "enabled"}},
    }
    assert config.get_profile_overrides("openai", model_name="gpt-5.2") == {
        "tool_calling": False,
        "max_input_tokens": 123,
    }
    assert config.get_target_model_params("primary", "openai:gpt-5.2") == {
        "reasoning_effort": "medium"
    }
    assert config.get_target_model_params("memory", "openai:gpt-5.2") == {}
    assert "should use provider:model format" in caplog.text
    assert "non-boolean 'enabled'" in caplog.text
    assert "invalid class_path" in caplog.text
    assert "params for 'unknown'" in caplog.text


def test_available_models_filters_disabled_providers_and_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_config = ModelConfig(
        providers={
            "openai": {"models": ["gpt-5.2"]},
            "hidden": {"enabled": False, "models": ["hidden-model"]},
        }
    )
    second_config = ModelConfig(providers={"anthropic": {"models": ["claude"]}})
    loads = [first_config, second_config]

    monkeypatch.setattr(mc.ModelConfig, "load", lambda: loads.pop(0))

    assert mc.get_available_models() == {"openai": ["gpt-5.2"]}
    assert mc.get_available_models() == {"openai": ["gpt-5.2"]}


def test_model_profiles_merge_registry_config_and_cli_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ModelConfig(
        providers={
            "openai": {
                "models": ["custom"],
                "profile": {
                    "tool_calling": False,
                    "gpt-5.2": {"max_input_tokens": 456},
                    "custom": {"image_inputs": True},
                },
            },
            "disabled": {"enabled": False, "models": ["ignored"]},
            "classy": {
                "class_path": "custom_pkg.models:Chat",
                "models": ["local-only"],
                "profile": {"local-only": {"max_input_tokens": 64}},
            },
        }
    )
    loaded_modules: list[str] = []

    def fake_load_profiles(module_path: str) -> dict[str, dict[str, object]]:
        loaded_modules.append(module_path)
        if module_path == "openai_profiles":
            return {"gpt-5.2": {"max_input_tokens": 100, "tool_calling": True}}
        if module_path == "custom_pkg.data._profiles":
            return {"remote": {"max_input_tokens": 32}}
        raise ImportError("missing profiles")

    monkeypatch.setattr(mc.ModelConfig, "load", lambda: config)
    monkeypatch.setattr(
        mc,
        "_get_provider_profile_modules",
        lambda: [
            ("openai", "openai_profiles"),
            ("disabled", "disabled_profiles"),
            ("missing", "missing_profiles"),
        ],
    )
    monkeypatch.setattr(mc, "_load_provider_profiles", fake_load_profiles)

    cli_override = {"image_inputs": False}
    profiles = mc.get_model_profiles(cli_override=cli_override)
    cached = mc.get_model_profiles(cli_override=cli_override)

    assert profiles is cached
    assert loaded_modules == [
        "openai_profiles",
        "missing_profiles",
        "custom_pkg.data._profiles",
    ]
    assert profiles["openai:gpt-5.2"]["profile"] == {
        "max_input_tokens": 456,
        "tool_calling": False,
        "image_inputs": False,
    }
    assert profiles["openai:gpt-5.2"]["overridden_keys"] == frozenset(
        {"tool_calling", "max_input_tokens", "image_inputs"}
    )
    assert profiles["openai:custom"]["profile"] == {
        "tool_calling": False,
        "image_inputs": False,
    }
    assert profiles["classy:remote"]["profile"] == {
        "max_input_tokens": 32,
        "image_inputs": False,
    }
    assert profiles["classy:local-only"]["profile"] == {
        "max_input_tokens": 64,
        "image_inputs": False,
    }
    with pytest.raises(TypeError):
        profiles["new"] = {"profile": {}, "overridden_keys": frozenset()}


def test_model_profiles_class_path_import_error_falls_back_to_config_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ModelConfig(
        providers={
            "classy": {
                "class_path": "custom_pkg.models:Chat",
                "models": ["local"],
                "profile": {"local": {"max_input_tokens": 16}},
            },
        }
    )
    monkeypatch.setattr(mc.ModelConfig, "load", lambda: config)
    monkeypatch.setattr(mc, "_get_provider_profile_modules", lambda: [])
    monkeypatch.setattr(
        mc,
        "_load_provider_profiles",
        lambda _module_path: (_ for _ in ()).throw(ImportError("missing")),
    )

    profiles = mc.get_model_profiles()

    assert profiles["classy:local"]["profile"] == {"max_input_tokens": 16}


def test_load_provider_profiles_reads_profiles_module_and_caches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package = tmp_path / "fake_provider"
    data_dir = package / "data"
    data_dir.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (data_dir / "__init__.py").write_text("", encoding="utf-8")
    profiles_file = data_dir / "_profiles.py"
    profiles_file.write_text(
        '_PROFILES = {"model": {"max_input_tokens": 10}}\n',
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    first = mc._load_provider_profiles("fake_provider.data._profiles")
    profiles_file.write_text("_PROFILES = {}\n", encoding="utf-8")
    second = mc._load_provider_profiles("fake_provider.data._profiles")

    assert first == {"model": {"max_input_tokens": 10}}
    assert second is first
    with pytest.raises(ImportError, match="not installed"):
        mc._load_provider_profiles("not_installed_provider.data._profiles")


def test_provider_registry_and_profile_loader_edge_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import langchain.chat_models.base as lc_base

    monkeypatch.setattr(lc_base, "_BUILTIN_PROVIDERS", None, raising=False)
    monkeypatch.setattr(
        lc_base,
        "_SUPPORTED_PROVIDERS",
        {"legacy": ("legacy_pkg.chat",)},
        raising=False,
    )
    assert mc._get_builtin_providers() == {"legacy": ("legacy_pkg.chat",)}
    assert mc._get_builtin_providers() == {"legacy": ("legacy_pkg.chat",)}
    assert mc._get_provider_profile_modules() == [
        ("legacy", "legacy_pkg.data._profiles")
    ]

    assert mc._profile_module_from_class_path("bad") is None
    assert mc._profile_module_from_class_path(":Chat") is None

    package_dir = tmp_path / "provider_pkg"
    package_dir.mkdir()
    spec = SimpleNamespace(origin=None, submodule_search_locations=[str(package_dir)])
    monkeypatch.setattr(mc.importlib.util, "find_spec", lambda _name: spec)

    with pytest.raises(ImportError, match="Profile module not found"):
        mc._load_provider_profiles("provider_pkg.data._profiles")

    profiles_file = package_dir / "data" / "_profiles.py"
    profiles_file.parent.mkdir()
    profiles_file.write_text("_PROFILES = {}\n", encoding="utf-8")
    monkeypatch.setattr(mc.importlib.util, "spec_from_file_location", lambda *_a: None)
    with pytest.raises(ImportError, match="Could not create module spec"):
        mc._load_provider_profiles("provider_pkg.data._profiles")

    mc._provider_profiles_cache.clear()
    no_location_spec = SimpleNamespace(origin=None, submodule_search_locations=None)
    monkeypatch.setattr(mc.importlib.util, "find_spec", lambda _name: no_location_spec)
    with pytest.raises(ImportError, match="Cannot determine location"):
        mc._load_provider_profiles("provider_pkg.data._profiles")


def test_model_profiles_cache_and_loader_runtime_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ModelConfig(
        providers={
            "openai": {
                "models": ["configured"],
                "profile": {"configured": {"tool_calling": True}},
            },
            "classy": {
                "class_path": "custom_pkg.models:Chat",
                "models": ["local"],
            },
        }
    )

    monkeypatch.setattr(mc.ModelConfig, "load", lambda: config)
    monkeypatch.setattr(
        mc,
        "_get_provider_profile_modules",
        lambda: [("openai", "openai_profiles")],
    )
    monkeypatch.setattr(
        mc,
        "_load_provider_profiles",
        lambda _module_path: (_ for _ in ()).throw(RuntimeError("bad profiles")),
    )

    profiles = mc.get_model_profiles()
    cached = mc.get_model_profiles()

    assert cached is profiles
    assert profiles["openai:configured"]["profile"] == {"tool_calling": True}
    assert profiles["classy:local"]["profile"] == {}


def test_provider_credentials_prefer_config_then_known_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = ModelConfig(
        providers={
            "custom": {"api_key_env": "CUSTOM_KEY", "models": ["m"]},
            "classy": {"class_path": "pkg.models:Chat", "models": ["m"]},
            "noenv": {"models": ["m"]},
        }
    )
    monkeypatch.setattr(mc.ModelConfig, "load", lambda: config)
    monkeypatch.delenv("CUSTOM_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert config.has_credentials("missing") is False
    assert config.has_credentials("noenv") is None
    assert config.get_profile_overrides("missing") == {}
    assert mc.has_provider_credentials("custom") is False
    assert mc.has_provider_credentials("classy") is True
    assert mc.has_provider_credentials("unknown") is None
    assert mc.has_provider_credentials("openai") is False
    assert mc.get_credential_env_var("custom") == "CUSTOM_KEY"
    assert mc.get_credential_env_var("openai") == "OPENAI_API_KEY"

    monkeypatch.setenv("CUSTOM_KEY", "secret")
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    assert mc.has_provider_credentials("custom") is True
    assert mc.has_provider_credentials("openai") is True


def test_save_and_clear_model_preferences_and_target_params(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"

    assert mc.save_default_model("openai:gpt-5.2", config_path=path)
    assert mc.save_recent_model("anthropic:claude", config_path=path)
    assert mc.save_memory_default_model("openai:gpt-mini", config_path=path)
    assert mc.save_target_model_params(
        "primary",
        "openai:gpt-5.2",
        {"temperature": 0.2},
        config_path=path,
    )

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["models"]["default"] == "openai:gpt-5.2"
    assert data["models"]["recent"] == "anthropic:claude"
    assert data["models"]["memory_default"] == "openai:gpt-mini"
    assert data["models"]["target_params"]["primary"]["openai:gpt-5.2"] == {
        "temperature": 0.2
    }

    assert mc.clear_default_model(config_path=path)
    assert mc.clear_memory_default_model(config_path=path)
    assert mc.save_target_model_params(
        "primary",
        "openai:gpt-5.2",
        None,
        config_path=path,
    )
    assert (
        mc.get_target_model_params("primary", "openai:gpt-5.2", config_path=path) == {}
    )
    new_target = tmp_path / "new-target.toml"
    assert mc.save_target_model_params(
        "memory",
        "openai:gpt-mini",
        {"temperature": 0.1},
        config_path=new_target,
    )
    assert mc.get_target_model_params(
        "memory",
        "openai:gpt-mini",
        config_path=new_target,
    ) == {"temperature": 0.1}
    assert mc.clear_default_model(config_path=tmp_path / "missing.toml")
    assert mc.clear_memory_default_model(config_path=path)
    assert mc.clear_memory_default_model(config_path=path)

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert "default" not in data["models"]
    assert "memory_default" not in data["models"]
    assert "target_params" not in data["models"]


def test_warning_suppression_round_trips_and_ignores_invalid_shapes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"

    assert mc.is_warning_suppressed("ripgrep", config_path=path) is False
    assert mc.suppress_warning("ripgrep", config_path=path)
    assert mc.suppress_warning("ripgrep", config_path=path)
    assert mc.is_warning_suppressed("ripgrep", config_path=path) is True

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["warnings"]["suppress"] == ["ripgrep"]

    path.write_text('[warnings]\nsuppress = "bad"\n', encoding="utf-8")
    assert mc.is_warning_suppressed("ripgrep", config_path=path) is False

    path.write_text("[warnings\n", encoding="utf-8")
    assert mc.is_warning_suppressed("ripgrep", config_path=path) is False


def test_thread_config_load_save_and_cache_behaviour(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    monkeypatch.setattr(mc, "DEFAULT_CONFIG_PATH", path)

    default_config = mc.load_thread_config()
    assert default_config.columns == mc.THREAD_COLUMN_DEFAULTS
    assert default_config.relative_time is True
    assert default_config.sort_order == "updated_at"
    assert mc.load_thread_config() is default_config

    assert mc.save_thread_columns({"messages": False, "cwd": True}, config_path=path)
    assert mc.save_thread_relative_time(False, config_path=path)
    assert mc.save_thread_sort_order("created_at", config_path=path)

    loaded = mc.load_thread_config(path)
    assert loaded.columns["messages"] is False
    assert loaded.columns["cwd"] is True
    assert loaded.relative_time is False
    assert loaded.sort_order == "created_at"
    assert mc.load_thread_columns(path)["cwd"] is True
    assert mc.load_thread_relative_time(path) is False
    assert mc.load_thread_sort_order(path) == "created_at"

    with pytest.raises(ValueError, match="Invalid sort_order"):
        mc.save_thread_sort_order("bad", config_path=path)

    path.write_text("[threads\n", encoding="utf-8")
    assert mc.load_thread_config(path).columns == mc.THREAD_COLUMN_DEFAULTS
    assert mc.load_thread_columns(path) == mc.THREAD_COLUMN_DEFAULTS
    assert mc.load_thread_relative_time(path) is True
    assert mc.load_thread_sort_order(path) == "updated_at"

    missing = tmp_path / "missing-thread.toml"
    assert mc.load_thread_columns(missing) == mc.THREAD_COLUMN_DEFAULTS
    assert mc.load_thread_relative_time(missing) is True
    assert mc.load_thread_sort_order(missing) == "updated_at"

    new_relative = tmp_path / "new-relative.toml"
    assert mc.save_thread_relative_time(True, config_path=new_relative)
    assert mc.load_thread_relative_time(new_relative) is True

    new_sort = tmp_path / "new-sort.toml"
    assert mc.save_thread_sort_order("created_at", config_path=new_sort)
    assert mc.load_thread_sort_order(new_sort) == "created_at"


def test_default_config_path_helpers_round_trip(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    monkeypatch.setattr(mc, "DEFAULT_CONFIG_PATH", path)

    assert ModelConfig.load() == ModelConfig()
    assert ModelConfig.load() is ModelConfig.load()
    mc.clear_caches()

    path.write_text("[models\n", encoding="utf-8")
    assert ModelConfig.load() == ModelConfig()
    mc.clear_caches()
    path.unlink()

    assert mc.save_default_model("openai:gpt-5.2")
    assert mc.save_recent_model("anthropic:claude")
    assert mc.save_memory_default_model("openai:gpt-mini")
    assert mc.save_target_model_params(
        "primary", "openai:gpt-5.2", {"temperature": 0.2}
    )
    assert mc.is_warning_suppressed("ripgrep") is False
    assert mc.suppress_warning("ripgrep")
    assert mc.load_thread_columns()["messages"] is True
    assert mc.save_thread_columns({"messages": False})
    assert mc.load_thread_relative_time() is True
    assert mc.save_thread_relative_time(False)
    assert mc.load_thread_sort_order() == "updated_at"
    assert mc.save_thread_sort_order("created_at")
    assert mc.register_provider_model(
        "local",
        "fake",
        class_path="local_models:Chat",
    )

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert data["models"]["recent"] == "anthropic:claude"
    assert data["models"]["providers"]["local"]["class_path"] == "local_models:Chat"
    assert data["threads"]["columns"] == {"messages": False}
    assert data["threads"]["relative_time"] is False
    assert data["threads"]["sort_order"] == "created_at"
    assert data["warnings"]["suppress"] == ["ripgrep"]

    assert mc.clear_default_model()
    assert mc.clear_memory_default_model()
    assert mc.save_target_model_params("primary", "openai:gpt-5.2", None)


def test_atomic_writes_clean_temp_files_on_dump_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[models]\ndefault = "openai:gpt-5.2"\n'
        '[models.target_params.primary."openai:gpt-5.2"]\n'
        "temperature = 0.2\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        mc.tomli_w,
        "dump",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    assert mc.save_default_model("openai:gpt-5.2", config_path=path) is False
    assert (
        mc.save_target_model_params(
            "primary", "openai:gpt-5.2", {"temperature": 0.3}, config_path=path
        )
        is False
    )
    assert mc.clear_default_model(config_path=path) is False
    assert mc.suppress_warning("ripgrep", config_path=path) is False
    assert mc.save_thread_columns({"cwd": True}, config_path=path) is False
    assert mc.save_thread_relative_time(False, config_path=path) is False
    assert mc.save_thread_sort_order("created_at", config_path=path) is False
    assert mc.register_provider_model("openai", "gpt-mini", config_path=path) is False
    assert not list(tmp_path.glob("*.tmp"))


def test_register_provider_model_merges_existing_provider_config(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[models.providers.openai]
models = ["existing"]
class_path = "already.set:Chat"

[models.providers.openai.params."new-model"]
base_url = "https://old.example.com"
extra_body = { old = true }
""".strip(),
        encoding="utf-8",
    )

    assert mc.register_provider_model(
        "openai",
        "new-model",
        api_key_env="OPENAI_API_KEY",
        base_url="https://new.example.com",
        max_input_tokens=128,
        extra_params={"extra_body": {"new": True}},
        class_path="ignored:Class",
        config_path=path,
    )

    data = tomllib.loads(path.read_text(encoding="utf-8"))
    provider = data["models"]["providers"]["openai"]
    assert provider["models"] == ["existing", "new-model"]
    assert provider["class_path"] == "already.set:Chat"
    assert provider["params"]["new-model"] == {
        "base_url": "https://new.example.com",
        "api_key_env": "OPENAI_API_KEY",
        "extra_body": {"old": True, "new": True},
    }
    assert provider["profile"]["new-model"]["max_input_tokens"] == 128

    new_path = tmp_path / "new-config.toml"
    assert mc.register_provider_model("local", "fake", config_path=new_path)
    new_data = tomllib.loads(new_path.read_text(encoding="utf-8"))
    assert new_data["models"]["providers"]["local"]["models"] == ["fake"]


def test_save_helpers_return_false_on_invalid_existing_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("[models\n", encoding="utf-8")

    assert mc.save_default_model("openai:gpt-5.2", config_path=path) is False
    assert mc.clear_default_model(config_path=path) is False
    assert (
        mc.save_target_model_params(
            "primary",
            "openai:gpt-5.2",
            {"temperature": 0.2},
            config_path=path,
        )
        is False
    )
    assert mc.suppress_warning("ripgrep", config_path=path) is False
    assert mc.save_thread_columns({"cwd": True}, config_path=path) is False
    assert mc.save_thread_relative_time(False, config_path=path) is False
    assert mc.save_thread_sort_order("created_at", config_path=path) is False
    assert mc.register_provider_model("openai", "gpt-5.2", config_path=path) is False
