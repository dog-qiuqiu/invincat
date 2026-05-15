"""Tests for ask_user middleware validation and fail-closed parsing."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.messages import SystemMessage, ToolMessage

from invincat_cli.middleware import ask_user
from invincat_cli.wecom.file import WECOM_CONTEXT_FLAG


def _text_question(text: str = "What next?") -> dict[str, object]:
    return {"type": "text", "question": text}


def _choice_question() -> dict[str, object]:
    return {
        "type": "multiple_choice",
        "question": "Pick one",
        "choices": [{"label": "A"}, {"label": "B"}],
    }


@pytest.mark.parametrize(
    ("questions", "match"),
    [
        ([], "at least one"),
        ([{"type": "text", "question": "   "}], "non-empty"),
        ([{"type": "unsupported", "question": "Q"}], "unsupported"),
        ([{"type": "multiple_choice", "question": "Q"}], "requires"),
        ([{"type": "text", "question": "Q", "choices": ["A"]}], "must not"),
    ],
)
def test_validate_questions_rejects_malformed_payloads(
    questions: list[dict[str, object]],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        ask_user._validate_questions(questions)


def test_validate_questions_accepts_text_and_choice_questions() -> None:
    ask_user._validate_questions([_text_question(), _choice_question()])


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        ({"status": "answered", "answers": ["blue"]}, "A: blue"),
        ({"status": "answered", "answers": []}, "A: (no answer)"),
        ({"status": "cancelled"}, "A: (cancelled)"),
        (
            {"status": "error", "error": "adapter failed"},
            "A: (error: adapter failed)",
        ),
        ("bad", "A: (error: invalid ask_user response payload)"),
        ({}, "A: (error: missing ask_user answers payload)"),
        ({"answers": "bad"}, "A: (error: invalid ask_user answers payload)"),
        ({"status": "weird", "answers": ["x"]}, "A: (error: invalid"),
    ],
)
def test_parse_answers_converts_resume_payloads_to_tool_message(
    response: object,
    expected: str,
) -> None:
    command = ask_user._parse_answers(response, [_text_question("Color?")], "call-1")
    message = command.update["messages"][0]

    assert isinstance(message, ToolMessage)
    assert message.tool_call_id == "call-1"
    assert "Q: Color?" in message.content
    assert expected in message.content


def test_is_wecom_context_requires_dict_context_flag() -> None:
    assert not ask_user._is_wecom_context(SimpleNamespace(context=None))
    assert not ask_user._is_wecom_context(SimpleNamespace(context=[]))
    assert not ask_user._is_wecom_context(SimpleNamespace(context={}))
    assert ask_user._is_wecom_context(
        SimpleNamespace(context={WECOM_CONTEXT_FLAG: True})
    )


class _Tool:
    def __init__(self, name: str) -> None:
        self.name = name


class _Request:
    def __init__(
        self,
        *,
        runtime: object,
        tools: list[object] | None = None,
        system_message: SystemMessage | None = None,
    ) -> None:
        self.runtime = runtime
        self.tools = tools or []
        self.system_message = system_message
        self.override_kwargs: dict[str, object] | None = None

    def override(self, **kwargs: object) -> _Request:
        clone = _Request(
            runtime=self.runtime,
            tools=kwargs.get("tools", self.tools),
            system_message=kwargs.get("system_message", self.system_message),
        )
        clone.override_kwargs = kwargs
        return clone


def test_apply_model_request_removes_tool_in_wecom_context() -> None:
    middleware = ask_user.AskUserMiddleware()
    request = _Request(
        runtime=SimpleNamespace(context={WECOM_CONTEXT_FLAG: True}),
        tools=[_Tool(ask_user.ASK_USER_TOOL_NAME), {"name": "other"}],
    )

    modified = middleware._apply_model_request(request)

    assert [tool.name if hasattr(tool, "name") else tool["name"] for tool in modified.tools] == [
        "other"
    ]


def test_apply_model_request_appends_system_prompt_to_existing_content() -> None:
    middleware = ask_user.AskUserMiddleware(system_prompt="Ask only when needed")
    request = _Request(
        runtime=SimpleNamespace(context={}),
        system_message=SystemMessage(content="Base prompt"),
    )

    modified = middleware._apply_model_request(request)

    assert isinstance(modified.system_message, SystemMessage)
    blocks = modified.system_message.content_blocks
    assert blocks[0]["text"] == "Base prompt"
    assert blocks[1] == {"type": "text", "text": "\n\nAsk only when needed"}


def test_apply_model_request_creates_system_prompt_when_missing() -> None:
    middleware = ask_user.AskUserMiddleware(system_prompt="Use ask_user sparingly")

    modified = middleware._apply_model_request(
        _Request(runtime=SimpleNamespace(context={}))
    )

    assert modified.system_message.content == [
        {"type": "text", "text": "Use ask_user sparingly"}
    ]


def test_reject_if_wecom_only_rejects_ask_user_calls_in_wecom_context() -> None:
    middleware = ask_user.AskUserMiddleware()
    handler_message = ToolMessage("handled", name="other", tool_call_id="call-2")

    non_ask_request = SimpleNamespace(
        tool_call={"name": "other", "id": "call-2"},
        runtime=SimpleNamespace(context={WECOM_CONTEXT_FLAG: True}),
    )
    assert middleware._reject_if_wecom(non_ask_request) is None

    normal_request = SimpleNamespace(
        tool_call={"name": ask_user.ASK_USER_TOOL_NAME, "id": "call-1"},
        runtime=SimpleNamespace(context={}),
    )
    assert middleware.wrap_tool_call(normal_request, lambda _request: handler_message)

    wecom_request = SimpleNamespace(
        tool_call={"name": ask_user.ASK_USER_TOOL_NAME, "id": "call-1"},
        runtime=SimpleNamespace(context={WECOM_CONTEXT_FLAG: True}),
    )
    rejected = middleware._reject_if_wecom(wecom_request)

    assert rejected is not None
    assert rejected.status == "error"
    assert "not available" in rejected.content


def test_wrap_model_call_passes_modified_request_to_handlers() -> None:
    middleware = ask_user.AskUserMiddleware(system_prompt="prompt")
    request = _Request(runtime=SimpleNamespace(context={}))

    response = middleware.wrap_model_call(request, lambda modified: modified)

    assert response.system_message.content == [{"type": "text", "text": "prompt"}]


def test_awrap_tool_call_rejects_wecom_without_calling_handler() -> None:
    middleware = ask_user.AskUserMiddleware()
    called = False

    async def handler(_request: object) -> ToolMessage:
        nonlocal called
        called = True
        return ToolMessage("handled", tool_call_id="call-1")

    request = SimpleNamespace(
        tool_call={"name": ask_user.ASK_USER_TOOL_NAME, "id": "call-1"},
        runtime=SimpleNamespace(context={WECOM_CONTEXT_FLAG: True}),
    )

    rejected = asyncio.run(middleware.awrap_tool_call(request, handler))

    assert not called
    assert rejected.status == "error"


def test_wrap_tool_call_allows_normal_ask_user_context() -> None:
    middleware = ask_user.AskUserMiddleware()
    expected = ToolMessage("handled", tool_call_id="call-1")
    request = SimpleNamespace(
        tool_call={"name": ask_user.ASK_USER_TOOL_NAME, "id": "call-1"},
        runtime=SimpleNamespace(context={}),
    )

    assert middleware.wrap_tool_call(request, lambda _request: expected) is expected


def test_middleware_installs_named_tool() -> None:
    middleware = ask_user.AskUserMiddleware(tool_description="custom")

    assert len(middleware.tools) == 1
    assert middleware.tools[0].name == ask_user.ASK_USER_TOOL_NAME
    assert "custom" in middleware.tools[0].description
