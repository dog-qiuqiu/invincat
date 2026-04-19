"""Approve plan middleware for interactive plan confirmation."""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, Literal

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from langchain.agents.middleware.types import ToolCallRequest
    from langchain_core.runnables import RunnableConfig

    from invincat_cli.widgets.approve import ApproveResult


APPROVE_PLAN_SYSTEM_PROMPT: str = """
## Plan Approval

When you have a plan ready for user approval, use the `approve_plan` tool.
This will display the plan to the user and wait for their confirmation.

- If the user approves, you will receive "approved" and should proceed with execution.
- If the user rejects, you will receive "rejected" and should ask for feedback to refine the plan.

Always use `approve_plan` after generating a plan with `write_todos`.
"""

APPROVE_PLAN_TOOL_DESCRIPTION: str = (
    "Display a plan to the user for approval. "
    "Use this after drafting a plan with write_todos. "
    "Returns 'approved' if the user confirms, or 'rejected' if they want to refine the plan."
)


class ApprovePlanRequest(TypedDict):
    """Request to approve a plan."""

    type: Literal["approve_plan"]
    todos: list[dict[str, Any]]
    tool_call_id: str


class TodoItem(TypedDict):
    """A single todo item."""

    content: str
    status: Literal["pending", "in_progress", "completed"]


def _validate_todos(todos: list[TodoItem]) -> None:
    """Validate todo items.

    Args:
        todos: List of todo items to validate.

    Raises:
        ValueError: If todos are invalid.
    """
    if not todos:
        raise ValueError("Todos list cannot be empty")

    valid_statuses = {"pending", "in_progress", "completed"}
    for i, todo in enumerate(todos):
        if "content" not in todo:
            raise ValueError(f"Todo item {i} missing 'content' field")
        if "status" not in todo:
            raise ValueError(f"Todo item {i} missing 'status' field")
        if todo["status"] not in valid_statuses:
            raise ValueError(
                f"Todo item {i} has invalid status '{todo['status']}'. "
                f"Must be one of: {valid_statuses}"
            )


def _parse_approval_response(
    response: dict[str, Any],
    tool_call_id: str,
) -> Command[Any]:
    """Parse the user's approval response.

    Args:
        response: User's response from the interrupt.
        tool_call_id: Tool call identifier.

    Returns:
        Command containing the result as a ToolMessage.
    """
    result_type = response.get("type", "rejected")

    if result_type == "approved":
        result_text = "approved"
    else:
        result_text = "rejected"

    return Command(
        update={
            "messages": [ToolMessage(result_text, tool_call_id=tool_call_id)],
        }
    )


class ApprovePlanMiddleware(AgentMiddleware[Any, Any, Any]):
    """Middleware that provides an approve_plan tool for plan confirmation.

    This middleware adds an `approve_plan` tool that allows agents to present
    a plan to the user for approval. The tool uses LangGraph interrupts to
    pause execution and wait for user confirmation.
    """

    def __init__(
        self,
        *,
        system_prompt: str = APPROVE_PLAN_SYSTEM_PROMPT,
        tool_description: str = APPROVE_PLAN_TOOL_DESCRIPTION,
    ) -> None:
        """Initialize ApprovePlanMiddleware.

        Args:
            system_prompt: System-level instructions injected into every LLM
                request to guide `approve_plan` usage.
            tool_description: Description string passed to the `approve_plan` tool
                decorator, visible to the LLM in the tool schema.
        """
        super().__init__()
        self.system_prompt = system_prompt
        self.tool_description = tool_description

        @tool(description=self.tool_description)
        def _approve_plan(
            todos: list[TodoItem],
            tool_call_id: Annotated[str, "InjectedToolCallId"],
        ) -> Command[Any]:
            """Present a plan to the user for approval.

            Args:
                todos: List of todo items to present for approval.
                tool_call_id: Tool call identifier injected by LangChain.

            Returns:
                `Command` containing the user's approval decision.
            """
            _validate_todos(todos)
            approve_request: ApprovePlanRequest = {
                "type": "approve_plan",
                "todos": todos,
                "tool_call_id": tool_call_id,
            }
            response = interrupt(approve_request)
            return _parse_approval_response(response, tool_call_id)

        _approve_plan.name = "approve_plan"
        self.tools = [_approve_plan]

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command[Any]],
    ) -> ToolMessage | Command[Any]:
        """Pass through tool calls unchanged.

        Args:
            request: The tool call request being processed.
            handler: The next handler in the middleware chain.

        Returns:
            The tool execution result.
        """
        return handler(request)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """Pass through tool calls unchanged (async).

        Args:
            request: The tool call request being processed.
            handler: The next handler in the middleware chain.

        Returns:
            The tool execution result.
        """
        return await handler(request)

    def wrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Any],
    ) -> Any:
        """Inject the approve_plan system prompt.

        Returns:
            Model response from the wrapped handler.
        """
        if request.system_message is not None:
            new_system_content = [
                *request.system_message.content_blocks,
                {"type": "text", "text": f"\n\n{self.system_prompt}"},
            ]
        else:
            new_system_content = [{"type": "text", "text": self.system_prompt}]
        new_system_message = SystemMessage(
            content=new_system_content  # type: ignore[arg-type]
        )
        return handler(request.override(system_message=new_system_message))

    async def awrap_model_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[Any]],
    ) -> Any:
        """Inject the approve_plan system prompt (async).

        Returns:
            Model response from the wrapped handler.
        """
        if request.system_message is not None:
            new_system_content = [
                *request.system_message.content_blocks,
                {"type": "text", "text": f"\n\n{self.system_prompt}"},
            ]
        else:
            new_system_content = [{"type": "text", "text": self.system_prompt}]
        new_system_message = SystemMessage(
            content=new_system_content  # type: ignore[arg-type]
        )
        return await handler(request.override(system_message=new_system_message))
