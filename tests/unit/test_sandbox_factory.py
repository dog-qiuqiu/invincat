from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from invincat_cli.integrations import sandbox_factory


class FakeBackend:
    def __init__(self, *, sandbox_id: str = "sandbox-1", exit_code: int = 0) -> None:
        self.id = sandbox_id
        self.exit_code = exit_code
        self.commands: list[str] = []

    def execute(self, command: str) -> SimpleNamespace:
        self.commands.append(command)
        return SimpleNamespace(exit_code=self.exit_code, output="setup output")


class FakeProvider:
    def __init__(
        self,
        backend: FakeBackend,
        *,
        delete_error: Exception | None = None,
    ) -> None:
        self.backend = backend
        self.delete_error = delete_error
        self.created_with: list[str | None] = []
        self.deleted: list[str] = []

    def get_or_create(self, *, sandbox_id: str | None = None) -> FakeBackend:
        self.created_with.append(sandbox_id)
        return self.backend

    def delete(self, *, sandbox_id: str) -> None:
        self.deleted.append(sandbox_id)
        if self.delete_error is not None:
            raise self.delete_error


def test_default_working_dirs_and_provider_names() -> None:
    assert sandbox_factory._get_available_sandbox_types() == [
        "agentcore",
        "daytona",
        "langsmith",
        "modal",
        "runloop",
    ]
    assert sandbox_factory.get_default_working_dir("modal") == "/workspace"

    with pytest.raises(ValueError, match="Unknown sandbox provider"):
        sandbox_factory.get_default_working_dir("missing")


def test_import_provider_module_reports_extra_install_hint() -> None:
    with pytest.raises(ImportError, match="deepagents-cli\\[missing\\]"):
        sandbox_factory._import_provider_module(
            "definitely_missing_provider_module",
            provider="missing",
            package="package-name",
        )


def test_verify_sandbox_deps_skips_none_langsmith_and_unknown(monkeypatch) -> None:
    calls: list[str] = []

    def find_spec(name: str) -> object:
        calls.append(name)
        return object()

    monkeypatch.setattr(sandbox_factory.importlib.util, "find_spec", find_spec)

    sandbox_factory.verify_sandbox_deps("")
    sandbox_factory.verify_sandbox_deps("none")
    sandbox_factory.verify_sandbox_deps("langsmith")
    sandbox_factory.verify_sandbox_deps("unknown")
    sandbox_factory.verify_sandbox_deps("modal")

    assert calls == ["langchain_modal"]


def test_verify_sandbox_deps_raises_for_missing_or_invalid_spec(monkeypatch) -> None:
    monkeypatch.setattr(
        sandbox_factory.importlib.util,
        "find_spec",
        lambda _name: None,
    )

    with pytest.raises(ImportError, match="deepagents-cli\\[daytona\\]"):
        sandbox_factory.verify_sandbox_deps("daytona")

    def broken_find_spec(_name: str) -> object:
        raise ValueError("bad module")

    monkeypatch.setattr(sandbox_factory.importlib.util, "find_spec", broken_find_spec)

    with pytest.raises(ImportError, match="deepagents-cli\\[runloop\\]"):
        sandbox_factory.verify_sandbox_deps("runloop")


def test_run_sandbox_setup_expands_environment_and_reports_failures(
    monkeypatch,
    tmp_path: Path,
) -> None:
    script_path = tmp_path / "setup.sh"
    script_path.write_text("echo ${PROJECT_NAME}")
    monkeypatch.setenv("PROJECT_NAME", "demo")
    backend = FakeBackend()

    sandbox_factory._run_sandbox_setup(backend, str(script_path))

    assert "demo" in backend.commands[0]

    with pytest.raises(FileNotFoundError):
        sandbox_factory._run_sandbox_setup(backend, str(tmp_path / "missing.sh"))

    failing_backend = FakeBackend(exit_code=7)
    with pytest.raises(RuntimeError, match="Setup failed"):
        sandbox_factory._run_sandbox_setup(failing_backend, str(script_path))


