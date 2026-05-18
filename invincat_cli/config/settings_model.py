"""Settings object and environment reload helpers for CLI configuration."""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


def _home() -> Path:
    """Return home path through config module for monkeypatch compatibility."""
    from invincat_cli import config as config_mod

    return config_mod.Path.home()


@dataclass
class Settings:
    """Global settings and environment detection for invincat-cli.

    This class is initialized once at startup and provides access to:
    - Available models and API keys
    - Current project information
    - Tool availability (e.g., Tavily)
    - File system paths
    """

    openai_api_key: str | None

    anthropic_api_key: str | None

    google_api_key: str | None

    nvidia_api_key: str | None

    tavily_api_key: str | None

    google_cloud_project: str | None

    deepagents_langchain_project: str | None

    user_langchain_project: str | None

    model_name: str | None = None

    model_provider: str | None = None

    model_context_limit: int | None = None

    model_unsupported_modalities: frozenset[str] = frozenset()

    project_root: Path | None = None

    shell_allow_list: list[str] | None = None

    extra_skills_dirs: list[Path] | None = None

    @classmethod
    def from_environment(cls, *, start_path: Path | None = None) -> Settings:
        """Create settings by detecting the current environment.

        Args:
            start_path: Directory to start project detection from (defaults to cwd)

        Returns:
            Settings instance with detected configuration
        """
        # Detect API keys (normalize empty strings to None).
        from invincat_cli import config as config_mod
        from invincat_cli.model_config import resolve_env_var

        openai_key = resolve_env_var("OPENAI_API_KEY")
        anthropic_key = resolve_env_var("ANTHROPIC_API_KEY")
        google_key = resolve_env_var("GOOGLE_API_KEY")
        nvidia_key = resolve_env_var("NVIDIA_API_KEY")
        tavily_key = resolve_env_var("TAVILY_API_KEY")
        google_cloud_project = resolve_env_var("GOOGLE_CLOUD_PROJECT")

        # Detect LangSmith configuration
        # DEEPAGENTS_CLI_LANGSMITH_PROJECT: Project for deepagents agent tracing
        # user_langchain_project: User's ORIGINAL LANGSMITH_PROJECT (before override)
        # When accessed via the module-level `settings` singleton,
        # _ensure_bootstrap() has already run and may have overridden
        # LANGSMITH_PROJECT. We use the saved original value, not the
        # current os.environ value. Direct callers should ensure
        # bootstrap has run if they depend on the override.
        from invincat_cli.core.env_vars import (
            EXTRA_SKILLS_DIRS,
            LANGSMITH_PROJECT,
            SHELL_ALLOW_LIST,
        )

        deepagents_langchain_project = resolve_env_var(LANGSMITH_PROJECT)
        user_langchain_project = config_mod._original_langsmith_project

        # Detect project
        from invincat_cli.project_utils import find_project_root

        project_root = find_project_root(start_path)

        # Parse shell command allow-list from environment
        # Format: comma-separated list of commands (e.g., "ls,cat,grep,pwd")

        shell_allow_list_str = os.environ.get(SHELL_ALLOW_LIST)
        shell_allow_list = config_mod.parse_shell_allow_list(shell_allow_list_str)

        # Parse extra skill containment roots from env var or config.toml.
        # These extend the path allowlist for load_skill_content but do not
        # add new skill discovery locations.
        extra_skills_dirs = config_mod._parse_extra_skills_dirs(
            os.environ.get(EXTRA_SKILLS_DIRS),
            config_mod._read_config_toml_skills_dirs(),
        )

        return cls(
            openai_api_key=openai_key,
            anthropic_api_key=anthropic_key,
            google_api_key=google_key,
            nvidia_api_key=nvidia_key,
            tavily_api_key=tavily_key,
            google_cloud_project=google_cloud_project,
            deepagents_langchain_project=deepagents_langchain_project,
            user_langchain_project=user_langchain_project,
            project_root=project_root,
            shell_allow_list=shell_allow_list,
            extra_skills_dirs=extra_skills_dirs,
        )

    def reload_from_environment(self, *, start_path: Path | None = None) -> list[str]:
        """Reload selected settings from environment variables and project files.

        This refreshes only fields that are expected to change at runtime
        (API keys, Google Cloud project, project root, shell allow-list, and
        LangSmith tracing project).

        Runtime model state (`model_name`, `model_provider`,
        `model_context_limit`) and the original user LangSmith project
        (`user_langchain_project`) are intentionally preserved -- they are
        not in `reloadable_fields` and are never touched by this method.

        !!! note

            `.env` files are loaded with `override=False`, so shell-exported
            variables always take precedence.  To override a shell-exported key
            from `.env`, use the `DEEPAGENTS_CLI_` prefix (e.g.
            `DEEPAGENTS_CLI_OPENAI_API_KEY`).

        Args:
            start_path: Directory to start project detection from (defaults to cwd).

        Returns:
            A list of human-readable change descriptions.
        """
        from invincat_cli import config as config_mod

        config_mod._load_dotenv(start_path=start_path)

        api_key_fields = {
            "openai_api_key",
            "anthropic_api_key",
            "google_api_key",
            "nvidia_api_key",
            "tavily_api_key",
        }
        """Fields that hold API keys — used to mask values in change reports
        so secrets are not logged as plaintext."""

        reloadable_fields = (
            "openai_api_key",
            "anthropic_api_key",
            "google_api_key",
            "nvidia_api_key",
            "tavily_api_key",
            "google_cloud_project",
            "deepagents_langchain_project",
            "project_root",
            "shell_allow_list",
            "extra_skills_dirs",
        )
        """Fields refreshed on `/reload`.

        Runtime model state (`model_name`, `model_provider`, `model_context_limit`)
        and the original user LangSmith project are intentionally excluded —
        they are set once and should not change across reloads.
        """

        previous = {field: getattr(self, field) for field in reloadable_fields}

        from invincat_cli.core.env_vars import (
            EXTRA_SKILLS_DIRS,
            LANGSMITH_PROJECT,
            SHELL_ALLOW_LIST,
        )

        try:
            shell_allow_list = config_mod.parse_shell_allow_list(os.environ.get(SHELL_ALLOW_LIST))
        except ValueError:
            logger.warning(
                "Invalid %s during reload; keeping previous value",
                SHELL_ALLOW_LIST,
            )
            shell_allow_list = previous["shell_allow_list"]

        try:
            from invincat_cli.project_utils import find_project_root

            project_root = find_project_root(start_path)
        except OSError:
            logger.warning(
                "Could not detect project root during reload; keeping previous value"
            )
            project_root = previous["project_root"]

        from invincat_cli.model_config import resolve_env_var

        refreshed = {
            "openai_api_key": resolve_env_var("OPENAI_API_KEY"),
            "anthropic_api_key": resolve_env_var("ANTHROPIC_API_KEY"),
            "google_api_key": resolve_env_var("GOOGLE_API_KEY"),
            "nvidia_api_key": resolve_env_var("NVIDIA_API_KEY"),
            "tavily_api_key": resolve_env_var("TAVILY_API_KEY"),
            "google_cloud_project": resolve_env_var("GOOGLE_CLOUD_PROJECT"),
            "deepagents_langchain_project": resolve_env_var(LANGSMITH_PROJECT),
            "project_root": project_root,
            "shell_allow_list": shell_allow_list,
            "extra_skills_dirs": config_mod._parse_extra_skills_dirs(
                os.environ.get(EXTRA_SKILLS_DIRS),
                config_mod._read_config_toml_skills_dirs(),
            ),
        }

        for field, value in refreshed.items():
            setattr(self, field, value)

        # Sync the LANGSMITH_PROJECT env var so LangSmith tracing picks up
        # the change
        new_project = refreshed["deepagents_langchain_project"]
        if new_project:
            os.environ["LANGSMITH_PROJECT"] = new_project
        elif previous["deepagents_langchain_project"]:
            # Override was previously active but new value is unset; restore.
            if config_mod._original_langsmith_project:
                os.environ["LANGSMITH_PROJECT"] = config_mod._original_langsmith_project
            else:
                os.environ.pop("LANGSMITH_PROJECT", None)

        def _display(field: str, value: object) -> str:
            if field in api_key_fields:
                return "set" if value else "unset"
            return str(value)

        changes: list[str] = []
        for field in reloadable_fields:
            old_value = previous[field]
            new_value = refreshed[field]
            if old_value != new_value:
                changes.append(
                    f"{field}: {_display(field, old_value)} -> "
                    f"{_display(field, new_value)}"
                )
        return changes

    @property
    def has_openai(self) -> bool:
        """Check if OpenAI API key is configured."""
        return self.openai_api_key is not None

    @property
    def has_anthropic(self) -> bool:
        """Check if Anthropic API key is configured."""
        return self.anthropic_api_key is not None

    @property
    def has_google(self) -> bool:
        """Check if Google API key is configured."""
        return self.google_api_key is not None

    @property
    def has_nvidia(self) -> bool:
        """Check if NVIDIA API key is configured."""
        return self.nvidia_api_key is not None

    @property
    def has_vertex_ai(self) -> bool:
        """Check if VertexAI is available (Google Cloud project set, no API key).

        VertexAI uses Application Default Credentials (ADC) for authentication,
        so if GOOGLE_CLOUD_PROJECT is set and GOOGLE_API_KEY is not, we assume
        VertexAI.
        """
        return self.google_cloud_project is not None and self.google_api_key is None

    @property
    def has_tavily(self) -> bool:
        """Check if Tavily API key is configured."""
        return self.tavily_api_key is not None

    @property
    def user_deepagents_dir(self) -> Path:
        """Get the base user-level .invincat directory.

        Returns:
            Path to ~/.invincat
        """
        return _home() / ".invincat"

    @staticmethod
    def _is_valid_agent_name(agent_name: str) -> bool:
        """Validate to prevent invalid filesystem paths and security issues.

        Returns:
            True if the agent name is valid, False otherwise.
        """
        if not agent_name or not agent_name.strip():
            return False
        # Allow only alphanumeric, hyphens, underscores, and whitespace
        return bool(re.match(r"^[a-zA-Z0-9_\-\s]+$", agent_name))

    def get_agent_dir(self, agent_name: str) -> Path:
        """Get the global agent directory path.

        Args:
            agent_name: Name of the agent

        Returns:
            Path to ~/.invincat/{agent_name}

        Raises:
            ValueError: If the agent name contains invalid characters.
        """
        if not self._is_valid_agent_name(agent_name):
            msg = (
                f"Invalid agent name: {agent_name!r}. Agent names can only "
                "contain letters, numbers, hyphens, underscores, and spaces."
            )
            raise ValueError(msg)
        return _home() / ".invincat" / agent_name

    def ensure_agent_dir(self, agent_name: str) -> Path:
        """Ensure the global agent directory exists and return its path.

        Args:
            agent_name: Name of the agent

        Returns:
            Path to ~/.invincat/{agent_name}

        Raises:
            ValueError: If the agent name contains invalid characters.
        """
        if not self._is_valid_agent_name(agent_name):
            msg = (
                f"Invalid agent name: {agent_name!r}. Agent names can only "
                "contain letters, numbers, hyphens, underscores, and spaces."
            )
            raise ValueError(msg)
        agent_dir = self.get_agent_dir(agent_name)
        agent_dir.mkdir(parents=True, exist_ok=True)
        return agent_dir

    def get_user_skills_dir(self, agent_name: str) -> Path:
        """Get user-level skills directory path for a specific agent.

        Args:
            agent_name: Name of the agent

        Returns:
            Path to ~/.invincat/{agent_name}/skills/
        """
        return self.get_agent_dir(agent_name) / "skills"

    def ensure_user_skills_dir(self, agent_name: str) -> Path:
        """Ensure user-level skills directory exists and return its path.

        Args:
            agent_name: Name of the agent

        Returns:
            Path to ~/.invincat/{agent_name}/skills/
        """
        skills_dir = self.get_user_skills_dir(agent_name)
        skills_dir.mkdir(parents=True, exist_ok=True)
        return skills_dir

    def get_project_skills_dir(self) -> Path | None:
        """Get project-level skills directory path.

        Returns:
            Path to {project_root}/.invincat/skills/, or None if not in a project
        """
        if not self.project_root:
            return None
        return self.project_root / ".invincat" / "skills"

    def ensure_project_skills_dir(self) -> Path | None:
        """Ensure project-level skills directory exists and return its path.

        Returns:
            Path to {project_root}/.invincat/skills/, or None if not in a project
        """
        if not self.project_root:
            return None
        skills_dir = self.get_project_skills_dir()
        if skills_dir is None:
            return None
        skills_dir.mkdir(parents=True, exist_ok=True)
        return skills_dir

    @property
    def user_agents_dir(self) -> Path:
        """Get the base user-level `.agents` directory (`~/.agents`).

        Returns:
            Path to `~/.agents`
        """
        return _home() / ".agents"

    def get_user_agent_skills_dir(self) -> Path:
        """Get user-level `~/.agents/skills/` directory.

        This is a generic alias path for skills that is tool-agnostic.

        Returns:
            Path to `~/.agents/skills/`
        """
        return self.user_agents_dir / "skills"

    def ensure_user_agent_skills_dir(self) -> Path:
        """Ensure user-level `~/.agents/skills/` exists and return its path.

        Returns:
            Path to `~/.agents/skills/`
        """
        skills_dir = self.get_user_agent_skills_dir()
        skills_dir.mkdir(parents=True, exist_ok=True)
        return skills_dir

    def get_project_agent_skills_dir(self) -> Path | None:
        """Get project-level `.agents/skills/` directory.

        This is a generic alias path for skills that is tool-agnostic.

        Returns:
            Path to `{project_root}/.agents/skills/`, or `None` if not in a project
        """
        if not self.project_root:
            return None
        return self.project_root / ".agents" / "skills"

    @staticmethod
    def get_user_claude_skills_dir() -> Path:
        """Get user-level `~/.claude/skills/` directory (experimental).

        Convenience bridge for cross-tool skill sharing with Claude Code.
        This is experimental and may be removed.

        Returns:
            Path to `~/.claude/skills/`
        """
        return _home() / ".claude" / "skills"

    def get_project_claude_skills_dir(self) -> Path | None:
        """Get project-level `.claude/skills/` directory (experimental).

        Convenience bridge for cross-tool skill sharing with Claude Code.
        This is experimental and may be removed.

        Returns:
            Path to `{project_root}/.claude/skills/`, or `None` if not in a project.
        """
        if not self.project_root:
            return None
        return self.project_root / ".claude" / "skills"

    @staticmethod
    def get_built_in_skills_dir() -> Path:
        """Get the directory containing built-in skills that ship with the CLI.

        Returns:
            Path to the `built_in_skills/` directory within the package.
        """
        return Path(__file__).resolve().parents[1] / "built_in_skills"

    def get_extra_skills_dirs(self) -> list[Path]:
        """Get user-configured extra skill directories.

        Set via `DEEPAGENTS_CLI_EXTRA_SKILLS_DIRS` (colon-separated paths) or
        `[skills].extra_allowed_dirs` in `~/.invincat/config.toml`.

        Returns:
            List of extra skill directory paths, or empty list if not configured.
        """
        return self.extra_skills_dirs or []
