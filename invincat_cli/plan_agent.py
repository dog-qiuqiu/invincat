"""Plan Agent — a standalone agent for task planning.

The planner is a dedicated agent that:
1. Understands user requirements
2. Generates structured todo lists via write_todos
3. Returns the plan for user approval

How `/plan <task>` works:
  1. The user types `/plan <task description>`.
  2. The CLI creates a planner agent and invokes it with the task.
  3. The planner generates a todo list via write_todos tool.
  4. The CLI displays the approve widget for user confirmation.
  5. If approved, the main agent executes the plan.
  6. If rejected, the CLI asks for feedback and re-invokes the planner.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import TYPE_CHECKING, Any

from langchain.agents import create_agent
from langchain.agents.middleware import TodoListMiddleware

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel
    from langgraph.graph.state import CompiledStateGraph

logger = logging.getLogger(__name__)


PLANNER_SUBAGENT_NAME = "planner"
"""Canonical planner subagent name used in directives and metadata."""

PLAN_APPROVED_MARKER = "<<PLAN_APPROVED>>"
"""Distinct marker inserted by planner when user approves execution."""

PLANNER_ALLOWED_TOOLS: tuple[str, ...] = ("write_todos", "ask_user")
"""Planner-visible tool contract documented in the system prompt."""

PLANNER_DESCRIPTION = (
    "Read-only planning specialist. It only creates plans and never edits files, "
    "executes commands, or performs implementation work."
)
"""Subagent description surfaced to the main agent."""

PLANNER_SYSTEM_PROMPT: str = f"""You are a task planning agent. Your ONLY job is to create structured task plans.

## Task boundary

Input is the user's query and intent.
Output is a structured plan recorded via write_todos, then confirmed via ask_user.

## Your Task

1. Understand the user's request — make reasonable assumptions, do NOT ask clarifying questions
2. Break it down into actionable steps
3. Call `write_todos` to record the plan
4. Call `ask_user` for final approval (this is required, not optional)
5. If the user selects "Approve and execute", include `{PLAN_APPROVED_MARKER}` in your response
6. If the user selects "Refine" or "Cancel", stop — do NOT execute anything

## Rules

- You can ONLY use these tools: {", ".join(PLANNER_ALLOWED_TOOLS)}
- Do NOT read files, edit code, run commands, search the web, or use any other tool
- During planning: make assumptions, never ask questions
- ask_user is only for the final approval step — not for clarification
- Respond in the same language as the user's input

## Step granularity

Each step should be one focused unit of work — one file to edit, one command to run, \
one conceptual change. Avoid steps that are too broad ("implement auth") or too narrow \
("add import on line 45"). A good step takes 1–5 minutes and has a clear done state.

## Output Format

After calling `write_todos`, output a numbered summary:

1. First task
2. Second task
3. Third task

Then call ask_user with these exact options:
- Approve and execute
- Refine
- Cancel

Only when the user selects "Approve and execute", append:
`{PLAN_APPROVED_MARKER}`

## write_todos Example

```
write_todos([
    {{"content": "First task description", "status": "in_progress"}},
    {{"content": "Second task description", "status": "pending"}},
    {{"content": "Third task description", "status": "pending"}}
])
```