def test_create_sandbox_cleans_up_only_created_sandboxes(monkeypatch) -> None:
    provider = FakeProvider(FakeBackend(sandbox_id="new-id"))
    monkeypatch.setattr(sandbox_factory, "_get_provider", lambda _name: provider)

    with sandbox_factory.create_sandbox("modal") as backend:
        assert backend.id == "new-id"

    assert provider.created_with == [None]
    assert provider.deleted == ["new-id"]

    setup_calls: list[tuple[FakeBackend, str]] = []
    setup_provider = FakeProvider(FakeBackend(sandbox_id="setup-id"))
    monkeypatch.setattr(sandbox_factory, "_get_provider", lambda _name: setup_provider)
    monkeypatch.setattr(
        sandbox_factory,
        "_run_sandbox_setup",
        lambda backend, path: setup_calls.append((backend, path)),
    )

    with sandbox_factory.create_sandbox("modal", setup_script_path="setup.sh"):
        pass

    assert setup_calls == [(setup_provider.backend, "setup.sh")]

    existing_provider = FakeProvider(FakeBackend(sandbox_id="existing"))
    monkeypatch.setattr(
        sandbox_factory,
        "_get_provider",
        lambda _name: existing_provider,
    )

    with sandbox_factory.create_sandbox("modal", sandbox_id="existing") as backend:
        assert backend.id == "existing"

    assert existing_provider.created_with == ["existing"]
    assert existing_provider.deleted == []


def test_create_sandbox_cleanup_errors_do_not_mask_body_errors(monkeypatch) -> None:
    provider = FakeProvider(
        FakeBackend(sandbox_id="new-id"),
        delete_error=RuntimeError("delete failed"),
    )
    monkeypatch.setattr(sandbox_factory, "_get_provider", lambda _name: provider)

    with pytest.raises(ValueError, match="body failed"):
        with sandbox_factory.create_sandbox("modal"):
            raise ValueError("body failed")

    assert provider.deleted == ["new-id"]


def test_langsmith_template_resolution() -> None:
    assert sandbox_factory._LangSmithProvider._resolve_template(None) == (
        sandbox_factory._LANGSMITH_DEFAULT_TEMPLATE,
        sandbox_factory._LANGSMITH_DEFAULT_IMAGE,
    )
    assert sandbox_factory._LangSmithProvider._resolve_template(
        "custom",
        "python:3.12",
    ) == ("custom", "python:3.12")
    assert sandbox_factory._LangSmithProvider._resolve_template(
        SimpleNamespace(name="template-object", image="python:3.11"),
    ) == ("template-object", "python:3.11")


def test_get_provider_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="Available providers"):
        sandbox_factory._get_provider("missing")


def test_get_provider_dispatches_known_providers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    markers = {
        "agentcore": object(),
        "daytona": object(),
        "langsmith": object(),
        "modal": object(),
        "runloop": object(),
    }
    monkeypatch.setattr(
        sandbox_factory, "_AgentCoreProvider", lambda: markers["agentcore"]
    )
    monkeypatch.setattr(sandbox_factory, "_DaytonaProvider", lambda: markers["daytona"])
    monkeypatch.setattr(
        sandbox_factory, "_LangSmithProvider", lambda: markers["langsmith"]
    )
    monkeypatch.setattr(sandbox_factory, "_ModalProvider", lambda: markers["modal"])
    monkeypatch.setattr(sandbox_factory, "_RunloopProvider", lambda: markers["runloop"])

    assert sandbox_factory._get_provider("agentcore") is markers["agentcore"]
    assert sandbox_factory._get_provider("daytona") is markers["daytona"]
    assert sandbox_factory._get_provider("langsmith") is markers["langsmith"]
    assert sandbox_factory._get_provider("modal") is markers["modal"]
    assert sandbox_factory._get_provider("runloop") is markers["runloop"]


def test_langsmith_provider_requires_api_key_and_initializes_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.model_config as model_config

    created: list[str] = []

    class FakeSandboxClient:
        def __init__(self, *, api_key: str) -> None:
            created.append(api_key)

    langsmith_sandbox = ModuleType("langsmith.sandbox")
    langsmith_sandbox.SandboxClient = FakeSandboxClient
    monkeypatch.setitem(sys.modules, "langsmith.sandbox", langsmith_sandbox)
    monkeypatch.setattr(model_config, "resolve_env_var", lambda _name: None)

    with pytest.raises(ValueError, match="No LangSmith sandbox API key"):
        sandbox_factory._LangSmithProvider()

    provider = sandbox_factory._LangSmithProvider(api_key="key-1")

    assert provider._api_key == "key-1"
    assert created == ["key-1"]


