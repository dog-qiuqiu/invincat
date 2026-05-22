"""Subagent progress tracking for streamed Textual execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from invincat_cli.textual_adapter.utils import normalize_tool_id


@dataclass
class SubagentTaskActivity:
    """UI state for one visible task-tool backed subagent run."""

    tool_call_id: str
    widget: Any
    subagent_type: str
    description: str
    namespaces: set[tuple] = field(default_factory=set)


class SubagentActivityTracker:
    """Map subgraph stream namespaces back to visible task tool widgets."""

    def __init__(self) -> None:
        self._tasks: dict[str, SubagentTaskActivity] = {}
        self._namespace_to_tool_id: dict[tuple, str] = {}

    def clear(self) -> None:
        """Clear all tracked task widgets and namespace mappings."""
        self._tasks.clear()
        self._namespace_to_tool_id.clear()

    def register_task(
        self,
        *,
        tool_call_id: str | int,
        widget: Any,
        args: dict[str, Any],
    ) -> None:
        """Register a task tool widget as a running subagent activity target."""
        normalized_id = normalize_tool_id(tool_call_id)
        if not normalized_id:
            return

        subagent_type = str(args.get("subagent_type") or "subagent").strip()
        description = str(args.get("description") or "").strip()
        activity = SubagentTaskActivity(
            tool_call_id=normalized_id,
            widget=widget,
            subagent_type=subagent_type or "subagent",
            description=description,
        )
        self._tasks[normalized_id] = activity
        _set_progress(widget, _start_detail(activity.subagent_type))

    def complete_task(self, tool_call_id: str | int | None) -> None:
        """Forget a task once the main task ToolMessage is processed."""
        normalized_id = normalize_tool_id(tool_call_id)
        if not normalized_id:
            return
        activity = self._tasks.pop(normalized_id, None)
        if activity is None:
            return
        for ns_key in list(activity.namespaces):
            self._namespace_to_tool_id.pop(ns_key, None)

    def observe_chunk(
        self,
        *,
        ns_key: tuple,
        stream_mode: str,
        data: Any,
    ) -> None:
        """Update the best matching task widget from a subagent stream chunk."""
        if not ns_key or not self._tasks:
            return

        activity = self._activity_for_namespace(ns_key)
        if activity is None:
            return

        detail = _activity_detail(stream_mode, data, activity.subagent_type)
        if detail:
            _set_progress(activity.widget, detail)

    def _activity_for_namespace(self, ns_key: tuple) -> SubagentTaskActivity | None:
        mapped_id = self._namespace_to_tool_id.get(ns_key)
        if mapped_id is not None:
            return self._tasks.get(mapped_id)

        ns_text = " ".join(str(part) for part in ns_key)
        matches = [
            activity
            for activity in self._tasks.values()
            if activity.subagent_type and activity.subagent_type in ns_text
        ]
        if len(matches) == 1:
            activity = matches[0]
        elif len(self._tasks) == 1:
            activity = next(iter(self._tasks.values()))
        else:
            unused = [
                activity
                for activity in self._tasks.values()
                if not activity.namespaces
            ]
            if len(unused) != 1:
                return None
            activity = unused[0]

        activity.namespaces.add(ns_key)
        self._namespace_to_tool_id[ns_key] = activity.tool_call_id
        return activity


def _set_progress(widget: Any, detail: str) -> None:
    set_progress = getattr(widget, "set_progress_detail", None)
    if callable(set_progress):
        set_progress(detail)


def _start_detail(subagent_type: str) -> str:
    return f"Starting {subagent_type} subagent"


def _activity_detail(stream_mode: str, data: Any, subagent_type: str) -> str | None:
    if stream_mode == "updates":
        return f"{subagent_type} subagent updating state"
    if stream_mode != "messages":
        return None
    if not isinstance(data, tuple) or len(data) != 2:
        return None

    message, _metadata = data
    tool_message_name = getattr(message, "name", None)
    if tool_message_name:
        return f"{subagent_type} finished {tool_message_name}"

    tool_name = _tool_name_from_message(message)
    if tool_name:
        return f"{subagent_type} calling {tool_name}"

    content_blocks = getattr(message, "content_blocks", None)
    if content_blocks:
        return f"{subagent_type} subagent working"
    return None


def _tool_name_from_message(message: Any) -> str | None:
    for block in getattr(message, "content_blocks", None) or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") in {"tool_call", "tool_call_chunk"}:
            name = block.get("name")
            if name:
                return str(name)

    for attr in ("tool_call_chunks", "tool_calls"):
        for call in getattr(message, attr, None) or []:
            name = call.get("name") if isinstance(call, dict) else getattr(call, "name", None)
            if name:
                return str(name)
    return None
