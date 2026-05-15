"""SQLite schema and migrations for the scheduler store."""

from __future__ import annotations

import sqlite3

DDL = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    prompt      TEXT NOT NULL,
    cron        TEXT NOT NULL,
    timezone    TEXT NOT NULL DEFAULT 'UTC',
    cwd         TEXT NOT NULL,
    delivery    TEXT NOT NULL DEFAULT '{}',
    report      TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    next_run_at TEXT,
    last_run_at TEXT,
    last_status TEXT NOT NULL DEFAULT 'never',
    last_error  TEXT,
    run_count   INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    misfire_policy TEXT NOT NULL DEFAULT 'run_once',
    schedule_type TEXT NOT NULL DEFAULT 'recurring',
    run_at TEXT,
    delete_after_run INTEGER NOT NULL DEFAULT 0,
    timeout_seconds INTEGER NOT NULL DEFAULT 600
);

CREATE TABLE IF NOT EXISTS scheduled_task_runs (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    started_at   TEXT,
    finished_at  TEXT,
    status       TEXT NOT NULL DEFAULT 'running',
    report_path  TEXT,
    error        TEXT,
    thread_id    TEXT,
    cwd          TEXT NOT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'none',
    delivery_error TEXT,
    delivered_at TEXT,
    delivery_attempts INTEGER NOT NULL DEFAULT 0,
    runner_id TEXT,
    runner_kind TEXT,
    runner_pid INTEGER,
    FOREIGN KEY (task_id) REFERENCES scheduled_tasks(id)
);
"""

RUN_COLUMN_MIGRATIONS = {
    "delivery_status": "ALTER TABLE scheduled_task_runs ADD COLUMN delivery_status TEXT NOT NULL DEFAULT 'none'",
    "delivery_error": "ALTER TABLE scheduled_task_runs ADD COLUMN delivery_error TEXT",
    "delivered_at": "ALTER TABLE scheduled_task_runs ADD COLUMN delivered_at TEXT",
    "delivery_attempts": "ALTER TABLE scheduled_task_runs ADD COLUMN delivery_attempts INTEGER NOT NULL DEFAULT 0",
    "runner_id": "ALTER TABLE scheduled_task_runs ADD COLUMN runner_id TEXT",
    "runner_kind": "ALTER TABLE scheduled_task_runs ADD COLUMN runner_kind TEXT",
    "runner_pid": "ALTER TABLE scheduled_task_runs ADD COLUMN runner_pid INTEGER",
}

TASK_COLUMN_MIGRATIONS = {
    "schedule_type": "ALTER TABLE scheduled_tasks ADD COLUMN schedule_type TEXT NOT NULL DEFAULT 'recurring'",
    "run_at": "ALTER TABLE scheduled_tasks ADD COLUMN run_at TEXT",
    "delete_after_run": "ALTER TABLE scheduled_tasks ADD COLUMN delete_after_run INTEGER NOT NULL DEFAULT 0",
    "timeout_seconds": "ALTER TABLE scheduled_tasks ADD COLUMN timeout_seconds INTEGER NOT NULL DEFAULT 600",
}


def migrate(conn: sqlite3.Connection) -> None:
    """Apply additive scheduler schema migrations."""
    task_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(scheduled_tasks)").fetchall()
    }
    for column, sql in TASK_COLUMN_MIGRATIONS.items():
        if column not in task_columns:
            conn.execute(sql)

    run_columns = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(scheduled_task_runs)").fetchall()
    }
    for column, sql in RUN_COLUMN_MIGRATIONS.items():
        if column not in run_columns:
            conn.execute(sql)
