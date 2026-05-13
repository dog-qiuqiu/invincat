"""Schedule manager modal screen for /schedule command."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from textual.binding import Binding, BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.screen import ModalScreen
from textual.widgets import Static

if TYPE_CHECKING:
    from textual.app import ComposeResult
    from textual.events import Click

    from invincat_cli.scheduler.models import ScheduledTask
    from invincat_cli.scheduler.store import SchedulerStore

from invincat_cli import theme
from invincat_cli.config import get_glyphs, is_ascii_mode
from invincat_cli.i18n import t

logger = logging.getLogger(__name__)


@dataclass
class ScheduleAction:
    """Returned by ScheduleManagerScreen to indicate what the app should do."""

    kind: str
    """One of: 'run_now', 'pause', 'resume', 'delete'."""

    task_id: str


class TaskRow(Static):
    """A single task row in the schedule list."""

    def __init__(
        self,
        task: ScheduledTask,
        index: int,
        *,
        selected: bool = False,
    ) -> None:
        super().__init__("", classes="schedule-task-row")
        self._task = task
        self.index = index
        self._selected = selected
        self._update_label()

    def _update_label(self) -> None:
        from invincat_cli.scheduler.parser import describe_schedule

        task = self._task

        enabled_icon = "●" if task.enabled else "○"
        status_map = {
            "never": "",
            "success": "[green]✓[/green]",
            "failed": "[red]✗[/red]",
            "running": "[yellow]▶[/yellow]",
            "missed": "[yellow]![/yellow]",
            "timeout": "[red]T[/red]",
        }
        status_icon = status_map.get(task.last_status, "")
        schedule_desc = describe_schedule(task.cron, task.timezone)
        next_run = (task.next_run_at or "—")[:16].replace("T", " ")
        short_id = task.id[:8]

        if self._selected:
            label = Content.from_markup(
                f"[bold cyan] {enabled_icon} $title[/bold cyan]"
                f"  [dim cyan]$schedule  next: $next_run  $status  [$short_id][/dim cyan]",
                title=task.title,
                schedule=schedule_desc,
                next_run=next_run,
                status=status_icon or task.last_status,
                short_id=short_id,
            )
        else:
            dim_color = "dim" if not task.enabled else ""
            label = Content.from_markup(
                f"[{dim_color}] {enabled_icon} $title"
                f"  [dim]$schedule  next: $next_run  $status  [$short_id][/dim][/{dim_color}]",
                title=task.title,
                schedule=schedule_desc,
                next_run=next_run,
                status=status_icon or task.last_status,
                short_id=short_id,
            )
        self.update(label)

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._update_label()

    def refresh_task(self, task: ScheduledTask) -> None:
        self._task = task
        self._update_label()

    def on_click(self, event: Click) -> None:
        event.stop()
        screen = self.screen
        if isinstance(screen, ScheduleManagerScreen):
            screen._move_to(self.index)


class ScheduleManagerScreen(ModalScreen["ScheduleAction | None"]):
    """Modal screen for viewing and managing scheduled tasks.

    Keys:
        ↑/↓ / j/k  Navigate task list
        Enter       Run selected task now
        p           Pause / resume selected task
        d           Delete selected task
        Esc         Close
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("up", "move_up", "Up", show=False, priority=True),
        Binding("k", "move_up", "Up", show=False, priority=True),
        Binding("down", "move_down", "Down", show=False, priority=True),
        Binding("j", "move_down", "Down", show=False, priority=True),
        Binding("enter", "run_now", "Run now", show=False, priority=True),
        Binding("p", "toggle_pause", "Pause/Resume", show=False, priority=True),
        Binding("d", "delete_task", "Delete", show=False, priority=True),
        Binding("r", "refresh", "Refresh", show=False, priority=True),
        Binding("escape", "close", "Close", show=False, priority=True),
    ]

    CSS = """
    ScheduleManagerScreen {
        align: center middle;
        background: transparent;
    }

    ScheduleManagerScreen > Vertical {
        width: 92;
        max-width: 96%;
        height: auto;
        max-height: 88%;
        background: $surface;
        border: solid $primary;
        padding: 0 0;
    }

    ScheduleManagerScreen .schedule-title {
        text-style: bold;
        color: $primary;
        text-align: center;
        padding: 1 2 0 2;
    }

    ScheduleManagerScreen .schedule-divider {
        height: 1;
        color: $primary-darken-2;
        border-bottom: solid $primary-darken-2;
        margin: 0 0;
    }

    ScheduleManagerScreen .schedule-task-list {
        height: auto;
        max-height: 16;
        min-height: 3;
        padding: 0 2;
    }

    ScheduleManagerScreen .schedule-empty {
        color: $text-muted;
        text-style: italic;
        text-align: center;
        padding: 2 2;
    }

    ScheduleManagerScreen .schedule-status-bar {
        height: 1;
        background: $primary-background;
        color: $text-muted;
        padding: 0 2;
        margin-top: 1;
    }

    ScheduleManagerScreen .schedule-keybindings {
        height: auto;
        padding: 1 2 0 2;
    }

    ScheduleManagerScreen .schedule-keybinding-row {
        height: 1;
        color: $text-muted;
    }

    ScheduleManagerScreen .schedule-key {
        color: $primary;
        text-style: bold;
        width: 20;
        min-width: 20;
    }

    ScheduleManagerScreen .schedule-key-desc {
        color: $text-muted;
    }

    ScheduleManagerScreen .schedule-footer {
        height: 1;
        color: $text-muted;
        text-style: italic;
        text-align: center;
        padding: 0 2 1 2;
        margin-top: 1;
    }
    """

    def __init__(self, store: SchedulerStore) -> None:
        super().__init__()
        self._store = store
        self._tasks: list[ScheduledTask] = []
        self._selected_index: int = 0
        self._confirm_delete: str | None = None

    def compose(self) -> ComposeResult:
        glyphs = get_glyphs()
        key_bindings = [
            (f"{glyphs.arrow_up} / {glyphs.arrow_down}", t("schedule.manager.key.navigate")),
            ("Enter",                                     t("schedule.manager.key.run_now")),
            ("p",                                         t("schedule.manager.key.pause_resume")),
            ("d  d",                                      t("schedule.manager.key.delete")),
            ("r",                                         t("schedule.manager.key.refresh")),
            ("Esc",                                       t("schedule.manager.key.close")),
        ]
        with Vertical():
            yield Static(t("schedule.manager.title"), classes="schedule-title")
            yield Static("", classes="schedule-divider")
            with VerticalScroll(classes="schedule-task-list", id="task-list-scroll"):
                yield Static("", id="task-list-container")
            yield Static("", classes="schedule-status-bar", id="status-bar")
            yield Static("", classes="schedule-divider")
            with Vertical(classes="schedule-keybindings"):
                for key, desc in key_bindings:
                    with Horizontal(classes="schedule-keybinding-row"):
                        yield Static(
                            Content.from_markup(f"[bold cyan]  {key}[/bold cyan]"),
                            classes="schedule-key",
                        )
                        yield Static(
                            Content.from_markup(f"[dim]{desc}[/dim]"),
                            classes="schedule-key-desc",
                        )
            yield Static(
                Content.from_markup(
                    f"[dim italic]{t('schedule.manager.footer_hint')}[/dim italic]"
                ),
                classes="schedule-footer",
            )

    def on_mount(self) -> None:
        if is_ascii_mode():
            container = self.query_one(Vertical)
            colors = theme.get_theme_colors(self)
            container.styles.border = ("ascii", colors.primary)
        self._load_tasks()
        self.set_interval(30, self._auto_refresh)

    def _load_tasks(self) -> None:
        self._tasks = self._store.list_tasks()
        self._selected_index = max(0, min(self._selected_index, len(self._tasks) - 1))
        self._confirm_delete = None
        self._render_list()
        self._update_status()

    def _auto_refresh(self) -> None:
        """Refresh task data from the store without disrupting UI state."""
        self._tasks = self._store.list_tasks()
        self._selected_index = max(0, min(self._selected_index, len(self._tasks) - 1))
        self._render_list()
        if not self._confirm_delete:
            self._update_status()

    def _render_list(self) -> None:
        container = self.query_one("#task-list-container", Static)
        if not self._tasks:
            container.update(
                Content.from_markup(
                    f"[dim italic]{t('schedule.manager.empty')}[/dim italic]"
                )
            )
            return

        status_map = {
            "never":   t("schedule.task.status.never"),
            "success": t("schedule.task.status.success"),
            "failed":  t("schedule.task.status.failed"),
            "running": t("schedule.task.status.running"),
            "missed":  t("schedule.task.status.missed"),
            "timeout": t("schedule.task.status.timeout"),
        }
        next_label = t("schedule.manager.next_run_label")

        lines: list[str] = []
        for i, task in enumerate(self._tasks):
            selected = i == self._selected_index
            from invincat_cli.scheduler.display import (
                describe_schedule_for_display,
                format_schedule_time_for_display,
            )

            enabled_icon = "●" if task.enabled else "○"
            status_str = status_map.get(task.last_status, task.last_status)
            schedule_desc = describe_schedule_for_display(
                task.cron,
                task.timezone,
                task.schedule_type,
            )
            next_run = format_schedule_time_for_display(
                task.next_run_at,
                task.timezone,
                missing="—",
            ).replace("T", " ")
            short_id = task.id[:8]

            if selected:
                line = (
                    f"[bold cyan reverse] {enabled_icon} {task.title}"
                    f"  {schedule_desc}  {next_label} {next_run}  {status_str}  [{short_id}] [/bold cyan reverse]"
                )
            elif not task.enabled:
                line = (
                    f"[dim] {enabled_icon} {task.title}"
                    f"  {schedule_desc}  {next_label} {next_run}  {status_str}  [{short_id}][/dim]"
                )
            else:
                line = (
                    f" {enabled_icon} {task.title}"
                    f"  [dim]{schedule_desc}  {next_label} {next_run}  {status_str}  [{short_id}][/dim]"
                )
            lines.append(line)

        container.update(Content.from_markup("\n".join(lines)))

    def _update_status(self, message: str = "") -> None:
        bar = self.query_one("#status-bar", Static)
        if message:
            bar.update(Content.from_markup(f" [bold]{message}[/bold]"))
        elif not self._tasks:
            bar.update("")
        else:
            task = self._tasks[self._selected_index]
            state_label = (
                f"[green]{t('schedule.manager.status.enabled')}[/green]"
                if task.enabled
                else f"[dim]{t('schedule.manager.status.paused')}[/dim]"
            )
            runs_label = t("schedule.manager.status.runs").format(n=task.run_count)
            fail_label = t("schedule.manager.status.failures").format(n=task.failure_count) if task.failure_count else ""
            last_date = task.last_run_at[:10] if task.last_run_at else t("schedule.manager.status.never")
            last_label = t("schedule.manager.status.last_run").format(date=last_date)
            parts = [f" {task.title}", state_label, runs_label]
            if fail_label:
                parts.append(f"[red]{fail_label}[/red]")
            if task.last_run_at:
                from invincat_cli.scheduler.display import (
                    format_schedule_time_for_display,
                )

                local_last = format_schedule_time_for_display(
                    task.last_run_at,
                    task.timezone,
                    missing=t("schedule.manager.status.never"),
                )
                last_label = t("schedule.manager.status.last_run").format(
                    date=local_last[:10]
                )
            parts.append(f"[dim]{last_label}[/dim]")
            bar.update(Content.from_markup("  ·  ".join(parts)))

    def _move_to(self, index: int) -> None:
        if not self._tasks:
            return
        old = self._selected_index
        self._selected_index = max(0, min(index, len(self._tasks) - 1))
        if old != self._selected_index:
            self._confirm_delete = None
        self._render_list()
        self._update_status()
        self._scroll_to_selected()

    def _scroll_to_selected(self) -> None:
        """Scroll the task list so the selected row stays visible."""
        try:
            scroll = self.query_one("#task-list-scroll", VerticalScroll)
            visible_height = scroll.size.height
            if visible_height <= 0:
                return
            # Keep selected line vertically centred in the visible window.
            target_y = max(0, self._selected_index - visible_height // 2)
            scroll.scroll_to(y=target_y, animate=False)
        except Exception:
            pass

    def action_move_up(self) -> None:
        self._move_to(self._selected_index - 1)

    def action_move_down(self) -> None:
        self._move_to(self._selected_index + 1)

    def action_run_now(self) -> None:
        if not self._tasks:
            return
        self._confirm_delete = None
        task = self._tasks[self._selected_index]
        self.dismiss(ScheduleAction(kind="run_now", task_id=task.id))

    def action_toggle_pause(self) -> None:
        if not self._tasks:
            return
        self._confirm_delete = None
        task = self._tasks[self._selected_index]
        new_state = not task.enabled
        self._store.set_task_enabled(task.id, new_state)
        verb = t("schedule.manager.action.resumed") if new_state else t("schedule.manager.action.paused")
        self._update_status(f"{task.title} — {verb}")
        self._load_tasks()

    def action_delete_task(self) -> None:
        if not self._tasks:
            return
        task = self._tasks[self._selected_index]
        if self._confirm_delete == task.id:
            self._store.delete_task(task.id)
            self._confirm_delete = None
            self._selected_index = max(0, self._selected_index - 1)
            self._load_tasks()
            self._update_status(t("schedule.manager.action.deleted"))
        else:
            self._confirm_delete = task.id
            self._update_status(t("schedule.manager.confirm_delete").format(title=task.title))

    def action_refresh(self) -> None:
        self._load_tasks()
        self._update_status(t("schedule.manager.action.refreshed"))

    def action_close(self) -> None:
        self.dismiss(None)

    def on_key(self, event: object) -> None:
        # Any key other than 'd' cancels a pending delete confirmation
        key = getattr(event, "key", "")
        if key != "d" and self._confirm_delete is not None:
            self._confirm_delete = None
            self._update_status()