def test_langsmith_provider_ensure_template_create_and_error_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResourceNotFoundError(Exception):
        def __init__(self, resource_type: str) -> None:
            super().__init__(resource_type)
            self.resource_type = resource_type

    langsmith_sandbox = ModuleType("langsmith.sandbox")
    langsmith_sandbox.ResourceNotFoundError = FakeResourceNotFoundError
    monkeypatch.setitem(sys.modules, "langsmith.sandbox", langsmith_sandbox)

    class FakeClient:
        def __init__(self, resource_type: str = "template") -> None:
            self.resource_type = resource_type
            self.created: list[tuple[str, str]] = []

        def get_template(self, _name: str) -> None:
            raise FakeResourceNotFoundError(self.resource_type)

        def create_template(self, *, name: str, image: str) -> None:
            self.created.append((name, image))

    provider = sandbox_factory._LangSmithProvider.__new__(
        sandbox_factory._LangSmithProvider
    )
    provider._client = FakeClient()

    provider._ensure_template("template", "python:3")

    assert provider._client.created == [("template", "python:3")]

    provider._client = FakeClient(resource_type="sandbox")
    with pytest.raises(RuntimeError, match="Unexpected resource not found"):
        provider._ensure_template("template", "python:3")

    class BrokenClient:
        def get_template(self, _name: str) -> None:
            raise RuntimeError("boom")

    provider._client = BrokenClient()
    with pytest.raises(RuntimeError, match="Failed to check template"):
        provider._ensure_template("template", "python:3")


def test_langsmith_provider_rejects_kwargs_create_errors_and_deletes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_module = ModuleType("deepagents.backends.langsmith")
    backend_module.LangSmithSandbox = lambda sandbox: SimpleNamespace(
        id=sandbox.name, sandbox=sandbox
    )
    monkeypatch.setitem(sys.modules, "deepagents.backends.langsmith", backend_module)

    class FakeClient:
        def __init__(self) -> None:
            self.deleted: list[str] = []

        def create_sandbox(self, *, template_name: str, timeout: int) -> Any:
            raise RuntimeError(f"cannot create {template_name}/{timeout}")

        def delete_sandbox(self, name: str) -> None:
            self.deleted.append(name)

    provider = sandbox_factory._LangSmithProvider.__new__(
        sandbox_factory._LangSmithProvider
    )
    provider._client = FakeClient()
    monkeypatch.setattr(provider, "_ensure_template", lambda *_args: None)

    with pytest.raises(TypeError, match="unsupported arguments"):
        provider.get_or_create(extra=True)

    with pytest.raises(RuntimeError, match="Failed to create sandbox"):
        provider.get_or_create(template="template", timeout=3)

    provider.delete(sandbox_id="sandbox-1")
    assert provider._client.deleted == ["sandbox-1"]


def test_langsmith_provider_template_creation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResourceNotFoundError(Exception):
        resource_type = "template"

    langsmith_sandbox = ModuleType("langsmith.sandbox")
    langsmith_sandbox.ResourceNotFoundError = FakeResourceNotFoundError
    monkeypatch.setitem(sys.modules, "langsmith.sandbox", langsmith_sandbox)

    class BrokenCreateClient:
        def get_template(self, _name: str) -> None:
            raise FakeResourceNotFoundError()

        def create_template(self, *, name: str, image: str) -> None:
            raise RuntimeError(f"cannot create {name}:{image}")

    provider = sandbox_factory._LangSmithProvider.__new__(
        sandbox_factory._LangSmithProvider
    )
    provider._client = BrokenCreateClient()

    with pytest.raises(RuntimeError, match="Failed to create template"):
        provider._ensure_template("template", "python:3")


