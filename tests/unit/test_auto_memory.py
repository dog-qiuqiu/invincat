from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from langchain_core.messages import SystemMessage

import invincat_cli.auto_memory as auto_memory_module
from invincat_cli.auto_memory import RefreshableMemoryMiddleware


@dataclass
class _FakeRequest:
    state: dict[str, Any]
    system_message: SystemMessage | None = None

    def override(self, **kwargs: Any) -> "_FakeRequest":
        return replace(self, **kwargs)


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
    assert "user::" + str(user_store.resolve()) in contents
    assert "project::" + str(project_store.resolve()) in contents
    assert "Prefer concise Chinese responses." in "\n".join(contents.values())
    assert "Use uv for dependencies." in "\n".join(contents.values())


def test_before_agent_skips_when_already_loaded(tmp_path: Path) -> None:
    mw = RefreshableMemoryMiddleware(
        backend=object(),
        memory_store_paths={"project": str(tmp_path / "memory_project.json")},
    )
    assert mw.before_agent({"memory_contents": {"x": "y"}}, runtime=object()) is None


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
