from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from invincat_cli import sessions


class _FakeConsole:
    def __init__(self) -> None:
        self.messages: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def print(self, *args: Any, **kwargs: Any) -> None:
        self.messages.append((args, kwargs))

    @property
    def text(self) -> str:
        return "\n".join(str(args[0]) for args, _kwargs in self.messages if args)


def test_generate_thread_id_uses_uuid7(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sys.modules, "uuid_utils", SimpleNamespace(uuid7=lambda: "uuid7-value")
    )

    assert sessions.generate_thread_id() == "uuid7-value"


def test_create_jsonplus_serializer() -> None:
    assert hasattr(sessions._create_jsonplus_serializer(), "loads_typed")


def test_get_checkpointer_uses_async_sqlite_saver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    module = ModuleType("langgraph.checkpoint.sqlite.aio")
    events: list[tuple[str, str | None]] = []

    class FakeSaver:
        def __init__(self, path: str) -> None:
            self.path = path

        @classmethod
        def from_conn_string(cls, path: str) -> FakeSaver:
            events.append(("from_conn_string", path))
            return cls(path)

        async def __aenter__(self) -> str:
            events.append(("enter", self.path))
            return "checkpointer"

        async def __aexit__(self, *_args: object) -> None:
            events.append(("exit", None))

    setattr(module, "AsyncSqliteSaver", FakeSaver)
    monkeypatch.setitem(sys.modules, "langgraph.checkpoint.sqlite.aio", module)
    monkeypatch.setattr(sessions, "get_db_path", lambda: tmp_path / "sessions.db")
    monkeypatch.setattr(
        sessions, "_patch_aiosqlite", lambda: events.append(("patch", None))
    )

    async def run() -> None:
        async with sessions.get_checkpointer() as checkpointer:
            assert checkpointer == "checkpointer"

    asyncio.run(run())

    assert events == [
        ("patch", None),
        ("from_conn_string", str(tmp_path / "sessions.db")),
        ("enter", str(tmp_path / "sessions.db")),
        ("exit", None),
    ]


def test_patch_aiosqlite_adds_is_alive_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import aiosqlite

    monkeypatch.setattr(sessions, "_aiosqlite_patched", False)
    monkeypatch.delattr(aiosqlite.Connection, "is_alive", raising=False)

    sessions._patch_aiosqlite()

    assert sessions._aiosqlite_patched
    assert aiosqlite.Connection.is_alive(  # type: ignore[attr-defined]
        SimpleNamespace(_running=True, _connection=object())
    )
    assert not aiosqlite.Connection.is_alive(  # type: ignore[attr-defined]
        SimpleNamespace(_running=False, _connection=object())
    )


def test_timestamp_formatters_handle_valid_invalid_and_relative_values() -> None:
    now = datetime.now(UTC)

    assert sessions.format_timestamp(None) == ""
    assert sessions.format_timestamp("not-a-date") == ""
    assert sessions.format_timestamp("2026-05-14T10:15:00+00:00")

    assert sessions.format_relative_timestamp(None) == ""
    assert sessions.format_relative_timestamp("not-a-date") == ""
    assert (
        sessions.format_relative_timestamp((now + timedelta(seconds=5)).isoformat())
        == "just now"
    )
    assert sessions.format_relative_timestamp(
        (now - timedelta(seconds=30)).isoformat()
    ).endswith("s ago")
    assert (
        sessions.format_relative_timestamp((now - timedelta(minutes=5)).isoformat())
        == "5m ago"
    )
    assert (
        sessions.format_relative_timestamp((now - timedelta(hours=2)).isoformat())
        == "2h ago"
    )
    assert (
        sessions.format_relative_timestamp((now - timedelta(days=3)).isoformat())
        == "3d ago"
    )
    assert (
        sessions.format_relative_timestamp((now - timedelta(days=60)).isoformat())
        == "2mo ago"
    )
    assert (
        sessions.format_relative_timestamp((now - timedelta(days=730)).isoformat())
        == "2y ago"
    )


