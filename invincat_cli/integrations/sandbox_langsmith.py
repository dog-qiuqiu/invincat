"""LangSmith sandbox provider implementation."""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING, Any

from invincat_cli.integrations.sandbox_provider import SandboxProvider

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol
    from langsmith.sandbox import SandboxTemplate


_LANGSMITH_DEFAULT_TEMPLATE = "deepagents-cli"
"""Default LangSmith sandbox template name used when no template is specified."""

_LANGSMITH_DEFAULT_IMAGE = "python:3"
"""Default Docker image for LangSmith sandboxes when no image is provided."""


class _LangSmithProvider(SandboxProvider):
    """LangSmith sandbox provider implementation."""

    def __init__(self, api_key: str | None = None) -> None:
        from langsmith.sandbox import SandboxClient

        from invincat_cli.model_config import resolve_env_var

        self._api_key = (
            api_key
            or resolve_env_var("LANGSMITH_SANDBOX_API_KEY")
            or resolve_env_var("LANGSMITH_API_KEY")
        )
        if not self._api_key:
            msg = (
                "No LangSmith sandbox API key found. Set "
                "LANGSMITH_SANDBOX_API_KEY or LANGSMITH_API_KEY "
                "(or the DEEPAGENTS_CLI_-prefixed equivalents)."
            )
            raise ValueError(msg)
        self._client: SandboxClient = SandboxClient(api_key=self._api_key)

    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        timeout: int = 180,
        template: str | None = None,
        template_image: str | None = None,
        **kwargs: Any,
    ) -> SandboxBackendProtocol:
        """Get existing or create new LangSmith sandbox."""
        from deepagents.backends.langsmith import LangSmithSandbox

        if kwargs:
            msg = f"Received unsupported arguments: {list(kwargs.keys())}"
            raise TypeError(msg)
        if sandbox_id:
            try:
                sandbox = self._client.get_sandbox(name=sandbox_id)
            except Exception as e:
                msg = f"Failed to connect to existing sandbox '{sandbox_id}': {e}"
                raise RuntimeError(msg) from e
            return LangSmithSandbox(sandbox)

        resolved_template_name, resolved_image_name = self._resolve_template(
            template, template_image
        )
        self._ensure_template(resolved_template_name, resolved_image_name)

        try:
            sandbox = self._client.create_sandbox(
                template_name=resolved_template_name, timeout=timeout
            )
        except Exception as e:
            msg = (
                f"Failed to create sandbox from template "
                f"'{resolved_template_name}': {e}"
            )
            raise RuntimeError(msg) from e

        for _ in range(timeout // 2):
            try:
                result = sandbox.run("echo ready", timeout=5)
                if result.exit_code == 0:
                    break
            except Exception:  # noqa: S110, BLE001  # Sandbox not ready yet, continue polling
                pass
            time.sleep(2)
        else:
            with contextlib.suppress(Exception):
                self._client.delete_sandbox(sandbox.name)
            msg = f"LangSmith sandbox failed to start within {timeout} seconds"
            raise RuntimeError(msg)

        return LangSmithSandbox(sandbox)

    def delete(self, *, sandbox_id: str, **kwargs: Any) -> None:  # noqa: ARG002
        """Delete a LangSmith sandbox."""
        self._client.delete_sandbox(sandbox_id)

    @staticmethod
    def _resolve_template(
        template: SandboxTemplate | str | None,
        template_image: str | None = None,
    ) -> tuple[str, str]:
        """Resolve template name and image from kwargs."""
        resolved_image = template_image or _LANGSMITH_DEFAULT_IMAGE
        if template is None:
            return _LANGSMITH_DEFAULT_TEMPLATE, resolved_image
        if isinstance(template, str):
            return template, resolved_image
        if template_image is None and template.image:
            resolved_image = template.image
        return template.name, resolved_image

    def _ensure_template(
        self,
        template_name: str,
        template_image: str,
    ) -> None:
        """Ensure template exists, creating it if needed."""
        from langsmith.sandbox import ResourceNotFoundError

        try:
            self._client.get_template(template_name)
        except ResourceNotFoundError as e:
            if e.resource_type != "template":
                msg = f"Unexpected resource not found: {e}"
                raise RuntimeError(msg) from e
            try:
                self._client.create_template(name=template_name, image=template_image)
            except Exception as create_err:
                msg = f"Failed to create template '{template_name}': {create_err}"
                raise RuntimeError(msg) from create_err
        except Exception as e:
            msg = f"Failed to check template '{template_name}': {e}"
            raise RuntimeError(msg) from e
