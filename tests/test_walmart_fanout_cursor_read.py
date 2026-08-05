from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import aiosqlite

from sniperplug.services import walmart_global_deal_fanout_bulk as bulk


class Database:
    def __init__(self, conn) -> None:
        self.conn = conn

    def require_conn(self):
        return self.conn


class RecordingConnection:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.sql: list[str] = []

    async def execute(self, sql, params=()):
        normalized = " ".join(str(sql).split())
        self.sql.append(normalized)
        return await self.inner.execute(sql, params)

    async def commit(self) -> None:
        await self.inner.commit()


async def create_database(*, recording: bool = False):
    inner = await aiosqlite.connect(":memory:")
    await inner.execute(
        f"""
        CREATE TABLE {bulk.QUEUE_TABLE} (
            item_id TEXT PRIMARY KEY,
            verified_at TEXT,
            snapshot_json TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    await inner.commit()
    conn = RecordingConnection(inner) if recording else inner
    db = Database(conn)
    await bulk.ensure_global_deal_event_tables_once(db)
    return inner, conn, db


def test_cursor_query_plan_uses_partial_index_without_temp_sort() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        f"""
        CREATE TABLE {bulk.QUEUE_TABLE} (
            item_id TEXT PRIMARY KEY,
            verified_at TEXT,
            snapshot_json TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    conn.execute(bulk.FANOUT_CURSOR_INDEX_SQL)
    plan = [
        str(row[-1])
        for row in conn.execute(
            "EXPLAIN QUERY PLAN " + bulk.FANOUT_CURSOR_KEYS_SQL,
            ("2026-08-05T00:00:00+00:00", "", 150),
        )
    ]

    assert any(bulk.FANOUT_CURSOR_INDEX in detail for detail in plan)
    assert not any("TEMP B-TREE" in detail for detail in plan)
    assert "snapshot_json" not in bulk.FANOUT_CURSOR_KEYS_SQL.split("FROM", 1)[0]
    conn.close()


def test_cursor_keys_are_bounded_and_stable_for_equal_timestamps() -> None:
    async def run() -> None:
        inner, _conn, db = await create_database()
        timestamp = "2026-08-05T12:00:00+00:00"
        await inner.executemany(
            f"""
            INSERT INTO {bulk.QUEUE_TABLE} (
                item_id, verified_at, snapshot_json, status
            ) VALUES (?, ?, ?, ?)
            """,
            [
                ("003", timestamp, "snapshot-3", "verified_markdown"),
                ("001", timestamp, "snapshot-1", "verified_markdown"),
                ("002", timestamp, "snapshot-2", "verified_markdown"),
                (
                    "000",
                    timestamp,
                    "not-public",
                    "verified_no_reference",
                ),
                (
                    "004",
                    "2026-08-05T12:01:00+00:00",
                    "snapshot-4",
                    "verified_markdown",
                ),
            ],
        )
        await inner.commit()

        keys = await bulk._select_verified_queue_cursor_keys(
            db.require_conn(),
            last_verified_at=timestamp,
            last_item_id="001",
            limit=2,
        )

        assert [(key.verified_at, key.item_id) for key in keys] == [
            (timestamp, "002"),
            (timestamp, "003"),
        ]
        await inner.close()

    asyncio.run(run())


def test_empty_ingest_poll_never_loads_snapshot_payloads() -> None:
    async def run() -> None:
        inner, conn, db = await create_database(recording=True)
        conn.sql.clear()

        result = await bulk._ingest_verified_queue_events_bulk(db, limit=150)

        assert result == bulk._IngestResult()
        assert any(
            sql.startswith("SELECT last_verified_at, last_item_id")
            for sql in conn.sql
        )
        assert any(
            sql.startswith("SELECT item_id, verified_at")
            for sql in conn.sql
        )
        assert not any(
            sql.startswith(
                "WITH picked(item_id, verified_at, ordinal) AS"
            )
            for sql in conn.sql
        )
        assert not any(
            sql.startswith(
                "SELECT queue.item_id, queue.verified_at, queue.snapshot_json"
            )
            for sql in conn.sql
        )
        await inner.close()

    asyncio.run(run())


def test_snapshot_load_rechecks_selected_version_and_preserves_order() -> None:
    async def run() -> None:
        inner, _conn, db = await create_database()
        first_time = "2026-08-05T12:00:00+00:00"
        await inner.executemany(
            f"""
            INSERT INTO {bulk.QUEUE_TABLE} (
                item_id, verified_at, snapshot_json, status
            ) VALUES (?, ?, ?, 'verified_markdown')
            """,
            [
                ("001", first_time, "snapshot-1"),
                ("002", first_time, "snapshot-2"),
                ("003", first_time, "snapshot-3"),
            ],
        )
        await inner.commit()
        keys = await bulk._select_verified_queue_cursor_keys(
            db.require_conn(),
            last_verified_at="",
            last_item_id="",
            limit=10,
        )

        await inner.execute(
            f"""
            UPDATE {bulk.QUEUE_TABLE}
            SET verified_at = ?
            WHERE item_id = '002'
            """,
            ("2026-08-05T12:05:00+00:00",),
        )
        await inner.commit()

        rows = await bulk._load_cursor_snapshots(db.require_conn(), keys)

        assert [(key.item_id, snapshot) for key, snapshot in rows] == [
            ("001", "snapshot-1"),
            ("003", "snapshot-3"),
        ]
        await inner.close()

    asyncio.run(run())


def test_ingest_advances_to_last_selected_key_when_snapshot_changes(
    monkeypatch,
) -> None:
    async def run() -> None:
        inner, _conn, db = await create_database()
        timestamp = "2026-08-05T12:00:00+00:00"
        await inner.executemany(
            f"""
            INSERT INTO {bulk.QUEUE_TABLE} (
                item_id, verified_at, snapshot_json, status
            ) VALUES (?, ?, ?, 'verified_markdown')
            """,
            [
                ("001", timestamp, "snapshot-1"),
                ("002", timestamp, "snapshot-2"),
            ],
        )
        await inner.commit()

        async def load_only_first(_conn, keys):
            return [(keys[0], "snapshot-1")]

        monkeypatch.setattr(bulk, "_load_cursor_snapshots", load_only_first)
        monkeypatch.setattr(
            bulk,
            "_candidate_from_snapshot",
            lambda snapshot: snapshot,
        )
        monkeypatch.setattr(
            bulk.legacy,
            "_exact_card_for_candidate",
            lambda candidate: SimpleNamespace(
                retailer="walmart",
                label=str(candidate),
            ),
        )
        monkeypatch.setattr(
            bulk,
            "card_deal_key",
            lambda card, retailer: f"{retailer}:{card.label}",
        )

        result = await bulk._ingest_verified_queue_events_bulk(db, limit=10)
        state = await (
            await inner.execute(
                f"""
                SELECT last_verified_at, last_item_id
                FROM {bulk.legacy.STATE_TABLE}
                WHERE state_key = ?
                """,
                (bulk.legacy.STATE_KEY,),
            )
        ).fetchone()
        event_count = await (
            await inner.execute(
                f"SELECT COUNT(*) FROM {bulk.legacy.EVENT_TABLE}"
            )
        ).fetchone()

        assert result.loaded == 1
        assert result.exact_cards == 1
        assert result.new_events == 1
        assert state == (timestamp, "002")
        assert event_count == (1,)
        await inner.close()

    asyncio.run(run())


def test_production_source_removes_joined_snapshot_cursor_scan() -> None:
    source = Path(
        "sniperplug/services/walmart_global_deal_fanout_bulk.py"
    ).read_text(encoding="utf-8")

    assert (
        "SELECT queue.item_id, queue.verified_at, queue.snapshot_json"
        not in source
    )
    assert "AND (verified_at, item_id) > (?, ?)" in source
    assert "INDEXED BY {FANOUT_CURSOR_INDEX}" in source