def test_langsmith_provider_get_or_create_existing_new_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrapped: list[Any] = []

    class FakeLangSmithSandbox:
        def __init__(self, sandbox: Any) -> None:
            self.sandbox = sandbox
            self.id = sandbox.name
            wrapped.append(sandbox)

    backend_module = ModuleType("deepagents.backends.langsmith")
    backend_module.LangSmithSandbox = FakeLangSmithSandbox
    monkeypatch.setitem(sys.modules, "deepagents.backends.langsmith", backend_module)
    monkeypatch.setattr(sandbox_factory.time, "sleep", lambda _seconds: None)

    class ReadySandbox:
        name = "new"

        def __init__(self, *, ready: bool = True) -> None:
            self.ready = ready
            self.runs = 0

        def run(self, _command: str, *, timeout: int) -> SimpleNamespace:
            self.runs += 1
            return SimpleNamespace(exit_code=0 if self.ready else 1)

    class FakeClient:
        def __init__(self) -> None:
            self.created: list[tuple[str, int]] = []
            self.deleted: list[str] = []
            self.ready = True

        def get_sandbox(self, *, name: str) -> ReadySandbox:
            if name == "missing":
                raise RuntimeError("not found")
            sandbox = ReadySandbox()
            sandbox.name = name
            return sandbox

        def create_sandbox(self, *, template_name: str, timeout: int) -> ReadySandbox:
            self.created.append((template_name, timeout))
            return ReadySandbox(ready=self.ready)

        def delete_sandbox(self, name: str) -> None:
            self.deleted.append(name)

    provider = sandbox_factory._LangSmithProvider.__new__(
        sandbox_factory._LangSmithProvider
    )
    provider._client = FakeClient()
    monkeypatch.setattr(provider, "_ensure_template", lambda *_args: None)

    existing = provider.get_or_create(sandbox_id="existing")
    created = provider.get_or_create(timeout=2, template="template")

    assert existing.id == "existing"
    assert created.id == "new"
    assert provider._client.created == [("template", 2)]

    with pytest.raises(RuntimeError, match="Failed to connect"):
        provider.get_or_create(sandbox_id="missing")

    provider._client.ready = False
    with pytest.raises(RuntimeError, match="failed to start"):
        provider.get_or_create(timeout=2)
    assert provider._client.deleted == ["new"]

    class FlakyClient(FakeClient):
        def create_sandbox(self, *, template_name: str, timeout: int) -> ReadySandbox:
            sandbox = ReadySandbox()
            sandbox.name = "flaky"

            def flaky_run(_command: str, *, timeout: int) -> SimpleNamespace:
                sandbox.runs += 1
                if sandbox.runs == 1:
                    raise RuntimeError("warming")
                return SimpleNamespace(exit_code=0)

            sandbox.run = flaky_run  # type: ignore[method-assign]
            return sandbox

    provider._client = FlakyClient()
    assert provider.get_or_create(timeout=4).id == "flaky"


def test_daytona_provider_lifecycle_and_failure_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.model_config as model_config

    monkeypatch.setattr(model_config, "resolve_env_var", lambda name: f"{name}-value")
    monkeypatch.setattr(sandbox_factory.time, "sleep", lambda _seconds: None)

    class FakeSandbox:
        def __init__(self, *, exit_code: int = 0) -> None:
            self.deleted = False
            self.process = SimpleNamespace(
                exec=lambda *_args, **_kwargs: SimpleNamespace(exit_code=exit_code)
            )

        def delete(self) -> None:
            self.deleted = True

    class FakeClient:
        def __init__(self) -> None:
            self.sandbox = FakeSandbox()
            self.deleted: list[Any] = []

        def create(self) -> FakeSandbox:
            return self.sandbox

        def get(self, sandbox_id: str) -> str:
            return f"sandbox:{sandbox_id}"

        def delete(self, sandbox: Any) -> None:
            self.deleted.append(sandbox)

    fake_client = FakeClient()
    daytona_module = ModuleType("daytona")
    daytona_module.DaytonaConfig = lambda **kwargs: kwargs
    daytona_module.Daytona = lambda _config: fake_client
    daytona_backend = ModuleType("langchain_daytona")
    daytona_backend.DaytonaSandbox = lambda *, sandbox: SimpleNamespace(
        id="wrapped-daytona", sandbox=sandbox
    )

    def fake_import(module_name: str, **_kwargs: Any) -> ModuleType:
        return daytona_module if module_name == "daytona" else daytona_backend

    monkeypatch.setattr(sandbox_factory, "_import_provider_module", fake_import)

    provider = sandbox_factory._DaytonaProvider()
    backend = provider.get_or_create(timeout=2)
    provider.delete(sandbox_id="id-1")

    assert backend.id == "wrapped-daytona"
    assert fake_client.deleted == ["sandbox:id-1"]

    monkeypatch.setattr(model_config, "resolve_env_var", lambda _name: None)
    with pytest.raises(ValueError, match="No Daytona API key"):
        sandbox_factory._DaytonaProvider()
    monkeypatch.setattr(model_config, "resolve_env_var", lambda name: f"{name}-value")

    with pytest.raises(NotImplementedError):
        provider.get_or_create(sandbox_id="existing")

    class FlakySandbox(FakeSandbox):
        def __init__(self) -> None:
            super().__init__(exit_code=0)
            self.calls = 0

            def exec_ready(*_args: Any, **_kwargs: Any) -> SimpleNamespace:
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("warming")
                return SimpleNamespace(exit_code=0)

            self.process = SimpleNamespace(exec=exec_ready)

    fake_client.sandbox = FlakySandbox()
    assert provider.get_or_create(timeout=4).id == "wrapped-daytona"

    fake_client.sandbox = FakeSandbox(exit_code=1)
    with pytest.raises(RuntimeError, match="failed to start"):
        provider.get_or_create(timeout=2)
    assert fake_client.sandbox.deleted is True