def test_format_path_shortens_home_paths(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(sessions.Path, "home", lambda: home)

    assert sessions.format_path(None) == ""
    assert sessions.format_path(str(home)) == "~"
    assert sessions.format_path(str(home / "project")) == "~/project"
    assert sessions.format_path("/var/tmp/project") == "/var/tmp/project"

    monkeypatch.setattr(
        sessions.Path,
        "home",
        lambda: (_ for _ in ()).throw(RuntimeError("no home")),
    )
    assert sessions.format_path("/tmp/project") == "/tmp/project"


def test_get_db_path_migrates_legacy_database_and_caches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    legacy_dir = home / ".deepagents"
    legacy_dir.mkdir(parents=True)
    legacy_db = legacy_dir / "sessions.db"
    legacy_db.write_text("legacy", encoding="utf-8")
    monkeypatch.setattr(sessions.Path, "home", lambda: home)
    monkeypatch.setattr(sessions, "_db_path", None)

    db_path = sessions.get_db_path()

    assert db_path == home / ".invincat" / "sessions.db"
    assert db_path.read_text(encoding="utf-8") == "legacy"
    legacy_db.write_text("changed", encoding="utf-8")
    assert sessions.get_db_path() == db_path
    assert db_path.read_text(encoding="utf-8") == "legacy"


def test_get_db_path_tolerates_failed_legacy_migration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    home = tmp_path / "home"
    legacy_dir = home / ".deepagents"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "sessions.db").write_text("legacy", encoding="utf-8")
    monkeypatch.setattr(sessions.Path, "home", lambda: home)
    monkeypatch.setattr(sessions, "_db_path", None)
    monkeypatch.setattr(
        sessions.shutil,
        "copy2",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("copy failed")),
    )

    assert sessions.get_db_path() == home / ".invincat" / "sessions.db"


def test_thread_caches_apply_fresh_values_and_copy_recent_rows(monkeypatch) -> None:
    sessions._message_count_cache.clear()
    sessions._initial_prompt_cache.clear()
    sessions._recent_threads_cache.clear()
    monkeypatch.setattr(sessions, "get_thread_limit", lambda: 2)

    threads = [
        sessions.ThreadInfo(
            thread_id="one",
            agent_name="agent",
            updated_at="older",
            latest_checkpoint_id="fresh",
        ),
        sessions.ThreadInfo(
            thread_id="two",
            agent_name="agent",
            updated_at="newer",
            latest_checkpoint_id="stale",
        ),
    ]
    sessions._cache_message_count("one", "fresh", 3)
    sessions._cache_message_count("two", "other", 9)
    sessions._cache_initial_prompt("one", "fresh", "hello")
    sessions._cache_initial_prompt("two", "other", "stale")

    assert sessions.apply_cached_thread_message_counts(threads) == 1
    assert sessions.apply_cached_thread_initial_prompts(threads) == 1
    assert threads[0]["message_count"] == 3
    assert threads[0]["initial_prompt"] == "hello"
    assert "message_count" not in threads[1]
    assert "initial_prompt" not in threads[1]

    sessions._cache_recent_threads("agent", 3, threads)
    cached = sessions.get_cached_threads("agent", 2)

    assert cached is not None
    assert [row["thread_id"] for row in cached] == ["one", "two"]
    cached[0]["thread_id"] = "mutated"
    assert sessions.get_cached_threads("agent", 2)[0]["thread_id"] == "one"  # type: ignore[index]
    assert sessions.get_cached_threads("missing", 2) is None