Mark the first task as "in_progress", all others as "pending"."""


def build_planner_subagent() -> dict[str, str]:
    """Build planner subagent metadata for main-agent delegation."""
    return {
        "name": PLANNER_SUBAGENT_NAME,
        "description": PLANNER_DESCRIPTION,
        "system_prompt": PLANNER_SYSTEM_PROMPT,
    }


def build_plan_directive(task: str) -> str:
    """Build directive text instructing the main agent to invoke planner.

    The directive encodes the handoff protocol so the main agent can safely
    execute only after planner confirmation.
    """
    normalized_task = task.strip()
    return (
        f"Delegate planning to subagent '{PLANNER_SUBAGENT_NAME}' for this task:\n"
        f"{normalized_task}\n\n"
        f"The planner will provide a todo list and use ask_user for confirmation. "
        f"Only implement/execute when the response includes {PLAN_APPROVED_MARKER}. "
        f"If approval is missing (Refine/Cancel), stop and do not execute.\n"
        "After approval, mirror the approved plan into your own write_todos call "
        "so progress state is persisted in the main thread."
    )


def create_planner_agent(
    model: str | BaseChatModel,
    model_params: dict[str, Any] | None = None,
) -> CompiledStateGraph:
    """Create a standalone planner agent.

    The planner agent has access only to `write_todos` tool and is designed
    to generate structured task plans for user approval.

    Args:
        model: The language model to use (string identifier or BaseChatModel).
        model_params: Optional model parameters to pass to model initialization.

    Returns:
        A compiled planner agent graph.
    """
    from invincat_cli.config import create_model

    if isinstance(model, str):
        model_result = create_model(model, extra_kwargs=model_params)
        model = model_result.model

    todo_middleware = TodoListMiddleware()

    return create_agent(
        model=model,
        tools=todo_middleware.tools,
        system_prompt=PLANNER_SYSTEM_PROMPT,
        middleware=[todo_middleware],
        name=PLANNER_SUBAGENT_NAME,
    )


_TODO_PATTERN = re.compile(r"^\s*(\d+)\.\s+(.+)$")


def extract_todos_from_message(message: str) -> list[dict[str, str]] | None:
    """Extract todo items from planner's output message.

    Args:
        message: The planner's final message containing the plan.

    Returns:
        List of todo dicts with 'content' and 'status' keys, or None if
        extraction fails.
    """
    lines = message.split("\n")
    todos: list[dict[str, str]] = []

    for line in lines:
        match = _TODO_PATTERN.match(line)
        if match:
            content = match.group(2).strip()
            if content:
                todos.append({
                    "content": content,
                    "status": "in_progress" if len(todos) == 0 else "pending",
                })

    return todos if todos else None


async def execute_planner_streaming(
    planner: CompiledStateGraph,
    task: str,
    adapter: Any,
) -> list[dict[str, str]] | None:
    """Execute planner with streaming output to Textual UI.

    Args:
        planner: The compiled planner agent graph.
        task: The task description to plan.
        adapter: The TextualUIAdapter for UI operations.

    Returns:
        List of todo dicts, or None if extraction fails.
    """
    from langchain_core.messages import AIMessageChunk, HumanMessage, ToolMessage

    from invincat_cli.widgets.messages import (
        AssistantMessage,
        ToolCallMessage,
    )

    stream_input = {"messages": [HumanMessage(content=task)]}

    pending_text = ""
    assistant_msg: AssistantMessage | None = None
    current_tool_msg: ToolCallMessage | None = None
    todos: list[dict[str, str]] | None = None
    final_content = ""

    try:
        async for chunk in planner.astream(
            stream_input,
            stream_mode=["messages", "updates"],
        ):
            if not isinstance(chunk, tuple):
                continue

            mode, data = chunk[0], chunk[1] if len(chunk) > 1 else None

            if mode == "updates":
                if isinstance(data, dict):
                    for key, value in data.items():
                        if key == "todos" and isinstance(value, list):
                            todos = [
                                {"content": t.get("content", ""), "status": t.get("status", "pending")}
                                for t in value
                                if t.get("content")
                            ]

            elif mode == "messages":
                if not isinstance(data, tuple) or len(data) < 2:
                    continue

                message, metadata = data[0], data[1]

                if isinstance(message, AIMessageChunk):
                    if hasattr(message, "content_blocks"):
                        for block in message.content_blocks:
                            if block.get("type") == "text":
                                text = block.get("text", "")
                                if text:
                                    pending_text += text
                                    if assistant_msg is None:
                                        msg_id = f"planner-{uuid.uuid4().hex[:8]}"
                                        assistant_msg = AssistantMessage(id=msg_id)
                                        await adapter._mount_message(assistant_msg)
                                    await assistant_msg.append_content(text)

                    if hasattr(message, "tool_calls") and message.tool_calls:
                        for tc in message.tool_calls:
                            if tc.get("name") == "write_todos":
                                tool_id = tc.get("id", str(uuid.uuid4()))
                                args = tc.get("args", {})
                                current_tool_msg = ToolCallMessage(
                                    "write_todos",
                                    args,
                                    tool_call_id=tool_id,
                                )
                                await adapter._mount_message(current_tool_msg)
                                raw_todos = args.get("todos", [])
                                if raw_todos:
                                    todos = [
                                        {"content": t.get("content", ""), "status": t.get("status", "pending")}
                                        for t in raw_todos
                                        if t.get("content")
                                    ]

                elif isinstance(message, ToolMessage):
                    tool_name = getattr(message, "name", "write_todos")
                    tool_status = getattr(message, "status", "success")
                    tool_content = str(message.content) if message.content else "(no output)"

                    if current_tool_msg:
                        if tool_status == "success":
                            current_tool_msg.set_success(tool_content)
                        else:
                            current_tool_msg.set_error(tool_content)
                        current_tool_msg = None

                elif hasattr(message, "content"):
                    content = message.content
                    if isinstance(content, str):
                        final_content = content
                    elif isinstance(content, list):
                        text_parts = []
                        for block in content:
                            if isinstance(block, dict) and "text" in block:
                                text_parts.append(block["text"])
                            elif isinstance(block, str):
                                text_parts.append(block)
                        final_content = "\n".join(text_parts)

    except Exception as e:
        logger.exception("Planner streaming failed")
        raise

    if not todos and final_content:
        todos = extract_todos_from_message(final_content)

    return todos