def test_modal_provider_lifecycle_with_fake_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.model_config as model_config

    monkeypatch.setattr(model_config, "resolve_env_var", lambda _name: None)
    monkeypatch.setattr(sandbox_factory.time, "sleep", lambda _seconds: None)

    class FakeProcess:
        returncode = 0

        def wait(self) -> None:
            return None

    class FakeSandbox:
        def __init__(self, sandbox_id: str = "modal-new") -> None:
            self.id = sandbox_id
            self.terminated = False

        def poll(self) -> None:
            return None

        def exec(self, *_args: Any, **_kwargs: Any) -> FakeProcess:
            return FakeProcess()

        def terminate(self) -> None:
            self.terminated = True

    class FakeSandboxFactory:
        created: list[dict[str, Any]] = []
        from_ids: list[dict[str, Any]] = []
        created_sandbox = FakeSandbox()

        @classmethod
        def create(cls, **kwargs: Any) -> FakeSandbox:
            cls.created.append(kwargs)
            return cls.created_sandbox

        @classmethod
        def from_id(cls, **kwargs: Any) -> FakeSandbox:
            cls.from_ids.append(kwargs)
            return FakeSandbox(kwargs["sandbox_id"])

    modal_module = ModuleType("modal")
    modal_module.Client = SimpleNamespace(from_credentials=lambda *_args: object())
    modal_module.App = SimpleNamespace(lookup=lambda **kwargs: ("app", kwargs))
    modal_module.Sandbox = FakeSandboxFactory
    modal_backend = ModuleType("langchain_modal")
    modal_backend.ModalSandbox = lambda *, sandbox: SimpleNamespace(
        id=sandbox.id, sandbox=sandbox
    )
    monkeypatch.setattr(
        sandbox_factory,
        "_import_provider_module",
        lambda module_name, **_kwargs: (
            modal_backend if module_name == "langchain_modal" else modal_module
        ),
    )

    provider = sandbox_factory._ModalProvider()
    created = provider.get_or_create(timeout=2)
    existing = provider.get_or_create(sandbox_id="existing", timeout=2)
    provider.delete(sandbox_id="existing")

    assert created.id == "modal-new"
    assert existing.id == "existing"
    assert FakeSandboxFactory.created[0]["workdir"] == "/workspace"
    assert FakeSandboxFactory.from_ids[-1]["sandbox_id"] == "existing"

    provider._client = object()
    provider.get_or_create(sandbox_id="with-client", timeout=2)
    provider.delete(sandbox_id="with-client")
    assert "client" in FakeSandboxFactory.from_ids[-1]