def test_thread_cache_limits_and_require_message_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions._message_count_cache.clear()
    sessions._initial_prompt_cache.clear()
    sessions._recent_threads_cache.clear()
    monkeypatch.setattr(sessions, "_MAX_MESSAGE_COUNT_CACHE", 1)
    monkeypatch.setattr(sessions, "_MAX_INITIAL_PROMPT_CACHE", 1)
    monkeypatch.setattr(sessions, "_MAX_RECENT_THREADS_CACHE_KEYS", 1)

    sessions._cache_message_count("old", "a", 1)
    sessions._cache_message_count("new", "b", 2)
    sessions._cache_initial_prompt("old", "a", "old")
    sessions._cache_initial_prompt("new", "b", "new")

    assert "old" not in sessions._message_count_cache
    assert "old" not in sessions._initial_prompt_cache

    rows = [
        sessions.ThreadInfo(
            thread_id="new",
            agent_name=None,
            updated_at="b",
            latest_checkpoint_id="b",
        )
    ]
    sessions._cache_recent_threads(None, 5, rows)
    assert sessions.get_cached_threads(None, 1, require_message_counts=True) == [
        sessions.ThreadInfo(
            thread_id="new",
            agent_name=None,
            updated_at="b",
            latest_checkpoint_id="b",
            message_count=2,
            initial_prompt="new",
        )
    ]

    sessions._recent_threads_cache.clear()
    sessions._cache_recent_threads(None, 1, rows)
    sessions._cache_recent_threads("agent", 1, rows)
    assert (None, 1) not in sessions._recent_threads_cache

    sessions._recent_threads_cache.clear()
    sessions._message_count_cache.clear()
    sessions._cache_recent_threads(None, 1, rows)
    assert sessions.get_cached_threads(None, 1, require_message_counts=True) is None
    assert sessions.get_cached_threads(None, 0) is None

    sessions._recent_threads_cache.clear()
    sessions._cache_recent_threads(None, 5, rows)
    assert sessions.get_cached_threads(None, 2, require_message_counts=True) is None


def test_summarize_checkpoint_extracts_message_count_and_initial_prompt() -> None:
    checkpoint = {
        "channel_values": {
            "messages": [
                SimpleNamespace(type="system", content="ignore"),
                SimpleNamespace(
                    type="human",
                    content=[{"text": "hello"}, {"image": "ignored"}, "world"],
                ),
                SimpleNamespace(type="ai", content="answer"),
            ]
        }
    }

    summary = sessions._summarize_checkpoint(checkpoint)

    assert summary.message_count == 3
    assert summary.initial_prompt == "hello  world"
    assert sessions._summarize_checkpoint({}).message_count == 0
    assert sessions._summarize_checkpoint({"channel_values": {"messages": "bad"}}) == (
        0,
        None,
    )
    assert sessions._initial_prompt_from_messages([]) is None
    assert sessions._coerce_prompt_text(None) is None
    assert sessions._coerce_prompt_text(["", {"text": ""}]) is None
    assert sessions._coerce_prompt_text(123) == "123"


def test_checkpoint_message_shape_guards() -> None:
    assert sessions._checkpoint_messages(None) == []
    assert sessions._checkpoint_messages({"channel_values": "bad"}) == []


