"""Unit tests for configuration module."""

import os
import tempfile
import tomllib
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from invincat_cli.config import (
    Settings,
    build_stream_config,
    is_ascii_mode,
    is_shell_command_allowed,
)
from invincat_cli.model_config import ModelConfig, ModelConfigError


class TestSettings:
    """Tests for Settings class."""

    def test_from_environment(self):
        """Test creating settings from environment."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Set up test environment
            test_env = {
                "OPENAI_API_KEY": "test-openai-key",
                "ANTHROPIC_API_KEY": "test-anthropic-key",
                "TAVILY_API_KEY": "test-tavily-key",
                "DEEPAGENTS_CLI_SHELL_ALLOW_LIST": "ls,cd,pwd",
            }
            
            with patch.dict(os.environ, test_env, clear=True):
                settings = Settings.from_environment(start_path=Path(tmpdir))
                
                assert settings.openai_api_key == "test-openai-key"
                assert settings.anthropic_api_key == "test-anthropic-key"
                assert settings.tavily_api_key == "test-tavily-key"
                assert settings.shell_allow_list == ["ls", "cd", "pwd"]
                assert settings.project_root is None  # Not a git repo

    def test_get_agent_dir(self):
        """Test getting agent directory path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("invincat_cli.config.Path.home", return_value=Path(tmpdir)):
                settings = Settings.from_environment()
                agent_dir = settings.get_agent_dir("test-agent")

                expected = Path(tmpdir) / ".invincat" / "test-agent"
                assert agent_dir == expected
                assert "test-agent" in str(agent_dir)

    def test_get_user_agent_md_path(self):
        """Test getting user-level AGENTS.md path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("invincat_cli.config.Path.home", return_value=Path(tmpdir)):
                md_path = Settings.get_user_agent_md_path("test-agent")

                expected = Path(tmpdir) / ".invincat" / "test-agent" / "AGENTS.md"
                assert md_path == expected

    def test_provider_key_properties(self):
        """Test provider key availability properties."""
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_environment()

            assert not settings.has_openai
            assert not settings.has_anthropic

            settings.openai_api_key = "test-key"
            settings.anthropic_api_key = "test-key"

            assert settings.has_openai
            assert settings.has_anthropic


class TestUtilityFunctions:
    """Tests for utility functions."""

    def test_is_ascii_mode(self):
        """Test ASCII mode detection."""
        # Test with ASCII locale when stdout encoding is non-UTF.
        with (
            patch("sys.stdout", new=Mock(encoding="ascii")),
            patch.dict(os.environ, {"LC_ALL": "C", "LANG": "C"}),
        ):
            assert is_ascii_mode() is True

        # UTF-8 stdout encoding should force Unicode mode.
        with (
            patch("sys.stdout", new=Mock(encoding="UTF-8")),
            patch.dict(os.environ, {"LC_ALL": "C", "LANG": "C"}),
        ):
            assert is_ascii_mode() is False

    def test_is_shell_command_allowed(self):
        """Test shell command allow list checking."""
        # Test with no allow list (all commands require approval)
        assert is_shell_command_allowed("ls", None) is False
        assert is_shell_command_allowed("cd /tmp", None) is False

        # Test with allow list
        allow_list = ["ls", "cd", "pwd", "/bin/ls"]
        assert is_shell_command_allowed("ls", allow_list) is True
        assert is_shell_command_allowed("cd /tmp", allow_list) is True
        assert is_shell_command_allowed("rm -rf /", allow_list) is False
        assert is_shell_command_allowed("ls -la", allow_list) is True  # ls with args
        assert is_shell_command_allowed("/bin/ls", allow_list) is True
        assert is_shell_command_allowed("./script.sh", allow_list) is False

    def test_build_stream_config(self):
        """Test building stream configuration."""
        config = build_stream_config("thread-1", "assistant-1")

        assert "configurable" in config
        assert "metadata" in config
        assert config["configurable"]["thread_id"] == "thread-1"
        assert config["metadata"]["assistant_id"] == "assistant-1"


class TestModelCreation:
    """Tests for model creation functions."""

    @patch("invincat_cli.model_config.ModelConfig.load")
    def test_default_model_spec_requires_registered_model(self, mock_model_config_load):
        """Environment credentials alone should not select a default model."""
        from invincat_cli.config import _get_default_model_spec

        mock_model_config_load.return_value = ModelConfig()

        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=True):
            with pytest.raises(ModelConfigError, match="No model configured"):
                _get_default_model_spec()

    @patch("invincat_cli.model_config.ModelConfig.load")
    def test_default_model_spec_ignores_unregistered_recent(
        self, mock_model_config_load
    ):
        """Stale recent/default values are ignored unless they are registered."""
        from invincat_cli.config import _get_default_model_spec

        mock_model_config_load.return_value = ModelConfig(
            default_model="openai:gpt-5.2",
            recent_model="anthropic:claude-sonnet-4-6",
            providers={
                "openai": {
                    "models": ["gpt-4o"],
                },
            },
        )

        assert _get_default_model_spec() == "openai:gpt-4o"

    @patch("invincat_cli.model_config.ModelConfig.load")
    def test_default_model_spec_uses_registered_recent(self, mock_model_config_load):
        """Registered recent model takes priority over first registered model."""
        from invincat_cli.config import _get_default_model_spec

        mock_model_config_load.return_value = ModelConfig(
            recent_model="google_genai:gemini-2.5-pro",
            providers={
                "openai": {
                    "models": ["gpt-4o"],
                },
                "google_genai": {
                    "models": ["gemini-2.5-pro"],
                },
            },
        )

        assert _get_default_model_spec() == "google_genai:gemini-2.5-pro"

    @patch("invincat_cli.config._create_model_via_init")
    @patch("invincat_cli.model_config.ModelConfig.load")
    @patch("invincat_cli.config._get_provider_kwargs", return_value={"api_key": "test-key"})
    def test_create_model_openai(
        self, _mock_kwargs, mock_model_config_load, mock_create_via_init
    ):
        """Test creating OpenAI model returns ModelResult."""
        from invincat_cli.config import create_model

        mock_model = Mock()
        mock_model.profile = {"max_input_tokens": 128000, "image_inputs": False}
        mock_create_via_init.return_value = mock_model

        mock_cfg = Mock()
        mock_cfg.get_class_path.return_value = None
        mock_cfg.get_profile_overrides.return_value = {}
        mock_model_config_load.return_value = mock_cfg

        result = create_model("openai:gpt-4o")

        assert result.model is mock_model
        assert result.model_name == "gpt-4o"
        assert result.provider == "openai"
        assert result.context_limit == 128000
        assert "image" in result.unsupported_modalities

    def test_create_model_invalid_model_spec(self):
        """Test invalid provider:model syntax."""
        from invincat_cli.config import create_model

        with pytest.raises(ModelConfigError, match="model name is required"):
            create_model("openai:")

    @patch("invincat_cli.models.deepseek_chat_openai.DeepSeekChatOpenAICompat")
    @patch("invincat_cli.model_config.ModelConfig.load")
    @patch(
        "invincat_cli.config._get_provider_kwargs",
        return_value={
            "api_key": "test-key",
            "base_url": "https://api.deepseek.com/v1",
            "reasoning_effort": "medium",
            "extra_body": {"thinking": {"type": "disabled"}},
        },
    )
    def test_create_model_strips_reasoning_effort_when_deepseek_thinking_disabled(
        self,
        _mock_kwargs,
        mock_model_config_load,
        mock_deepseek_model,
    ):
        """DeepSeek rejects reasoning_effort when thinking is explicitly disabled."""
        from invincat_cli.config import create_model

        mock_model = Mock()
        mock_model.profile = {}
        mock_deepseek_model.return_value = mock_model

        mock_cfg = Mock()
        mock_cfg.get_class_path.return_value = None
        mock_cfg.get_profile_overrides.return_value = {}
        mock_model_config_load.return_value = mock_cfg

        create_model("openai:deepseek-chat")

        _, kwargs = mock_deepseek_model.call_args
        assert kwargs["model"] == "deepseek-chat"
        assert kwargs["extra_body"]["thinking"]["type"] == "disabled"
        assert "reasoning_effort" not in kwargs

    def test_register_model_merges_extra_params(self):
        """Registering a model can persist nested constructor params."""
        from invincat_cli.model_config import register_provider_model

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"

            assert register_provider_model(
                "openai",
                "deepseek-chat",
                api_key_env="DEEPSEEK_API_KEY",
                base_url="https://api.deepseek.com/v1",
                max_input_tokens=64000,
                extra_params={
                    "reasoning_effort": "medium",
                    "extra_body": {
                        "thinking": {
                            "type": "disabled",
                        },
                    },
                },
                config_path=config_path,
            )

            data = tomllib.loads(config_path.read_text())
            model_params = data["models"]["providers"]["openai"]["params"][
                "deepseek-chat"
            ]

            assert model_params["api_key_env"] == "DEEPSEEK_API_KEY"
            assert model_params["base_url"] == "https://api.deepseek.com/v1"
            assert model_params["reasoning_effort"] == "medium"
            assert model_params["extra_body"]["thinking"]["type"] == "disabled"
            assert (
                data["models"]["providers"]["openai"]["profile"]["deepseek-chat"][
                    "max_input_tokens"
                ]
                == 64000
            )

    def test_model_config_returns_isolated_nested_overrides(self):
        """Model params/profile reads should not share nested mutable objects."""
        config = ModelConfig(
            providers={
                "openai": {
                    "models": ["deepseek-chat"],
                    "params": {
                        "deepseek-chat": {
                            "extra_body": {
                                "thinking": {
                                    "type": "enabled",
                                },
                            },
                        },
                    },
                    "profile": {
                        "deepseek-chat": {
                            "metadata": {
                                "owner": "primary",
                            },
                        },
                    },
                },
            },
        )

        first_params = config.get_kwargs("openai", model_name="deepseek-chat")
        second_params = config.get_kwargs("openai", model_name="deepseek-chat")
        first_params["extra_body"]["thinking"]["type"] = "disabled"

        assert second_params["extra_body"]["thinking"]["type"] == "enabled"

        first_profile = config.get_profile_overrides(
            "openai", model_name="deepseek-chat"
        )
        second_profile = config.get_profile_overrides(
            "openai", model_name="deepseek-chat"
        )
        first_profile["metadata"]["owner"] = "memory"

        assert second_profile["metadata"]["owner"] == "primary"

    def test_target_model_params_are_isolated_by_target(self):
        """Primary and memory target params can differ for the same model."""
        from invincat_cli.model_config import (
            get_target_model_params,
            save_target_model_params,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.toml"

            assert save_target_model_params(
                "primary",
                "openai:deepseek-chat",
                {
                    "reasoning_effort": "high",
                    "extra_body": {"thinking": {"type": "enabled"}},
                },
                config_path=config_path,
            )
            assert save_target_model_params(
                "memory",
                "openai:deepseek-chat",
                {
                    "reasoning_effort": "low",
                    "extra_body": {"thinking": {"type": "disabled"}},
                },
                config_path=config_path,
            )

            primary = get_target_model_params(
                "primary", "openai:deepseek-chat", config_path=config_path
            )
            memory = get_target_model_params(
                "memory", "openai:deepseek-chat", config_path=config_path
            )

            assert primary["reasoning_effort"] == "high"
            assert primary["extra_body"]["thinking"]["type"] == "enabled"
            assert memory["reasoning_effort"] == "low"
            assert memory["extra_body"]["thinking"]["type"] == "disabled"

            primary["extra_body"]["thinking"]["type"] = "disabled"

            assert (
                get_target_model_params(
                    "primary", "openai:deepseek-chat", config_path=config_path
                )["extra_body"]["thinking"]["type"]
                == "enabled"
            )


if __name__ == "__main__":
    pytest.main([__file__])
