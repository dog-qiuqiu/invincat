"""Daytona, Modal, and Runloop sandbox provider implementations."""

from __future__ import annotations

import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from invincat_cli.integrations.sandbox_provider import (
    SandboxNotFoundError,
    SandboxProvider,
)

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)


def _import_provider_module(module_name: str, *, provider: str, package: str) -> Any:
    from invincat_cli.integrations import sandbox_factory as _factory

    return _factory._import_provider_module(
        module_name, provider=provider, package=package
    )


class _DaytonaProvider(SandboxProvider):
    """Daytona sandbox provider lifecycle management."""

    def __init__(self) -> None:
        daytona_module = _import_provider_module(
            "daytona",
            provider="daytona",
            package="langchain-daytona",
        )

        from invincat_cli.model_config import resolve_env_var

        api_key = resolve_env_var("DAYTONA_API_KEY")
        if not api_key:
            msg = (
                "No Daytona API key found. Set DAYTONA_API_KEY "
                "or DEEPAGENTS_CLI_DAYTONA_API_KEY."
            )
            raise ValueError(msg)
        self._client = daytona_module.Daytona(
            daytona_module.DaytonaConfig(
                api_key=api_key,
                api_url=resolve_env_var("DAYTONA_API_URL"),
            )
        )

    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        timeout: int = 180,
        **kwargs: Any,  # noqa: ARG002
    ) -> SandboxBackendProtocol:
        """Get or create a Daytona sandbox."""
        daytona_backend = _import_provider_module(
            "langchain_daytona",
            provider="daytona",
            package="langchain-daytona",
        )

        if sandbox_id:
            msg = (
                "Connecting to existing Daytona sandbox by ID not yet supported. "
                "Create a new sandbox by omitting sandbox_id parameter."
            )
            raise NotImplementedError(msg)

        sandbox = self._client.create()
        last_exc: Exception | None = None
        for _ in range(timeout // 2):
            try:
                result = sandbox.process.exec("echo ready", timeout=5)
                if result.exit_code == 0:
                    break
            except Exception as exc:  # noqa: BLE001  # Transient failures expected during readiness polling
                last_exc = exc
            time.sleep(2)
        else:
            with contextlib.suppress(Exception):
                sandbox.delete()
            detail = f" Last error: {last_exc}" if last_exc else ""
            msg = f"Daytona sandbox failed to start within {timeout} seconds.{detail}"
            raise RuntimeError(msg)

        return daytona_backend.DaytonaSandbox(sandbox=sandbox)

    def delete(self, *, sandbox_id: str, **kwargs: Any) -> None:  # noqa: ARG002
        """Delete a Daytona sandbox by id."""
        sandbox = self._client.get(sandbox_id)
        self._client.delete(sandbox)


class _ModalProvider(SandboxProvider):
    """Modal sandbox provider lifecycle management."""

    def __init__(self) -> None:
        self._modal = _import_provider_module(
            "modal",
            provider="modal",
            package="langchain-modal",
        )

        from invincat_cli.model_config import resolve_env_var

        token_id = resolve_env_var("MODAL_TOKEN_ID")
        token_secret = resolve_env_var("MODAL_TOKEN_SECRET")
        if token_id and token_secret:
            try:
                self._client = self._modal.Client.from_credentials(
                    token_id, token_secret
                )
            except Exception as exc:
                msg = (
                    "Failed to authenticate with Modal using "
                    "MODAL_TOKEN_ID / MODAL_TOKEN_SECRET "
                    "(or the DEEPAGENTS_CLI_-prefixed equivalents). "
                    "Verify your credentials are valid."
                )
                raise ValueError(msg) from exc
        elif token_id or token_secret:
            logger.warning(
                "Only one of MODAL_TOKEN_ID / MODAL_TOKEN_SECRET is set; "
                "both are required for explicit credential auth. "
                "Falling back to default Modal authentication.",
            )
            self._client = None
        else:
            self._client = None

        lookup_kwargs: dict[str, Any] = {
            "name": "deepagents-sandbox",
            "create_if_missing": True,
        }
        if self._client is not None:
            lookup_kwargs["client"] = self._client
        self._app = self._modal.App.lookup(**lookup_kwargs)

    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        timeout: int = 180,
        **kwargs: Any,  # noqa: ARG002
    ) -> SandboxBackendProtocol:
        """Get or create a Modal sandbox."""
        modal_backend = _import_provider_module(
            "langchain_modal",
            provider="modal",
            package="langchain-modal",
        )

        client_kwargs: dict[str, Any] = {}
        if self._client is not None:
            client_kwargs["client"] = self._client

        if sandbox_id:
            sandbox = self._modal.Sandbox.from_id(
                sandbox_id=sandbox_id,
                app=self._app,
                **client_kwargs,
            )
        else:
            sandbox = self._modal.Sandbox.create(
                app=self._app, workdir="/workspace", **client_kwargs
            )
            last_exc: Exception | None = None
            for _ in range(timeout // 2):
                if sandbox.poll() is not None:
                    msg = "Modal sandbox terminated unexpectedly during startup"
                    raise RuntimeError(msg)
                try:
                    process = sandbox.exec("echo", "ready", timeout=5)
                    process.wait()
                    if process.returncode == 0:
                        break
                except Exception as exc:  # noqa: BLE001  # Transient failures expected during readiness polling
                    last_exc = exc
                time.sleep(2)
            else:
                sandbox.terminate()
                detail = f" Last error: {last_exc}" if last_exc else ""
                msg = f"Modal sandbox failed to start within {timeout} seconds.{detail}"
                raise RuntimeError(msg)

        return modal_backend.ModalSandbox(sandbox=sandbox)

    def delete(self, *, sandbox_id: str, **kwargs: Any) -> None:  # noqa: ARG002
        """Terminate a Modal sandbox by id."""
        del_kwargs: dict[str, Any] = {"sandbox_id": sandbox_id, "app": self._app}
        if self._client is not None:
            del_kwargs["client"] = self._client
        sandbox = self._modal.Sandbox.from_id(**del_kwargs)
        sandbox.terminate()


class _RunloopProvider(SandboxProvider):
    """Runloop sandbox provider lifecycle management."""

    def __init__(self) -> None:
        runloop_module = _import_provider_module(
            "runloop_api_client",
            provider="runloop",
            package="langchain-runloop",
        )

        from invincat_cli.model_config import resolve_env_var

        api_key = resolve_env_var("RUNLOOP_API_KEY")
        if not api_key:
            msg = (
                "No Runloop API key found. Set RUNLOOP_API_KEY "
                "or DEEPAGENTS_CLI_RUNLOOP_API_KEY."
            )
            raise ValueError(msg)
        self._client = runloop_module.Runloop(bearer_token=api_key)

    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        timeout: int = 180,
        **kwargs: Any,  # noqa: ARG002
    ) -> SandboxBackendProtocol:
        """Get or create a Runloop devbox."""
        runloop_backend = _import_provider_module(
            "langchain_runloop",
            provider="runloop",
            package="langchain-runloop",
        )
        runloop_sdk = _import_provider_module(
            "runloop_api_client.sdk",
            provider="runloop",
            package="langchain-runloop",
        )

        if sandbox_id:
            try:
                self._client.devboxes.retrieve(id=sandbox_id)
            except KeyError as e:
                raise SandboxNotFoundError(sandbox_id) from e
        else:
            view = self._client.devboxes.create()
            sandbox_id = view.id
            for _ in range(timeout // 2):
                status = self._client.devboxes.retrieve(id=sandbox_id)
                if status.status == "running":
                    break
                time.sleep(2)
            else:
                self._client.devboxes.shutdown(id=sandbox_id)
                msg = f"Devbox failed to start within {timeout} seconds"
                raise RuntimeError(msg)

        devbox = runloop_sdk.Devbox(self._client, sandbox_id)
        return runloop_backend.RunloopSandbox(devbox=devbox)

    def delete(self, *, sandbox_id: str, **kwargs: Any) -> None:  # noqa: ARG002
        """Shut down a Runloop devbox by id."""
        self._client.devboxes.shutdown(id=sandbox_id)
