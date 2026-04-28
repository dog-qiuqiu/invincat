from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from collections.abc import Mapping

from langchain_core.messages import SystemMessage

import invincat_cli.auto_memory as auto_memory_module
from invincat_cli.auto_memory import RefreshableMemoryMiddleware


@dataclass
class _FakeRequest:
    state: dict[str, Any]
    system_message: SystemMessage | None = None

    def override(self, **kwargs: Any) -> "_FakeRequest":
        return replace(self, **kwargs)


@dataclass
class _AsyncFakeRequest:
    state: dict[str, Any]
    system_message: SystemMessage | None = None

    def override(self, **kwargs: Any) -> "_AsyncFakeRequest":
        return replace(self, **kwargs)


class _StateMapping(Mapping[str, Any]):
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = dict(payload)

    def __getitem__(self, key: str) -> Any:
        return self._payload[key]

    def __iter__(self):
        return iter(self._payload)

    def __len__(self) -> int:
        return len(self._payload)

    def get(self, key: str, default: Any = None) -> Any:
        return self._payload.get(key, default)


def _make_store(path: Path, *, scope: str, items: list[dict[str, Any]]) -> None:
    path.write_text(
        json.dumps({"version": 1, "scope": scope, "items": items}),
        encoding="utf-8",
    )


def test_before_agent_loads_rendered_store_content(tmp_path: Path) -> None:
    user_store = tmp_path / "memory_user.json"
    project_store = tmp_path / "memory_project.json"
    _make_store(
        user_store,
        scope="user",
        items=[
            {
                "id": "mem_u_000001",
                "section": "User Preferences",
                "content": "Prefer concise Chinese responses.",
                "status": "active",
            }
        ],
    )
    _make_store(
        project_store,
        scope="project",
        items=[
            {
                "id": "mem_p_000001",
                "section": "Project Rules",
                "content": "Use uv for dependencies.",
                "status": "active",
            }
        ],
    )

    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={"user": str(user_store), "project": str(project_store)},
    )
    update = mw.before_agent({"memory_contents": None}, runtime=object())
    assert isinstance(update, dict)
    contents = update["memory_contents"]
    assert "User Memory" in contents
    assert "Project Memory" in contents
    assert "Prefer concise Chinese responses." in "\n".join(contents.values())
    assert "Use uv for dependencies." in "\n".join(contents.values())


def test_before_agent_always_refreshes_even_when_already_loaded(tmp_path: Path) -> None:
    project_store = tmp_path / "memory_project.json"
    _make_store(
        project_store,
        scope="project",
        items=[
            {
                "id": "mem_p_000001",
                "section": "Project Rules",
                "content": "Always refresh from disk.",
                "status": "active",
            }
        ],
    )
    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={"project": str(project_store)},
    )
    update = mw.before_agent({"memory_contents": {"x": "y"}}, runtime=object())
    assert isinstance(update, dict)
    rendered = "\n".join(update["memory_contents"].values())
    assert "Always refresh from disk." in rendered


def test_invalid_store_is_ignored(tmp_path: Path) -> None:
    project_store = tmp_path / "memory_project.json"
    project_store.write_text("{bad-json", encoding="utf-8")
    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={"project": str(project_store)},
    )
    update = mw.before_agent({"memory_contents": None}, runtime=object())
    assert isinstance(update, dict)
    assert update["memory_contents"] == {}


def test_malformed_items_are_filtered_from_injection(tmp_path: Path) -> None:
    project_store = tmp_path / "memory_project.json"
    _make_store(
        project_store,
        scope="project",
        items=[
            {
                "id": "mem_p_000001",
                "section": "Project Rules",
                "content": "valid item",
                "status": "active",
            },
            {
                "id": "mem_p_000002",
                "section": "Project Rules",
                "content": "",
                "status": "active",
            },
            {
                "id": "mem_p_000003",
                "section": "Project Rules",
                "content": "bad status",
                "status": "unknown",
            },
        ],
    )
    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={"project": str(project_store)},
    )
    update = mw.before_agent({"memory_contents": None}, runtime=object())
    assert isinstance(update, dict)
    rendered = "\n".join(update["memory_contents"].values())
    assert "valid item" in rendered
    assert "bad status" not in rendered


def test_wrap_model_call_injects_agent_memory(tmp_path: Path) -> None:
    project_store = tmp_path / "memory_project.json"
    _make_store(
        project_store,
        scope="project",
        items=[
            {
                "id": "mem_p_000001",
                "section": "Project Rules",
                "content": "Use snake_case fields.",
                "status": "active",
            }
        ],
    )
    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={"project": str(project_store)},
    )
    loaded = mw.before_agent({"memory_contents": None}, runtime=object())
    req = _FakeRequest(state=loaded or {})

    captured: dict[str, Any] = {}

    def _handler(request: _FakeRequest) -> str:
        captured["request"] = request
        return "ok"

    out = mw.wrap_model_call(req, _handler)
    assert out == "ok"
    final_req = captured["request"]
    assert isinstance(final_req.system_message, SystemMessage)
    text = final_req.system_message.text
    assert "<agent_memory>" in text
    assert "Use snake_case fields." in text
    assert "edit_file" not in text


