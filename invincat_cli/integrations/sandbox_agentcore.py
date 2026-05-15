"""AgentCore Code Interpreter sandbox provider implementation."""

from __future__ import annotations

import contextlib
import logging
import os
from typing import TYPE_CHECKING, Any

from invincat_cli.integrations.sandbox_provider import SandboxProvider

if TYPE_CHECKING:
    from deepagents.backends.protocol import SandboxBackendProtocol

logger = logging.getLogger(__name__)


def _import_provider_module(module_name: str, *, provider: str, package: str) -> Any:
    from invincat_cli.integrations import sandbox_factory as _factory

    return _factory._import_provider_module(
        module_name, provider=provider, package=package
    )


class _AgentCoreProvider(SandboxProvider):
    """AgentCore Code Interpreter sandbox provider."""

    def __init__(self, region: str | None = None) -> None:
        self._region = region or os.environ.get(
            "AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-west-2")
        )

        try:
            import boto3  # ty: ignore[unresolved-import]

            session = boto3.Session()
            credentials = session.get_credentials()
            if credentials is None:
                msg = (
                    "AWS credentials not found. Configure via "
                    "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_SESSION_TOKEN, "
                    "~/.aws/credentials, or an IAM role."
                )
                raise ValueError(msg)  # noqa: TRY301  # intentional raise for early credential validation
        except ImportError:
            logger.debug("boto3 not installed; skipping credential pre-check")
        except ValueError:
            raise
        except Exception:
            logger.warning(
                "AWS credential pre-validation failed - the session may "
                "fail to start. Check your AWS configuration.",
                exc_info=True,
            )

        self._active_interpreters: dict[str, Any] = {}

    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        **kwargs: Any,  # noqa: ARG002
    ) -> SandboxBackendProtocol:
        """Create a new AgentCore Code Interpreter session."""
        if sandbox_id:
            msg = (
                "AgentCore does not support reconnecting to existing sessions. "
                "Remove the --sandbox-id option."
            )
            raise NotImplementedError(msg)

        agentcore_module = _import_provider_module(
            "bedrock_agentcore.tools.code_interpreter_client",
            provider="agentcore",
            package="langchain-agentcore-codeinterpreter",
        )
        agentcore_backend = _import_provider_module(
            "langchain_agentcore_codeinterpreter",
            provider="agentcore",
            package="langchain-agentcore-codeinterpreter",
        )

        interpreter = agentcore_module.CodeInterpreter(
            region=self._region,
            integration_source="deepagents-cli",
        )
        try:
            interpreter.start()
        except Exception:
            with contextlib.suppress(Exception):
                interpreter.stop()
            raise

        backend = agentcore_backend.AgentCoreSandbox(interpreter=interpreter)
        self._active_interpreters[backend.id] = interpreter
        return backend

    def delete(self, *, sandbox_id: str, **kwargs: Any) -> None:  # noqa: ARG002
        """Stop an AgentCore session."""
        interpreter = self._active_interpreters.pop(sandbox_id, None)
        if interpreter:
            try:
                interpreter.stop()
                logger.info("AgentCore session %s stopped", sandbox_id)
            except Exception:
                logger.warning(
                    "Failed to stop AgentCore session %s - the session may "
                    "still be running and incurring costs. Check the AWS "
                    "console to verify.",
                    sandbox_id,
                    exc_info=True,
                )
        else:
            logger.info(
                "AgentCore session %s not tracked (may have already expired)",
                sandbox_id,
            )
