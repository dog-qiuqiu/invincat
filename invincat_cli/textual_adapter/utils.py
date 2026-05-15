"""Small helper functions for Textual agent streaming."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from invincat_cli.core.session_stats import SessionStats, format_token_count
from invincat_cli.presentation.formatting import format_duration

if TYPE_CHECKING:
    from rich.console import Console


def print_usage_table(
    stats: SessionStats,
    wall_time: float,
    console: Console,
) -> None:
    """Print a model-usage stats table to a Rich console."""
    from rich.table import Table

    has_time = wall_time >= 0.1  # noqa: PLR2004
    if not (stats.request_count or stats.input_tokens or has_time):
        return

    if stats.per_model:
        multi_model = len(stats.per_model) > 1

        table = Table(
            show_header=True,
            header_style="bold",
            box=None,
            padding=(0, 2, 0, 0),
            show_edge=False,
        )
        table.add_column("Model", style="dim")
        table.add_column("Reqs", justify="right", style="dim")
        table.add_column("InputTok", justify="right", style="dim")
        table.add_column("OutputTok", justify="right", style="dim")

        if multi_model:
            for model_name, ms in stats.per_model.items():
                table.add_row(
                    model_name,
                    str(ms.request_count),
                    format_token_count(ms.input_tokens),
                    format_token_count(ms.output_tokens),
                )
            table.add_row(
                "Total",
                str(stats.request_count),
                format_token_count(stats.input_tokens),
                format_token_count(stats.output_tokens),
            )
        else:
            model_label = next(iter(stats.per_model))
            table.add_row(
                model_label,
                str(stats.request_count),
                format_token_count(stats.input_tokens),
                format_token_count(stats.output_tokens),
            )

        console.print()
        console.print("[bold]Usage Stats[/bold]")
        console.print(table)
    if has_time:
        console.print()
        console.print(
            f"Agent active  {format_duration(wall_time)}",
            style="dim",
            highlight=False,
        )


def is_summarization_chunk(metadata: dict | None) -> bool:
    """Return whether a streamed message chunk belongs to summarization."""
    if metadata is None:
        return False
    return metadata.get("lc_source") == "summarization"


def is_internal_model_chunk(metadata: dict | None) -> bool:
    """Return whether a streamed message chunk belongs to internal middleware."""
    if metadata is None:
        return False
    return metadata.get("lc_source") == "memory_agent"


def normalize_tool_id(tool_id: Any) -> str | None:
    """Normalize a tool call ID to a string for consistent comparison."""
    if tool_id is None:
        return None
    return str(tool_id)


def is_transient_stream_error(exc: BaseException) -> bool:
    """Return True for errors safe to retry before any chunks were received."""
    err_str = str(exc).lower()
    err_type = type(exc).__name__.lower()
    transient_status_codes = ("429", "500", "502", "503", "504")
    if any(code in err_str for code in transient_status_codes):
        return True
    transient_keywords = (
        "rate_limit",
        "ratelimit",
        "rate limit",
        "connect",
        "timeout",
        "network",
        "unavailable",
        "overloaded",
        "capacity",
        "reset by peer",
        "broken pipe",
        "eof",
        "connection closed",
        "connection error",
    )
    if any(kw in err_str or kw in err_type for kw in transient_keywords):
        return True
    return False


def read_mentioned_file(file_path: Path, max_embed_bytes: int) -> str:
    """Read a mentioned file for inline embedding."""
    file_size = file_path.stat().st_size
    if file_size > max_embed_bytes:
        size_kb = file_size // 1024
        return (
            f"\n### {file_path.name}\n"
            f"Path: `{file_path}`\n"
            f"Size: {size_kb}KB (too large to embed, "
            "use read_file tool to view)"
        )
    content = file_path.read_text(encoding="utf-8")
    return f"\n### {file_path.name}\nPath: `{file_path}`\n```\n{content}\n```"