def test_wrap_model_call_reads_store_even_without_before_agent_state(tmp_path: Path) -> None:
    project_store = tmp_path / "memory_project.json"
    _make_store(
        project_store,
        scope="project",
        items=[
            {
                "id": "mem_p_000001",
                "section": "Project Rules",
                "content": "Read directly in wrap.",
                "status": "active",
            }
        ],
    )
    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={"project": str(project_store)},
    )
    req = _FakeRequest(state={})

    captured: dict[str, Any] = {}

    def _handler(request: _FakeRequest) -> str:
        captured["request"] = request
        return "ok"

    out = mw.wrap_model_call(req, _handler)
    assert out == "ok"
    final_req = captured["request"]
    assert isinstance(final_req.system_message, SystemMessage)
    assert "Read directly in wrap." in final_req.system_message.text


def test_wrap_model_call_reuses_cache_when_store_unchanged(tmp_path: Path, monkeypatch: Any) -> None:
    project_store = tmp_path / "memory_project.json"
    _make_store(
        project_store,
        scope="project",
        items=[
            {
                "id": "mem_p_000001",
                "section": "Rules",
                "content": "cached",
                "status": "active",
            }
        ],
    )
    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={"project": str(project_store)},
    )
    # prime cache
    mw.before_agent({"memory_contents": None}, runtime=object())

    calls = {"n": 0}
    original = mw._load_memory_contents

    def _wrapped() -> dict[str, str]:
        calls["n"] += 1
        return original()

    monkeypatch.setattr(mw, "_load_memory_contents", _wrapped)

    req = _FakeRequest(state={})
    mw.wrap_model_call(req, lambda r: "ok")
    mw.wrap_model_call(req, lambda r: "ok")
    assert calls["n"] == 0


def test_wrap_model_call_reload_on_store_signature_change(tmp_path: Path) -> None:
    project_store = tmp_path / "memory_project.json"
    _make_store(
        project_store,
        scope="project",
        items=[
            {
                "id": "mem_p_000001",
                "section": "Rules",
                "content": "old",
                "status": "active",
            }
        ],
    )
    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={"project": str(project_store)},
    )
    mw.before_agent({"memory_contents": None}, runtime=object())

    _make_store(
        project_store,
        scope="project",
        items=[
            {
                "id": "mem_p_000001",
                "section": "Rules",
                "content": "new",
                "status": "active",
            }
        ],
    )

    captured: dict[str, Any] = {}

    def _handler(request: _FakeRequest) -> str:
        captured["request"] = request
        return "ok"

    out = mw.wrap_model_call(_FakeRequest(state={}), _handler)
    assert out == "ok"
    final_req = captured["request"]
    assert isinstance(final_req.system_message, SystemMessage)
    assert "new" in final_req.system_message.text


def test_awrap_model_call_does_not_fallback_to_disk_when_state_missing_memory_contents(
    tmp_path: Path,
) -> None:
    project_store = tmp_path / "memory_project.json"
    _make_store(
        project_store,
        scope="project",
        items=[
            {
                "id": "mem_p_000001",
                "section": "Rules",
                "content": "async fallback",
                "status": "active",
            }
        ],
    )
    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={"project": str(project_store)},
    )
    req = _AsyncFakeRequest(state={})

    captured: dict[str, Any] = {}

    async def _handler(request: _AsyncFakeRequest) -> str:
        captured["request"] = request
        return "ok"

    out = asyncio.run(mw.awrap_model_call(req, _handler))
    assert out == "ok"
    final_req = captured["request"]
    assert isinstance(final_req.system_message, SystemMessage)
    assert "(No memory loaded)" in final_req.system_message.text


def test_awrap_model_call_reads_memory_contents_from_mapping_state() -> None:
    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={},
    )
    req = _AsyncFakeRequest(
        state=_StateMapping(
            {
                "memory_contents": {
                    "User Memory": "### Always Apply\n- Preferences: concise output"
                }
            }
        )  # type: ignore[arg-type]
    )

    captured: dict[str, Any] = {}

    async def _handler(request: _AsyncFakeRequest) -> str:
        captured["request"] = request
        return "ok"

    out = asyncio.run(mw.awrap_model_call(req, _handler))
    assert out == "ok"
    final_req = captured["request"]
    assert isinstance(final_req.system_message, SystemMessage)
    assert "concise output" in final_req.system_message.text