def test_modal_provider_credentials_and_startup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.model_config as model_config

    class FakeSandbox:
        def __init__(self, *, poll_result: int | None = None, exec_error: bool = False):
            self.id = "modal-new"
            self.poll_result = poll_result
            self.exec_error = exec_error
            self.terminated = False

        def poll(self) -> int | None:
            return self.poll_result

        def exec(self, *_args: Any, **_kwargs: Any) -> Any:
            if self.exec_error:
                raise RuntimeError("not ready")
            return SimpleNamespace(wait=lambda: None, returncode=1)

        def terminate(self) -> None:
            self.terminated = True

    class FakeSandboxFactory:
        created_sandbox = FakeSandbox()

        @classmethod
        def create(cls, **_kwargs: Any) -> FakeSandbox:
            return cls.created_sandbox

        @classmethod
        def from_id(cls, **kwargs: Any) -> FakeSandbox:
            return FakeSandbox()

    modal_backend = ModuleType("langchain_modal")
    modal_backend.ModalSandbox = lambda *, sandbox: SimpleNamespace(id=sandbox.id)
    modal_module = ModuleType("modal")
    modal_module.App = SimpleNamespace(lookup=lambda **kwargs: ("app", kwargs))
    modal_module.Sandbox = FakeSandboxFactory

    monkeypatch.setattr(
        sandbox_factory,
        "_import_provider_module",
        lambda module_name, **_kwargs: (
            modal_backend if module_name == "langchain_modal" else modal_module
        ),
    )
    monkeypatch.setattr(sandbox_factory.time, "sleep", lambda _seconds: None)

    modal_module.Client = SimpleNamespace(
        from_credentials=lambda *_args: (_ for _ in ()).throw(RuntimeError("bad auth"))
    )
    monkeypatch.setattr(
        model_config,
        "resolve_env_var",
        lambda name: {"MODAL_TOKEN_ID": "id", "MODAL_TOKEN_SECRET": "secret"}.get(name),
    )
    with pytest.raises(ValueError, match="Failed to authenticate"):
        sandbox_factory._ModalProvider()

    explicit_client = object()
    modal_module.Client = SimpleNamespace(
        from_credentials=lambda *_args: explicit_client
    )
    provider = sandbox_factory._ModalProvider()
    assert provider._client is explicit_client

    monkeypatch.setattr(
        model_config,
        "resolve_env_var",
        lambda name: "id" if name == "MODAL_TOKEN_ID" else None,
    )
    partial_provider = sandbox_factory._ModalProvider()
    assert partial_provider._client is None

    FakeSandboxFactory.created_sandbox = FakeSandbox(poll_result=1)
    with pytest.raises(RuntimeError, match="terminated unexpectedly"):
        partial_provider.get_or_create(timeout=2)

    FakeSandboxFactory.created_sandbox = FakeSandbox(exec_error=True)
    with pytest.raises(RuntimeError, match="failed to start"):
        partial_provider.get_or_create(timeout=2)
    assert FakeSandboxFactory.created_sandbox.terminated is True


def test_runloop_provider_lifecycle_and_not_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.model_config as model_config

    monkeypatch.setattr(model_config, "resolve_env_var", lambda _name: "runloop-key")
    monkeypatch.setattr(sandbox_factory.time, "sleep", lambda _seconds: None)

    class FakeDevboxes:
        def __init__(self) -> None:
            self.created = False
            self.shutdowns: list[str] = []
            self.status = "running"

        def create(self) -> SimpleNamespace:
            self.created = True
            return SimpleNamespace(id="devbox-new")

        def retrieve(self, *, id: str) -> SimpleNamespace:  # noqa: A002
            if id == "missing":
                raise KeyError(id)
            return SimpleNamespace(id=id, status=self.status)

        def shutdown(self, *, id: str) -> None:  # noqa: A002
            self.shutdowns.append(id)

    class FakeRunloopClient:
        def __init__(self, *, bearer_token: str) -> None:
            self.bearer_token = bearer_token
            self.devboxes = FakeDevboxes()

    runloop_module = ModuleType("runloop_api_client")
    runloop_module.Runloop = FakeRunloopClient
    runloop_sdk = ModuleType("runloop_api_client.sdk")
    runloop_sdk.Devbox = lambda client, sandbox_id: ("devbox", client, sandbox_id)
    runloop_backend = ModuleType("langchain_runloop")
    runloop_backend.RunloopSandbox = lambda *, devbox: SimpleNamespace(
        id=devbox[2], devbox=devbox
    )
    monkeypatch.setattr(
        sandbox_factory,
        "_import_provider_module",
        lambda module_name, **_kwargs: {
            "runloop_api_client": runloop_module,
            "runloop_api_client.sdk": runloop_sdk,
            "langchain_runloop": runloop_backend,
        }[module_name],
    )

    provider = sandbox_factory._RunloopProvider()
    existing = provider.get_or_create(sandbox_id="existing", timeout=2)
    created = provider.get_or_create(timeout=2)
    provider.delete(sandbox_id="existing")

    assert existing.id == "existing"
    assert created.id == "devbox-new"
    assert provider._client.devboxes.shutdowns == ["existing"]

    monkeypatch.setattr(model_config, "resolve_env_var", lambda _name: None)
    with pytest.raises(ValueError, match="No Runloop API key"):
        sandbox_factory._RunloopProvider()
    monkeypatch.setattr(model_config, "resolve_env_var", lambda _name: "runloop-key")

    with pytest.raises(sandbox_factory.SandboxNotFoundError):
        provider.get_or_create(sandbox_id="missing")

    provider._client.devboxes.status = "starting"
    with pytest.raises(RuntimeError, match="failed to start"):
        provider.get_or_create(timeout=2)
    assert "devbox-new" in provider._client.devboxes.shutdowns


