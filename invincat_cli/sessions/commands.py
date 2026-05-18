"""CLI command rendering for session/thread management."""

from __future__ import annotations

from typing import TYPE_CHECKING

from invincat_cli import sessions as _sessions

if TYPE_CHECKING:
    from invincat_cli.io.output import OutputFormat


async def list_threads_command(
    agent_name: str | None = None,
    limit: int | None = None,
    sort_by: str | None = None,
    branch: str | None = None,
    verbose: bool = False,
    relative: bool | None = None,
    *,
    output_format: OutputFormat = "text",
) -> None:
    """CLI handler for `invincat-cli threads list`."""
    from invincat_cli.model_config import (
        load_thread_relative_time,
        load_thread_sort_order,
    )

    if sort_by is None:
        raw = load_thread_sort_order()
        sort_by = "created" if raw == "created_at" else "updated"
    if relative is None:
        relative = load_thread_relative_time()

    fmt_ts = (
        _sessions.format_relative_timestamp if relative else _sessions.format_timestamp
    )
    limit = _sessions.get_thread_limit() if limit is None else max(1, limit)

    threads = await _sessions.list_threads(
        agent_name,
        limit=limit,
        include_message_count=True,
        sort_by=sort_by,
        branch=branch,
    )

    if verbose and threads:
        await _sessions.populate_thread_checkpoint_details(
            threads,
            include_message_count=False,
            include_initial_prompt=True,
        )

    if output_format == "json":
        from invincat_cli.io.output import write_json

        write_json("threads list", list(threads))
        return

    from rich.markup import escape as escape_markup
    from rich.table import Table

    from invincat_cli import theme
    from invincat_cli.config import console
    from invincat_cli.core.version import CLI_COMMAND

    if not threads:
        filters = []
        if agent_name:
            filters.append(f"agent '{escape_markup(agent_name)}'")
        if branch:
            filters.append(f"branch '{escape_markup(branch)}'")
        if filters:
            console.print(
                f"[yellow]No threads found for {' and '.join(filters)}.[/yellow]"
            )
        else:
            console.print("[yellow]No threads found.[/yellow]")
        console.print(f"[dim]Start a conversation with: {CLI_COMMAND}[/dim]")
        return

    title_parts = []
    if agent_name:
        title_parts.append(f"agent '{escape_markup(agent_name)}'")
    if branch:
        title_parts.append(f"branch '{escape_markup(branch)}'")

    title_filter = f" for {' and '.join(title_parts)}" if title_parts else ""
    sort_label = "created" if sort_by == "created" else "updated"
    title = f"Recent Threads{title_filter} (last {limit}, by {sort_label})"

    table = Table(title=title, show_header=True, header_style=f"bold {theme.PRIMARY}")
    table.add_column("Thread ID", style="bold")
    table.add_column("Agent")
    table.add_column("Messages", justify="right")
    if verbose:
        table.add_column("Created")
    table.add_column("Updated" if sort_by == "updated" else "Last Used")
    if verbose:
        table.add_column("Branch")
        table.add_column("Location")
        table.add_column("Prompt", max_width=40, no_wrap=True)

    prompt_max = 40

    for thread in threads:
        row: list[str] = [
            thread["thread_id"],
            thread["agent_name"] or "unknown",
            str(thread.get("message_count", 0)),
        ]
        if verbose:
            row.append(fmt_ts(thread.get("created_at")))
        row.append(fmt_ts(thread.get("updated_at")))
        if verbose:
            prompt = " ".join((thread.get("initial_prompt") or "").split())
            if len(prompt) > prompt_max:
                prompt = prompt[: prompt_max - 3] + "..."
            row.extend(
                [
                    thread.get("git_branch") or "",
                    _sessions.format_path(thread.get("cwd")),
                    prompt,
                ]
            )
        table.add_row(*row)

    console.print()
    console.print(table)
    if len(threads) >= limit:
        console.print(
            f"[dim]Showing last {limit} threads. "
            "Override with -n/--limit or DA_CLI_RECENT_THREADS.[/dim]"
        )
    console.print()


async def delete_thread_command(
    thread_id: str,
    *,
    dry_run: bool = False,
    output_format: OutputFormat = "text",
) -> None:
    """CLI handler for `invincat-cli threads delete`."""
    if dry_run:
        exists = await _sessions.thread_exists(thread_id)
        if output_format == "json":
            from invincat_cli.io.output import write_json

            write_json(
                "threads delete",
                {"thread_id": thread_id, "exists": exists, "dry_run": True},
            )
            return

        from rich.markup import escape as escape_markup

        from invincat_cli.config import console

        escaped_id = escape_markup(thread_id)
        if exists:
            console.print(f"Would delete thread '{escaped_id}'.")
        else:
            console.print(f"Thread '{escaped_id}' not found. Nothing to delete.")
        console.print("No changes made.", style="dim")
        return

    deleted = await _sessions.delete_thread(thread_id)

    if output_format == "json":
        from invincat_cli.io.output import write_json

        write_json("threads delete", {"thread_id": thread_id, "deleted": deleted})
        return

    from rich.markup import escape as escape_markup

    from invincat_cli import theme
    from invincat_cli.config import console

    escaped_id = escape_markup(thread_id)
    if deleted:
        console.print(f"[green]Thread '{escaped_id}' deleted.[/green]")
    else:
        console.print(
            f"Thread '{escaped_id}' not found or already deleted.",
            style=theme.MUTED,
        )