def test_abefore_agent_uses_to_thread(tmp_path: Path, monkeypatch: Any) -> None:
    project_store = tmp_path / "memory_project.json"
    _make_store(
        project_store,
        scope="project",
        items=[
            {
                "id": "mem_p_000001",
                "section": "Project Rules",
                "content": "Use uv.",
                "status": "active",
            }
        ],
    )
    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={"project": str(project_store)},
    )

    called = {"v": False}
    original_to_thread = auto_memory_module.asyncio.to_thread

    async def _fake_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
        called["v"] = True
        return await original_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(auto_memory_module.asyncio, "to_thread", _fake_to_thread)

    result = asyncio.run(mw.abefore_agent({"memory_contents": None}, runtime=object()))
    assert called["v"] is True
    assert isinstance(result, dict)
    assert "memory_contents" in result


def test_total_memory_injection_budget_is_enforced(tmp_path: Path) -> None:
    user_store = tmp_path / "memory_user.json"
    project_store = tmp_path / "memory_project.json"
    big = "x" * 500
    user_items = [
        {
            "id": f"mem_u_{idx:06d}",
            "section": "User Preferences",
            "content": big,
            "status": "active",
        }
        for idx in range(1, 40)
    ]
    project_items = [
        {
            "id": f"mem_p_{idx:06d}",
            "section": "Project Rules",
            "content": big,
            "status": "active",
        }
        for idx in range(1, 40)
    ]
    _make_store(user_store, scope="user", items=user_items)
    _make_store(project_store, scope="project", items=project_items)

    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={"user": str(user_store), "project": str(project_store)},
    )
    update = mw.before_agent({"memory_contents": None}, runtime=object())
    assert isinstance(update, dict)
    contents = update["memory_contents"]
    total_len = sum(len(v) for v in contents.values())
    assert total_len <= auto_memory_module._MAX_TOTAL_INJECTION_CHARS


def test_hot_priority_and_cold_exclusion_in_injection(tmp_path: Path) -> None:
    project_store = tmp_path / "memory_project.json"
    _make_store(
        project_store,
        scope="project",
        items=[
            {
                "id": "mem_p_000001",
                "section": "Rules",
                "content": "Warm item",
                "status": "active",
                "tier": "warm",
                "score": 60,
            },
            {
                "id": "mem_p_000002",
                "section": "Rules",
                "content": "Hot item",
                "status": "active",
                "tier": "hot",
                "score": 90,
            },
            {
                "id": "mem_p_000003",
                "section": "Rules",
                "content": "Cold item",
                "status": "active",
                "tier": "cold",
                "score": 10,
            },
        ],
    )
    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={"project": str(project_store)},
    )
    update = mw.before_agent({"memory_contents": None}, runtime=object())
    assert isinstance(update, dict)
    rendered = "\n".join(update["memory_contents"].values())
    assert "Hot item" in rendered
    assert "Warm item" in rendered
    assert "Cold item" not in rendered
    assert rendered.index("Hot item") < rendered.index("Warm item")


def test_same_tier_sorted_by_score_desc(tmp_path: Path) -> None:
    project_store = tmp_path / "memory_project.json"
    _make_store(
        project_store,
        scope="project",
        items=[
            {
                "id": "mem_p_000001",
                "section": "Rules",
                "content": "score 75",
                "status": "active",
                "tier": "hot",
                "score": 75,
            },
            {
                "id": "mem_p_000002",
                "section": "Rules",
                "content": "score 95",
                "status": "active",
                "tier": "hot",
                "score": 95,
            },
        ],
    )
    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={"project": str(project_store)},
    )
    update = mw.before_agent({"memory_contents": None}, runtime=object())
    assert isinstance(update, dict)
    rendered = "\n".join(update["memory_contents"].values())
    assert rendered.index("score 95") < rendered.index("score 75")


def test_legacy_item_without_tier_score_is_handled(tmp_path: Path) -> None:
    project_store = tmp_path / "memory_project.json"
    _make_store(
        project_store,
        scope="project",
        items=[
            {
                "id": "mem_p_000001",
                "section": "Rules",
                "content": "Legacy item",
                "status": "active",
            }
        ],
    )
    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={"project": str(project_store)},
    )
    update = mw.before_agent({"memory_contents": None}, runtime=object())
    assert isinstance(update, dict)
    rendered = "\n".join(update["memory_contents"].values())
    assert "Legacy item" in rendered


def test_reload_when_store_changes_even_if_state_has_cached_empty_dict(tmp_path: Path) -> None:
    project_store = tmp_path / "memory_project.json"
    _make_store(project_store, scope="project", items=[])
    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={"project": str(project_store)},
    )

    first = mw.before_agent({"memory_contents": None}, runtime=object())
    assert isinstance(first, dict)
    assert first["memory_contents"] == {}

    _make_store(
        project_store,
        scope="project",
        items=[
            {
                "id": "mem_p_000001",
                "section": "Rules",
                "content": "Use uv.",
                "status": "active",
            }
        ],
    )

    second = mw.before_agent({"memory_contents": {}}, runtime=object())
    assert isinstance(second, dict)
    rendered = "\n".join(second["memory_contents"].values())
    assert "Use uv." in rendered
