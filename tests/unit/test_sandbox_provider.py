from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from invincat_cli.integrations.sandbox_provider import SandboxError, SandboxProvider


class RecordingProvider(SandboxProvider):
    def __init__(self) -> None:
        self.created: list[tuple[str | None, dict[str, object]]] = []
        self.deleted: list[tuple[str, dict[str, object]]] = []
        self.backend = SimpleNamespace(id="sandbox-1")

    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        **kwargs: object,
    ) -> SimpleNamespace:
        self.created.append((sandbox_id, kwargs))
        return self.backend

    def delete(self, *, sandbox_id: str, **kwargs: object) -> None:
        self.deleted.append((sandbox_id, kwargs))


class SuperProvider(SandboxProvider):
    def get_or_create(
        self,
        *,
        sandbox_id: str | None = None,
        **kwargs: object,
    ) -> object:
        return super().get_or_create(sandbox_id=sandbox_id, **kwargs)

    def delete(self, *, sandbox_id: str, **kwargs: object) -> None:
        return super().delete(sandbox_id=sandbox_id, **kwargs)


def test_sandbox_error_exposes_original_exception_cause() -> None:
    cause = RuntimeError("root cause")

    try:
        raise SandboxError("wrapped") from cause
    except SandboxError as exc:
        assert exc.original_exc is cause

    assert SandboxError("plain").original_exc is None


def test_sandbox_provider_base_methods_raise_not_implemented() -> None:
    provider = SuperProvider()

    with pytest.raises(NotImplementedError):
        provider.get_or_create()

    with pytest.raises(NotImplementedError):
        provider.delete(sandbox_id="sandbox-1")


def test_sandbox_provider_async_wrappers_delegate_to_sync_methods() -> None:
    async def run() -> None:
        provider = RecordingProvider()

        backend = await provider.aget_or_create(sandbox_id="sandbox-1", image="py")
        await provider.adelete(sandbox_id="sandbox-1", force=True)

        assert backend is provider.backend
        assert provider.created == [("sandbox-1", {"image": "py"})]
        assert provider.deleted == [("sandbox-1", {"force": True})]

    asyncio.run(run())
