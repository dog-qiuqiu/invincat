"""LangGraph server lifecycle management for the CLI.

Handles starting/stopping a `langgraph dev` server process and generating the
required `langgraph.json` configuration file.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import subprocess  # noqa: S404
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

logger = logging.getLogger(__name__)

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 2024
_HEALTH_POLL_INTERVAL_LOCAL = 0.1
_HEALTH_POLL_INTERVAL_REMOTE = 0.3
_HEALTH_TIMEOUT = 60
_SHUTDOWN_TIMEOUT = 5


def _port_in_use(host: str, port: int) -> bool:
    """Check if a port is already in use."""
    from invincat_cli.server.app_network import port_in_use

    return port_in_use(host, port)


def _find_free_port(host: str) -> int:
    """Find a free port on the given host."""
    from invincat_cli.server.app_network import find_free_port

    return find_free_port(host)


def get_server_url(host: str = _DEFAULT_HOST, port: int = _DEFAULT_PORT) -> str:
    """Build the server base URL.

    Args:
        host: Server host.
        port: Server port.

    Returns:
        Base URL string.
    """
    return f"http://{host}:{port}"


def generate_langgraph_json(
    output_dir: str | Path,
    *,
    graph_ref: str = "./server_graph.py:graph",
    env_file: str | None = None,
    checkpointer_path: str | None = None,
) -> Path:
    """Generate a `langgraph.json` config file for `langgraph dev`."""
    from invincat_cli.server.app_config import generate_langgraph_json as generate

    return generate(
        output_dir,
        graph_ref=graph_ref,
        env_file=env_file,
        checkpointer_path=checkpointer_path,
    )


# ---------------------------------------------------------------------------
# Scoped env-var management
# ---------------------------------------------------------------------------


def _scoped_env_overrides(
    overrides: dict[str, str],
) -> Iterator[None]:
    """Apply env-var overrides, rolling back only on exception."""
    from invincat_cli.server.app_env import scoped_env_overrides

    return scoped_env_overrides(overrides, os_module=os)


# ---------------------------------------------------------------------------
# Health checking
# ---------------------------------------------------------------------------


async def wait_for_server_healthy(
    url: str,
    *,
    timeout: float = _HEALTH_TIMEOUT,  # noqa: ASYNC109
    process: subprocess.Popen | None = None,
    read_log: Callable[[], str] | None = None,
    local: bool = False,
) -> None:
    """Poll a LangGraph server health endpoint until it responds."""
    from invincat_cli.server.app_health import (
        wait_for_server_healthy as wait_for_health,
    )

    await wait_for_health(
        url,
        timeout=timeout,
        process=process,
        read_log=read_log,
        local=local,
        local_poll_interval=_HEALTH_POLL_INTERVAL_LOCAL,
        remote_poll_interval=_HEALTH_POLL_INTERVAL_REMOTE,
        asyncio_module=asyncio,
        time_module=time,
        logger=logger,
    )


# ---------------------------------------------------------------------------
# Server command / env construction
# ---------------------------------------------------------------------------


def _build_server_cmd(config_path: Path, *, host: str, port: int) -> list[str]:
    """Build the `langgraph dev` command line."""
    from invincat_cli.server.app_config import build_server_cmd

    return build_server_cmd(config_path, host=host, port=port)


def _build_server_env(
    config_path: Path | None = None,
) -> dict[str, str]:
    """Build the environment dict for the server subprocess."""
    from invincat_cli.server.app_config import build_server_env

    return build_server_env(config_path=config_path)


# ---------------------------------------------------------------------------
# ServerProcess
# ---------------------------------------------------------------------------


class ServerProcess:
    """Manages a `langgraph dev` server subprocess.

    Focuses on subprocess lifecycle (start, stop, restart) and health checking.
    Env-var management for restarts (e.g. configuration changes requiring a full
    restart) is handled by `_scoped_env_overrides`, keeping this class focused
    on process management.
    """

    def __init__(
        self,
        *,
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
        config_dir: str | Path | None = None,
        owns_config_dir: bool = False,
    ) -> None:
        """Initialize server process manager.

        Args:
            host: Host to bind the server to.
            port: Initial port to bind the server to.

                May be reassigned automatically by `start()` if the port is
                already in use.
            config_dir: Directory containing `langgraph.json`.
            owns_config_dir: When `True`, the server will delete `config_dir`
                on `stop()`.
        """
        self.host = host
        self.port = port
        self.config_dir = Path(config_dir) if config_dir else None
        self._owns_config_dir = owns_config_dir
        self._process: subprocess.Popen | None = None
        self._temp_dir: tempfile.TemporaryDirectory | None = None
        self._log_file: tempfile.NamedTemporaryFile | None = None  # type: ignore[type-arg]
        self._env_overrides: dict[str, str] = {}

    @property
    def url(self) -> str:
        """Server base URL."""
        return get_server_url(self.host, self.port)

    @property
    def running(self) -> bool:
        """Whether the server process is running."""
        return self._process is not None and self._process.poll() is None

    def _read_log_file(self) -> str:
        """Read the server log file contents.

        Returns:
            Log file contents as a string (may be empty).
        """
        if self._log_file is None:
            return ""
        try:
            self._log_file.flush()
            return Path(self._log_file.name).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            logger.warning(
                "Failed to read server log file %s",
                self._log_file.name,
                exc_info=True,
            )
            return ""

    def read_log_tail(self, max_chars: int = 3000) -> str:
        """Read the tail of the server log file.

        Args:
            max_chars: Maximum characters from the end of the log.

        Returns:
            Tail string, or empty string when logs are unavailable.
        """
        if max_chars <= 0:
            return ""
        content = self._read_log_file()
        if not content:
            return ""
        return content[-max_chars:]

    async def start(
        self,
        *,
        timeout: float = _HEALTH_TIMEOUT,  # noqa: ASYNC109
    ) -> None:
        """Start the `langgraph dev` server and wait for it to be healthy.

        Args:
            timeout: Max seconds to wait for the server to become healthy.

        Raises:
            RuntimeError: If the server fails to start or become healthy.
        """
        if self.running:
            return

        work_dir = self.config_dir
        if work_dir is None:
            self._temp_dir = tempfile.TemporaryDirectory(prefix="deepagents_server_")
            work_dir = Path(self._temp_dir.name)

        config_path = work_dir / "langgraph.json"
        if not config_path.exists():
            msg = (
                f"langgraph.json not found in {work_dir}. "
                "Call generate_langgraph_json() first."
            )
            raise RuntimeError(msg)

        if _port_in_use(self.host, self.port):
            self.port = _find_free_port(self.host)
            logger.info("Default port in use, using port %d instead", self.port)

        cmd = _build_server_cmd(config_path, host=self.host, port=self.port)
        env = _build_server_env(config_path=config_path)

        logger.info("Starting langgraph dev server: %s", " ".join(cmd))
        self._log_file = tempfile.NamedTemporaryFile(  # noqa: SIM115
            prefix="deepagents_server_log_",
            suffix=".txt",
            delete=False,
            mode="w",
            encoding="utf-8",
        )
        self._process = subprocess.Popen(  # noqa: S603, ASYNC220
            cmd,
            cwd=str(work_dir),
            env=env,
            stdout=self._log_file,
            stderr=subprocess.STDOUT,
        )

        try:
            await wait_for_server_healthy(
                self.url,
                timeout=timeout,
                process=self._process,
                read_log=self._read_log_file,
                local=True,
            )
        except Exception:
            self.stop()
            raise

    def _stop_process(self) -> None:
        """Stop only the server subprocess and its log file.

        Unlike `stop()`, this does NOT clean up the config directory or temp
        directory, so the server can be restarted with the same config.
        """
        if self._process is None:
            return

        if self._process.poll() is None:
            logger.info("Stopping langgraph dev server (pid=%d)", self._process.pid)
            try:
                self._process.send_signal(signal.SIGTERM)
                self._process.wait(timeout=_SHUTDOWN_TIMEOUT)
            except subprocess.TimeoutExpired:
                logger.warning("Server did not stop gracefully, killing")
                self._process.kill()
                try:
                    self._process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    logger.warning(
                        "Server process pid=%d did not exit after SIGKILL",
                        self._process.pid,
                    )
            except OSError:
                logger.warning("Error stopping server", exc_info=True)

        self._process = None

        if self._log_file is not None:
            try:
                self._log_file.close()
                Path(self._log_file.name).unlink()
            except OSError:
                logger.debug("Failed to clean up log file", exc_info=True)
            self._log_file = None

    def stop(self) -> None:
        """Stop the server process and clean up all resources."""
        self._stop_process()

        if self._temp_dir is not None:
            try:
                self._temp_dir.cleanup()
            except OSError:
                logger.debug("Failed to clean up temp dir", exc_info=True)
            self._temp_dir = None

        if self._owns_config_dir and self.config_dir is not None:
            import shutil

            try:
                shutil.rmtree(self.config_dir)
            except OSError:
                logger.debug(
                    "Failed to clean up config dir %s", self.config_dir, exc_info=True
                )
            self._owns_config_dir = False

    def update_env(self, **overrides: str) -> None:
        """Stage env var overrides to apply on the next `restart()`.

        These are applied to `os.environ` immediately before the subprocess
        starts, keeping mutation scoped to the restart call.

        Args:
            **overrides: Key/value env var pairs
                (e.g., `DEEPAGENTS_CLI_SERVER_MODEL="anthropic:claude-sonnet-4-6"`).
        """
        self._env_overrides.update(overrides)

    async def restart(self, *, timeout: float = _HEALTH_TIMEOUT) -> None:  # noqa: ASYNC109
        """Restart the server process, reusing the existing config directory.

        Stops the subprocess, then starts a new one. Any env overrides staged
        via `update_env()` are applied within a `_scoped_env_overrides` context
        manager so that failures automatically roll back the environment to the
        last known-good state.

        Args:
            timeout: Max seconds to wait for the server to become healthy.
        """
        logger.info("Restarting langgraph dev server")
        self._stop_process()

        with _scoped_env_overrides(self._env_overrides):
            await self.start(timeout=timeout)

        self._env_overrides.clear()

    async def __aenter__(self) -> Self:
        """Async context manager entry.

        Returns:
            The server process instance.
        """
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        """Async context manager exit."""
        self.stop()