def test_agentcore_provider_lifecycle_and_cleanup_warnings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "boto3", None)

    class FakeInterpreter:
        def __init__(self, **kwargs: Any) -> None:
            self.kwargs = kwargs
            self.started = False
            self.stopped = False

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

    agentcore_module = ModuleType("agentcore")
    agentcore_module.CodeInterpreter = FakeInterpreter
    agentcore_backend = ModuleType("agentcore_backend")
    agentcore_backend.AgentCoreSandbox = lambda *, interpreter: SimpleNamespace(
        id="agentcore-id", interpreter=interpreter
    )
    monkeypatch.setattr(
        sandbox_factory,
        "_import_provider_module",
        lambda module_name, **_kwargs: (
            agentcore_module
            if "code_interpreter_client" in module_name
            else agentcore_backend
        ),
    )

    provider = sandbox_factory._AgentCoreProvider(region="us-east-1")
    backend = provider.get_or_create()

    assert backend.id == "agentcore-id"
    assert backend.interpreter.started is True
    provider.delete(sandbox_id="agentcore-id")
    assert backend.interpreter.stopped is True

    with pytest.raises(NotImplementedError):
        provider.get_or_create(sandbox_id="existing")

    provider.delete(sandbox_id="already-gone")


def test_agentcore_provider_credentials_start_failure_and_stop_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoCredsSession:
        def get_credentials(self) -> None:
            return None

    boto3_module = ModuleType("boto3")
    boto3_module.Session = lambda: NoCredsSession()
    monkeypatch.setitem(sys.modules, "boto3", boto3_module)

    with pytest.raises(ValueError, match="AWS credentials not found"):
        sandbox_factory._AgentCoreProvider(region="us-east-1")

    class BrokenCredentialSession:
        def get_credentials(self) -> object:
            raise RuntimeError("credential lookup failed")

    boto3_module.Session = lambda: BrokenCredentialSession()

    class FailingInterpreter:
        stopped = False

        def __init__(self, **_kwargs: Any) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("start failed")

        def stop(self) -> None:
            type(self).stopped = True

    agentcore_module = ModuleType("agentcore")
    agentcore_module.CodeInterpreter = FailingInterpreter
    agentcore_backend = ModuleType("agentcore_backend")
    agentcore_backend.AgentCoreSandbox = lambda *, interpreter: SimpleNamespace(
        id="agentcore-id", interpreter=interpreter
    )
    monkeypatch.setattr(
        sandbox_factory,
        "_import_provider_module",
        lambda module_name, **_kwargs: (
            agentcore_module
            if "code_interpreter_client" in module_name
            else agentcore_backend
        ),
    )

    provider = sandbox_factory._AgentCoreProvider(region="us-east-1")
    with pytest.raises(RuntimeError, match="start failed"):
        provider.get_or_create()
    assert FailingInterpreter.stopped is True

    class StopFailInterpreter:
        def stop(self) -> None:
            raise RuntimeError("stop failed")

    provider._active_interpreters["agentcore-id"] = StopFailInterpreter()
    provider.delete(sandbox_id="agentcore-id")
