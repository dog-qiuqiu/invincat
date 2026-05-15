from __future__ import annotations

import asyncio
from types import SimpleNamespace

from invincat_cli import configurable_model
from invincat_cli.model_config import ModelConfigError


class FakeModel:
    def __init__(self, provider: str | None = None, *, broken: bool = False) -> None:
        self.provider = provider
        self.broken = broken

    def _get_ls_params(self) -> dict[str, str]:
        if self.broken:
            raise RuntimeError("no params")
        return {"ls_provider": self.provider or ""}


class FakeRequest:
    def __init__(
        self,
        *,
        runtime: object | None = None,
        model: object | None = None,
        model_settings: dict[str, object] | None = None,
        system_prompt: str | None = None,
    ) -> None:
        self.runtime = runtime
        self.model = model or FakeModel("anthropic")
        self.model_settings = model_settings or {}
        self.system_prompt = system_prompt
        self.overrides: dict[str, object] = {}

    def override(self, **kwargs: object) -> FakeRequest:
        request = FakeRequest(
            runtime=self.runtime,
            model=kwargs.get("model", self.model),
            model_settings=kwargs.get("model_settings", self.model_settings),  # type: ignore[arg-type]
            system_prompt=kwargs.get("system_prompt", self.system_prompt),  # type: ignore[arg-type]
        )
        request.overrides = kwargs
        return request


def test_is_anthropic_model_handles_provider_and_errors() -> None:
    assert configurable_model._is_anthropic_model(FakeModel("anthropic"))
    assert not configurable_model._is_anthropic_model(FakeModel("openai"))
    assert not configurable_model._is_anthropic_model(
        FakeModel("anthropic", broken=True)
    )
    assert not configurable_model._is_anthropic_model(object())


def test_apply_overrides_returns_original_without_context_or_overrides() -> None:
    request = FakeRequest(runtime=None)
    assert configurable_model._apply_overrides(request) is request  # type: ignore[arg-type]

    request = FakeRequest(runtime=SimpleNamespace(context="not-a-dict"))
    assert configurable_model._apply_overrides(request) is request  # type: ignore[arg-type]

    request = FakeRequest(runtime=SimpleNamespace(context={}))
    assert configurable_model._apply_overrides(request) is request  # type: ignore[arg-type]


def test_apply_overrides_merges_model_params_only() -> None:
    request = FakeRequest(
        runtime=SimpleNamespace(context={"model_params": {"temperature": 0.2}}),
        model_settings={"cache_control": True},
    )

    result = configurable_model._apply_overrides(request)  # type: ignore[arg-type]

    assert result is not request
    assert result.model_settings == {"cache_control": True, "temperature": 0.2}


def test_apply_overrides_switches_model_and_patches_identity(monkeypatch) -> None:
    monkeypatch.setattr(configurable_model, "model_matches_spec", lambda *_args: False)
    cleared: list[bool] = []
    new_model = FakeModel("openai")
    monkeypatch.setattr(
        "invincat_cli.model_config.clear_caches", lambda: cleared.append(True)
    )
    monkeypatch.setattr(
        "invincat_cli.config.create_model",
        lambda model: SimpleNamespace(
            model=new_model,
            model_name=model,
            provider="openai",
            context_limit=1234,
            unsupported_modalities=frozenset({"video"}),
        ),
    )
    request = FakeRequest(
        runtime=SimpleNamespace(
            context={
                "model": "openai:gpt",
                "model_params": {"temperature": 0.1},
            }
        ),
        model=FakeModel("anthropic"),
        model_settings={"cache_control": True, "top_p": 0.9},
        system_prompt="prefix\n\n### Model Identity\n\nold\n### Next\nbody",
    )

    result = configurable_model._apply_overrides(request)  # type: ignore[arg-type]

    assert cleared == [True]
    assert result.model is new_model
    assert result.model_settings == {"top_p": 0.9, "temperature": 0.1}
    assert "openai:gpt" in (result.system_prompt or "")
    assert "Video input may not be available" in (result.system_prompt or "")
    assert "### Next\nbody" in (result.system_prompt or "")


def test_apply_overrides_skips_matching_or_invalid_model(monkeypatch) -> None:
    monkeypatch.setattr(configurable_model, "model_matches_spec", lambda *_args: True)
    matching = FakeRequest(runtime=SimpleNamespace(context={"model": "same"}))
    assert configurable_model._apply_overrides(matching) is matching  # type: ignore[arg-type]

    monkeypatch.setattr(configurable_model, "model_matches_spec", lambda *_args: False)
    monkeypatch.setattr("invincat_cli.model_config.clear_caches", lambda: None)

    def fail_create(_model: str) -> object:
        raise ModelConfigError("bad model")

    monkeypatch.setattr("invincat_cli.config.create_model", fail_create)
    invalid = FakeRequest(runtime=SimpleNamespace(context={"model": "bad"}))

    assert configurable_model._apply_overrides(invalid) is invalid  # type: ignore[arg-type]


def test_apply_overrides_keeps_anthropic_settings_for_anthropic_model(
    monkeypatch,
) -> None:
    monkeypatch.setattr(configurable_model, "model_matches_spec", lambda *_args: False)
    monkeypatch.setattr("invincat_cli.model_config.clear_caches", lambda: None)
    monkeypatch.setattr(
        "invincat_cli.config.create_model",
        lambda _model: SimpleNamespace(
            model=FakeModel("anthropic"),
            model_name="anthropic:claude",
            provider="anthropic",
            context_limit=None,
            unsupported_modalities=frozenset(),
        ),
    )
    request = FakeRequest(
        runtime=SimpleNamespace(context={"model": "anthropic:claude"}),
        model_settings={"cache_control": True},
    )

    result = configurable_model._apply_overrides(request)  # type: ignore[arg-type]

    assert result.model_settings == {"cache_control": True}


def test_apply_overrides_warns_when_identity_heading_does_not_match(
    monkeypatch,
) -> None:
    monkeypatch.setattr(configurable_model, "model_matches_spec", lambda *_args: False)
    monkeypatch.setattr("invincat_cli.model_config.clear_caches", lambda: None)
    monkeypatch.setattr(
        "invincat_cli.config.create_model",
        lambda _model: SimpleNamespace(
            model=FakeModel("openai"),
            model_name="openai:gpt",
            provider="openai",
            context_limit=None,
            unsupported_modalities=frozenset(),
        ),
    )
    request = FakeRequest(
        runtime=SimpleNamespace(context={"model": "openai:gpt"}),
        system_prompt="### Model Identity without blank line",
    )

    result = configurable_model._apply_overrides(request)  # type: ignore[arg-type]

    assert "system_prompt" not in result.overrides


def test_configurable_model_middleware_wraps_sync_and_async_calls() -> None:
    middleware = configurable_model.ConfigurableModelMiddleware()
    request = FakeRequest(runtime=SimpleNamespace(context={"model_params": {"x": 1}}))
    seen: list[FakeRequest] = []

    def handler(value: FakeRequest) -> str:
        seen.append(value)
        return "ok"

    assert middleware.wrap_model_call(request, handler) == "ok"  # type: ignore[arg-type]
    assert seen[-1].model_settings == {"x": 1}

    async def run() -> None:
        async def async_handler(value: FakeRequest) -> str:
            seen.append(value)
            return "async-ok"

        assert await middleware.awrap_model_call(request, async_handler) == "async-ok"  # type: ignore[arg-type]
        assert seen[-1].model_settings == {"x": 1}

    asyncio.run(run())