def _seed_sessions_db(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE checkpoints (
            thread_id TEXT,
            checkpoint_id TEXT,
            metadata TEXT,
            type TEXT,
            checkpoint BLOB
        )
        """
    )
    conn.execute("CREATE TABLE writes (thread_id TEXT)")
    rows = [
        (
            "thread-a",
            "cp-1",
            {
                "agent_name": "agent",
                "updated_at": "2026-05-13T10:00:00+00:00",
                "git_branch": "main",
                "cwd": "/repo",
            },
        ),
        (
            "thread-a",
            "cp-2",
            {
                "agent_name": "agent",
                "updated_at": "2026-05-14T10:00:00+00:00",
                "git_branch": "main",
                "cwd": "/repo",
            },
        ),
        (
            "thread-b",
            "cp-1",
            {
                "agent_name": "other",
                "updated_at": "2026-05-12T10:00:00+00:00",
                "git_branch": "dev",
                "cwd": "/other",
            },
        ),
    ]
    for thread_id, checkpoint_id, metadata in rows:
        conn.execute(
            "INSERT INTO checkpoints VALUES (?, ?, ?, NULL, NULL)",
            (thread_id, checkpoint_id, json.dumps(metadata)),
        )
    conn.execute("INSERT INTO writes VALUES ('thread-a')")
    conn.commit()
    conn.close()


def test_session_database_queries_and_delete(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.db"
    _seed_sessions_db(db_path)
    monkeypatch.setattr(sessions, "get_db_path", lambda: db_path)
    sessions._message_count_cache.clear()
    sessions._initial_prompt_cache.clear()
    sessions._recent_threads_cache.clear()
    sessions._cache_message_count("thread-a", "cp-2", 2)
    sessions._cache_initial_prompt("thread-a", "cp-2", "prompt")
    sessions._cache_recent_threads(
        None,
        20,
        [
            sessions.ThreadInfo(
                thread_id="thread-a",
                agent_name="agent",
                updated_at="2026-05-14T10:00:00+00:00",
                latest_checkpoint_id="cp-2",
            )
        ],
    )

    async def run() -> None:
        threads = await sessions.list_threads(agent_name="agent", branch="main")
        assert [thread["thread_id"] for thread in threads] == ["thread-a"]
        assert threads[0]["latest_checkpoint_id"] == "cp-2"
        assert await sessions.get_most_recent() == "thread-a"
        assert await sessions.get_most_recent("agent") == "thread-a"
        assert await sessions.get_thread_agent("thread-a") == "agent"
        assert await sessions.thread_exists("thread-a")
        assert await sessions.find_similar_threads("thread", limit=5) == [
            "thread-a",
            "thread-b",
        ]
        assert await sessions.delete_thread("thread-a")
        assert not await sessions.thread_exists("thread-a")
        assert not await sessions.delete_thread("missing")

    asyncio.run(run())

    assert "thread-a" not in sessions._message_count_cache
    assert "thread-a" not in sessions._initial_prompt_cache
    assert sessions._recent_threads_cache[(None, 20)] == []


def test_thread_queries_handle_missing_checkpoints_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "empty.db"
    sqlite3.connect(db_path).close()
    monkeypatch.setattr(sessions, "get_db_path", lambda: db_path)

    async def run() -> None:
        assert await sessions.get_most_recent() is None
        assert await sessions.get_thread_agent("missing") is None
        assert not await sessions.thread_exists("missing")
        assert await sessions.find_similar_threads("missing") == []
        assert not await sessions.delete_thread("missing")

    asyncio.run(run())


def test_list_threads_handles_missing_table_and_invalid_sort(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "empty.db"
    sqlite3.connect(db_path).close()
    monkeypatch.setattr(sessions, "get_db_path", lambda: db_path)

    async def run() -> None:
        assert await sessions.list_threads() == []

        _seed_sessions_db(db_path)
        try:
            await sessions.list_threads(sort_by="bad")
        except ValueError as exc:
            assert "Invalid sort_by" in str(exc)
        else:
            raise AssertionError("expected ValueError")

    asyncio.run(run())


def test_list_threads_populates_counts_and_caches_unfiltered_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "sessions.db"
    _seed_sessions_db(db_path)
    monkeypatch.setattr(sessions, "get_db_path", lambda: db_path)
    sessions._recent_threads_cache.clear()
    calls: list[list[str]] = []

    async def fake_populate_counts(
        _conn: object, threads: list[sessions.ThreadInfo]
    ) -> None:
        calls.append([thread["thread_id"] for thread in threads])
        for thread in threads:
            thread["message_count"] = 7

    monkeypatch.setattr(sessions, "_populate_message_counts", fake_populate_counts)

    async def run() -> None:
        threads = await sessions.list_threads(limit=2, include_message_count=True)
        assert [thread["message_count"] for thread in threads] == [7, 7]

    asyncio.run(run())

    assert calls == [["thread-a", "thread-b"]]
    assert sessions.get_cached_threads(None, 2) is not None


def test_populate_checkpoint_fields_batch_and_serializer_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "sessions.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE checkpoints (
            thread_id TEXT,
            checkpoint_id TEXT,
            metadata TEXT,
            type TEXT,
            checkpoint BLOB
        )
        """
    )
    conn.executemany(
        "INSERT INTO checkpoints VALUES (?, ?, '{}', ?, ?)",
        [
            ("good", "cp-1", "json", b"good"),
            ("empty", "cp-1", None, None),
            ("bad", "cp-1", "json", b"bad"),
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(sessions, "get_db_path", lambda: db_path)
    sessions._message_count_cache.clear()
    sessions._initial_prompt_cache.clear()
    sessions._jsonplus_serializer = None

    class FakeSerde:
        def __init__(self) -> None:
            self.calls: list[tuple[str, bytes]] = []

        def loads_typed(self, payload: tuple[str, bytes]) -> dict[str, Any]:
            self.calls.append(payload)
            _type, blob = payload
            if blob == b"bad":
                raise ValueError("bad payload")
            return {
                "channel_values": {
                    "messages": [
                        SimpleNamespace(type="human", content="hello"),
                        SimpleNamespace(type="ai", content="answer"),
                    ]
                }
            }

    serde = FakeSerde()
    monkeypatch.setattr(sessions, "_create_jsonplus_serializer", lambda: serde)
    monkeypatch.setattr(sessions, "_SQLITE_MAX_VARIABLE_NUMBER", 2)
    threads = [
        sessions.ThreadInfo(
            thread_id="good",
            agent_name=None,
            updated_at="u1",
            latest_checkpoint_id="cp-1",
        ),
        sessions.ThreadInfo(
            thread_id="empty",
            agent_name=None,
            updated_at="u2",
            latest_checkpoint_id="cp-1",
        ),
        sessions.ThreadInfo(
            thread_id="bad",
            agent_name=None,
            updated_at="u3",
            latest_checkpoint_id="cp-1",
        ),
        sessions.ThreadInfo(
            thread_id="missing",
            agent_name=None,
            updated_at="u4",
            latest_checkpoint_id="cp-1",
        ),
    ]

    async def run() -> None:
        assert await sessions._get_jsonplus_serializer() is serde
        assert await sessions._get_jsonplus_serializer() is serde
        await sessions.populate_thread_checkpoint_details(threads)
        import aiosqlite

        async with aiosqlite.connect(str(db_path)) as aconn:
            assert (
                await sessions._count_messages_from_checkpoint(aconn, "good", serde)
                == 2
            )
            assert (
                await sessions._extract_initial_prompt(aconn, "good", serde) == "hello"
            )

    asyncio.run(run())

    assert threads[0]["message_count"] == 2
    assert threads[0]["initial_prompt"] == "hello"
    assert threads[1]["message_count"] == 0
    assert threads[2]["message_count"] == 0
    assert threads[3]["message_count"] == 0
    assert sessions._message_count_cache["good"] == ("cp-1", 2)
    assert sessions._initial_prompt_cache["good"] == ("cp-1", "hello")


def test_single_checkpoint_summary_empty_and_deserialize_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db_path = tmp_path / "sessions.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE checkpoints (
            thread_id TEXT,
            checkpoint_id TEXT,
            metadata TEXT,
            type TEXT,
            checkpoint BLOB
        )
        """
    )
    conn.executemany(
        "INSERT INTO checkpoints VALUES (?, ?, '{}', ?, ?)",
        [
            ("empty", "cp-1", None, None),
            ("bad", "cp-1", "json", b"bad"),
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(sessions, "get_db_path", lambda: db_path)

    class BadSerde:
        def loads_typed(self, _payload: tuple[str, bytes]) -> dict[str, Any]:
            raise KeyError("bad")

    async def run() -> None:
        import aiosqlite

        serde: Any = BadSerde()
        async with aiosqlite.connect(str(db_path)) as aconn:
            assert (
                await sessions._load_latest_checkpoint_summaries_batch(aconn, [], serde)
                == {}
            )
            assert await sessions._load_latest_checkpoint_summary(
                aconn, "missing", serde
            ) == sessions._CheckpointSummary(0, None)
            assert await sessions._load_latest_checkpoint_summary(
                aconn, "empty", serde
            ) == sessions._CheckpointSummary(0, None)
            assert await sessions._load_latest_checkpoint_summary(
                aconn, "bad", serde
            ) == sessions._CheckpointSummary(0, None)

    asyncio.run(run())


def test_populate_checkpoint_details_early_returns_and_cache_hits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert asyncio.run(sessions.populate_thread_message_counts([])) == []
    assert asyncio.run(sessions.populate_thread_initial_prompts([])) is None
    assert asyncio.run(sessions.populate_thread_checkpoint_details([])) == []
    rows = [
        sessions.ThreadInfo(
            thread_id="cached",
            agent_name=None,
            updated_at="fresh",
            latest_checkpoint_id="fresh",
        )
    ]
    sessions._message_count_cache.clear()
    sessions._initial_prompt_cache.clear()
    sessions._cache_message_count("cached", "fresh", 5)
    sessions._cache_initial_prompt("cached", "fresh", "prompt")

    async def fail_populate(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("should not fetch")

    monkeypatch.setattr(
        sessions, "_load_latest_checkpoint_summaries_batch", fail_populate
    )

    async def fake_serializer() -> object:
        return object()

    monkeypatch.setattr(sessions, "_get_jsonplus_serializer", fake_serializer)

    async def run() -> None:
        await sessions._populate_checkpoint_fields(
            object(),
            rows,
            include_message_count=True,
            include_initial_prompt=True,
        )

    asyncio.run(run())

    assert rows[0]["message_count"] == 5
    assert rows[0]["initial_prompt"] == "prompt"


def test_populate_checkpoint_fields_applies_batch_results_and_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        sessions.ThreadInfo(
            thread_id="hit",
            agent_name=None,
            updated_at="fresh",
            latest_checkpoint_id="cp",
        ),
        sessions.ThreadInfo(
            thread_id="missing",
            agent_name=None,
            updated_at="fresh",
            latest_checkpoint_id="cp",
        ),
    ]
    sessions._message_count_cache.clear()
    sessions._initial_prompt_cache.clear()

    async def fake_serializer() -> object:
        return object()

    async def fake_batch(
        _conn: object, thread_ids: list[str], _serde: object
    ) -> dict[str, sessions._CheckpointSummary]:
        assert thread_ids == ["hit", "missing"]
        return {"hit": sessions._CheckpointSummary(4, "prompt")}

    monkeypatch.setattr(sessions, "_get_jsonplus_serializer", fake_serializer)
    monkeypatch.setattr(sessions, "_load_latest_checkpoint_summaries_batch", fake_batch)

    asyncio.run(
        sessions._populate_checkpoint_fields(
            object(),
            rows,
            include_message_count=True,
            include_initial_prompt=True,
        )
    )

    assert rows[0]["message_count"] == 4
    assert rows[0]["initial_prompt"] == "prompt"
    assert rows[1]["message_count"] == 0
    assert rows[1]["initial_prompt"] is None
    assert sessions._message_count_cache["hit"] == ("cp", 4)
    assert sessions._initial_prompt_cache["missing"] == ("cp", None)


def test_populate_thread_helpers_delegate_for_nonempty_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [
        sessions.ThreadInfo(
            thread_id="thread-1",
            agent_name=None,
            updated_at="fresh",
            latest_checkpoint_id="fresh",
        )
    ]
    calls: list[tuple[bool, bool]] = []
    fake_conn = object()

    class FakeConnectionContext:
        async def __aenter__(self) -> object:
            return fake_conn

        async def __aexit__(self, *_args: object) -> None:
            return None

    async def fake_populate(
        conn: object,
        threads: list[sessions.ThreadInfo],
        *,
        include_message_count: bool,
        include_initial_prompt: bool,
    ) -> None:
        assert conn is fake_conn
        assert threads is rows
        calls.append((include_message_count, include_initial_prompt))

    monkeypatch.setattr(sessions, "_connect", FakeConnectionContext)
    monkeypatch.setattr(sessions, "_populate_checkpoint_fields", fake_populate)

    asyncio.run(sessions.populate_thread_message_counts(rows))
    asyncio.run(sessions.populate_thread_initial_prompts(rows))

    assert calls == [(True, False), (False, True)]


def test_prewarm_thread_message_counts_success_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions._recent_threads_cache.clear()
    events: list[Any] = []

    class Config:
        columns = {"messages": True, "initial_prompt": True}

    async def fake_list_threads(**kwargs: Any) -> list[sessions.ThreadInfo]:
        events.append(("list", kwargs))
        return [
            sessions.ThreadInfo(
                thread_id="thread-1",
                agent_name=None,
                updated_at="u",
                latest_checkpoint_id="cp",
            )
        ]

    async def fake_populate(
        threads: list[sessions.ThreadInfo], **kwargs: Any
    ) -> list[sessions.ThreadInfo]:
        events.append(("populate", kwargs, [row["thread_id"] for row in threads]))
        threads[0]["message_count"] = 1
        return threads

    import invincat_cli.model_config as model_config

    monkeypatch.setattr(model_config, "load_thread_config", lambda: Config())
    monkeypatch.setattr(sessions, "list_threads", fake_list_threads)
    monkeypatch.setattr(sessions, "populate_thread_checkpoint_details", fake_populate)

    asyncio.run(sessions.prewarm_thread_message_counts(limit=2))
    asyncio.run(sessions.prewarm_thread_message_counts(limit=0))

    assert events == [
        ("list", {"limit": 2, "include_message_count": False}),
        (
            "populate",
            {"include_message_count": True, "include_initial_prompt": True},
            ["thread-1"],
        ),
    ]
    assert sessions.get_cached_threads(None, 2)[0]["thread_id"] == "thread-1"  # type: ignore[index]

    async def raise_sqlite(**_kwargs: Any) -> list[sessions.ThreadInfo]:
        raise sqlite3.Error("db")

    monkeypatch.setattr(sessions, "list_threads", raise_sqlite)
    asyncio.run(sessions.prewarm_thread_message_counts(limit=2))

    async def raise_unexpected(**_kwargs: Any) -> list[sessions.ThreadInfo]:
        raise RuntimeError("boom")

    monkeypatch.setattr(sessions, "list_threads", raise_unexpected)
    asyncio.run(sessions.prewarm_thread_message_counts(limit=2))


def test_get_thread_limit_reads_and_clamps_environment(monkeypatch) -> None:
    monkeypatch.delenv("DA_CLI_RECENT_THREADS", raising=False)
    assert sessions.get_thread_limit() == sessions._DEFAULT_THREAD_LIMIT

    monkeypatch.setenv("DA_CLI_RECENT_THREADS", "0")
    assert sessions.get_thread_limit() == 1

    monkeypatch.setenv("DA_CLI_RECENT_THREADS", "7")
    assert sessions.get_thread_limit() == 7

    monkeypatch.setenv("DA_CLI_RECENT_THREADS", "bad")
    assert sessions.get_thread_limit() == sessions._DEFAULT_THREAD_LIMIT


def test_list_threads_command_json_and_text_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.config as config_module
    import invincat_cli.io.output as output_module
    import invincat_cli.model_config as model_config

    writes: list[tuple[str, Any]] = []
    console = _FakeConsole()
    monkeypatch.setattr(
        output_module, "write_json", lambda name, data: writes.append((name, data))
    )
    monkeypatch.setattr(config_module, "console", console)
    monkeypatch.setattr(model_config, "load_thread_sort_order", lambda: "created_at")
    monkeypatch.setattr(model_config, "load_thread_relative_time", lambda: True)

    async def fake_list_threads(*args: Any, **kwargs: Any) -> list[sessions.ThreadInfo]:
        assert args == ("agent",)
        assert kwargs["sort_by"] == "created"
        return [
            sessions.ThreadInfo(
                thread_id="thread-1",
                agent_name=None,
                updated_at="2026-05-14T10:00:00+00:00",
                created_at="2026-05-13T10:00:00+00:00",
                latest_checkpoint_id="cp",
                git_branch="main",
                cwd=str(Path.home() / "repo"),
                message_count=3,
                initial_prompt="x" * 80,
            )
        ]

    async def fake_populate(
        threads: list[sessions.ThreadInfo], **_kwargs: Any
    ) -> list[sessions.ThreadInfo]:
        return threads

    monkeypatch.setattr(sessions, "list_threads", fake_list_threads)
    monkeypatch.setattr(sessions, "populate_thread_checkpoint_details", fake_populate)

    asyncio.run(
        sessions.list_threads_command(
            agent_name="agent",
            limit=1,
            verbose=True,
            output_format="json",
        )
    )
    asyncio.run(
        sessions.list_threads_command(
            agent_name="agent",
            branch="main",
            limit=1,
            verbose=True,
            output_format="text",
        )
    )

    assert writes[0][0] == "threads list"
    assert writes[0][1][0]["thread_id"] == "thread-1"
    assert any(
        "Recent Threads" in getattr(args[0], "title", "")
        for args, _kwargs in console.messages
        if args
    )
    assert any(
        "branch" in getattr(args[0], "title", "")
        for args, _kwargs in console.messages
        if args
    )
    assert "Showing last 1 threads" in console.text


def test_list_threads_command_empty_text_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.config as config_module

    console = _FakeConsole()
    monkeypatch.setattr(config_module, "console", console)

    async def no_threads(*_args: Any, **_kwargs: Any) -> list[sessions.ThreadInfo]:
        return []

    monkeypatch.setattr(sessions, "list_threads", no_threads)

    asyncio.run(
        sessions.list_threads_command(
            agent_name="agent",
            branch="main",
            limit=1,
            output_format="text",
        )
    )
    asyncio.run(sessions.list_threads_command(limit=1, output_format="text"))

    assert "agent" in console.text
    assert "branch" in console.text
    assert "No threads found." in console.text


def test_delete_thread_command_json_dry_run_and_text_outputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import invincat_cli.config as config_module
    import invincat_cli.io.output as output_module

    console = _FakeConsole()
    writes: list[tuple[str, Any]] = []
    deleted: list[str] = []
    monkeypatch.setattr(config_module, "console", console)
    monkeypatch.setattr(
        output_module, "write_json", lambda name, data: writes.append((name, data))
    )

    async def fake_exists(thread_id: str) -> bool:
        return thread_id == "yes"

    monkeypatch.setattr(sessions, "thread_exists", fake_exists)

    async def fake_delete(thread_id: str) -> bool:
        deleted.append(thread_id)
        return thread_id == "yes"

    monkeypatch.setattr(sessions, "delete_thread", fake_delete)

    asyncio.run(
        sessions.delete_thread_command("yes", dry_run=True, output_format="json")
    )
    asyncio.run(
        sessions.delete_thread_command("yes", dry_run=True, output_format="text")
    )
    asyncio.run(
        sessions.delete_thread_command("missing", dry_run=True, output_format="text")
    )
    asyncio.run(sessions.delete_thread_command("yes", output_format="json"))
    asyncio.run(sessions.delete_thread_command("yes", output_format="text"))
    asyncio.run(sessions.delete_thread_command("missing", output_format="text"))

    assert writes == [
        ("threads delete", {"thread_id": "yes", "exists": True, "dry_run": True}),
        ("threads delete", {"thread_id": "yes", "deleted": True}),
    ]
    assert deleted == ["yes", "yes", "missing"]
    assert "Would delete thread" in console.text
    assert "Nothing to delete" in console.text
    assert "deleted" in console.text
    assert "not found or already deleted" in console.text
