from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.services import walmart_exact_queue_maintenance as cleaner
from sniperplug.services.walmart_exact_verification_queue import QUEUE_TABLE, ensure_walmart_exact_verification_queue

from sniperplug.services.walmart_exact_queue_maintenance import (
    CLEANUP_BATCH_SIZE,
    CLEANUP_OVERFLOW_PROBE_SECONDS,
    CLEANUP_FOLLOWUP_SECONDS,
    maybe_prune_walmart_exact_queue_bounded,
)


class RowsCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    async def fetchall(self):
        return list(self.rows)

    async def fetchone(self):
        return self.rows[0] if self.rows else None


class CleanupConnection:
    def __init__(self, row_batches):
        self.row_batches = list(row_batches)
        self.calls: list[tuple[str, tuple]] = []
        self.commit_count = 0

    async def execute(self, sql, params=()):
        self.calls.append((str(sql), tuple(params)))
        if str(sql).lstrip().upper().startswith("SELECT"):
            rows = self.row_batches.pop(0) if self.row_batches else []
            return RowsCursor(rows)
        return RowsCursor([])

    async def commit(self):
        self.commit_count += 1


def test_empty_cleanup_skips_delete_and_commit() -> None:
    conn = CleanupConnection([[], []])

    result = asyncio.run(
        maybe_prune_walmart_exact_queue_bounded(
            conn,
            now=datetime(2026, 8, 4, tzinfo=timezone.utc),
        )
    )

    assert result.deleted == 0
    assert result.skipped_noop_write is True
    assert len(conn.calls) == 2
    assert all(call[0].lstrip().upper().startswith("SELECT") for call in conn.calls)
    assert conn.commit_count == 0


def test_cleanup_deletes_only_preselected_item_ids() -> None:
    conn = CleanupConnection(
        [
            [{"item_id": "100"}, {"item_id": "200"}],
            [{"row_count": cleaner.QUEUE_MAX_ROWS + 100}],
            [{"item_id": "300"}],
        ]
    )

    result = asyncio.run(
        maybe_prune_walmart_exact_queue_bounded(
            conn,
            now=datetime(2026, 8, 4, tzinfo=timezone.utc),
        )
    )

    assert result.deleted == 3
    assert result.skipped_noop_write is False
    assert len(conn.calls) == 4
    delete_sql, delete_params = conn.calls[-1]
    assert delete_sql.lstrip().upper().startswith("DELETE")
    assert "WHERE item_id IN (?, ?, ?)" in " ".join(delete_sql.split())
    assert delete_params[:3] == ("100", "200", "300")
    assert len(delete_params) == 6
    assert conn.commit_count == 1


def test_overlapping_stale_and_overflow_rows_still_fill_unique_batch() -> None:
    stale_count = max(1, CLEANUP_BATCH_SIZE // 5)
    stale_ids = [str(index) for index in range(stale_count)]
    overflow_ids = [
        *stale_ids,
        *(
            str(index)
            for index in range(stale_count, CLEANUP_BATCH_SIZE + stale_count)
        ),
    ]
    conn = CleanupConnection(
        [
            [{"item_id": item_id} for item_id in stale_ids],
            [{"row_count": cleaner.QUEUE_MAX_ROWS + CLEANUP_BATCH_SIZE}],
            [{"item_id": item_id} for item_id in overflow_ids],
        ]
    )

    result = asyncio.run(
        maybe_prune_walmart_exact_queue_bounded(
            conn,
            now=datetime(2026, 8, 4, tzinfo=timezone.utc),
        )
    )

    assert result.deleted == CLEANUP_BATCH_SIZE
    assert conn.calls[1][0].strip() == f"SELECT COUNT(*) AS row_count FROM {QUEUE_TABLE}"
    assert conn.calls[2][1][-1] == CLEANUP_BATCH_SIZE - stale_count
    delete_sql, delete_params = conn.calls[-1]
    assert delete_sql.lstrip().upper().startswith("DELETE")
    assert len(delete_params) == CLEANUP_BATCH_SIZE
    assert len(set(delete_params)) == CLEANUP_BATCH_SIZE
    assert delete_params[:3] == ("0", "1", "2")
    assert delete_params[-1] == str(CLEANUP_BATCH_SIZE - 1)
    assert conn.commit_count == 1


def test_cleanup_is_throttled_after_confirmed_noop() -> None:
    conn = CleanupConnection([[], []])

    first = asyncio.run(maybe_prune_walmart_exact_queue_bounded(conn))
    second = asyncio.run(maybe_prune_walmart_exact_queue_bounded(conn))

    assert first.skipped_noop_write is True
    assert second.deleted == 0
    assert second.skipped_noop_write is False
    assert len(conn.calls) == 2
    assert conn.commit_count == 0

def test_cleanup_removes_expired_and_overflow_without_erasing_fresh_or_active_deals(monkeypatch) -> None:
    async def scenario() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        await ensure_walmart_exact_verification_queue(
            type("DB", (), {"require_conn": lambda self: conn})()
        )
        # Small sample with the exact same production cleanup code and SQL.
        monkeypatch.setattr(cleaner, "QUEUE_MAX_ROWS", 5)
        now = datetime.now(timezone.utc)
        ancient = (now - timedelta(days=20)).isoformat()
        old = (now - timedelta(days=2)).isoformat()
        fresh = (now - timedelta(minutes=2)).isoformat()
        recent_verified = (now - timedelta(minutes=5)).isoformat()
        active_lease = (now + timedelta(minutes=2)).isoformat()
        rows = [
            ("101", "verified_no_reference", ancient, None, None),
            ("102", "incomplete_identity", ancient, None, None),
            ("103", "not_buyable", old, None, None),
            ("104", "verified_under_threshold", old, None, None),
            ("105", "identity_mismatch", old, None, None),
            ("106", "verified_markdown", old, recent_verified, None),
            ("107", "verifying", ancient, None, active_lease),
            ("108", "pending", fresh, None, None),
            ("109", "verified_no_reference", fresh, None, None),
        ]
        try:
            for id_, status, seen, verified, lease in rows:
                await conn.execute(
                    f"""
                    INSERT INTO {QUEUE_TABLE} (
                        item_id, first_seen_at, last_seen_at,
                        next_attempt_at, status, verified_at, lease_until
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (id_, seen, seen, fresh, status, verified, lease),
                )
            await conn.commit()
            result = await cleaner.maybe_prune_walmart_exact_queue_bounded(
                conn, now=now,
            )
            assert result.deleted == 4
            assert result.expired_selected == 2
            assert result.overflow_selected == 2
            remaining_cursor = await conn.execute(
                f"SELECT item_id FROM {QUEUE_TABLE} ORDER BY item_id"
            )
            remaining = [row[0] for row in await remaining_cursor.fetchall()]
            assert remaining == ["105", "106", "107", "108", "109"]
            assert result.deleted <= CLEANUP_BATCH_SIZE
        finally:
            await conn.close()

    asyncio.run(scenario())


def test_cleanup_does_not_prune_when_queue_under_limit_and_not_expired(monkeypatch) -> None:
    async def scenario() -> None:
        conn = await aiosqlite.connect(":memory:")
        await ensure_walmart_exact_verification_queue(
            type("DB", (), {"require_conn": lambda self: conn})()
        )
        monkeypatch.setattr(cleaner, "QUEUE_MAX_ROWS", 5)
        now = datetime.now(timezone.utc)
        recent = (now - timedelta(minutes=10)).isoformat()
        try:
            for i in range(4):
                await conn.execute(
                    f"""
                    INSERT INTO {QUEUE_TABLE} (
                        item_id, first_seen_at, last_seen_at,
                        next_attempt_at, status
                    ) VALUES (?, ?, ?, ?, 'pending')
                    """,
                    (str(i), recent, recent, recent),
                )
            await conn.commit()
            result = await cleaner.maybe_prune_walmart_exact_queue_bounded(
                conn, now=now,
            )
            assert result.deleted == 0
            assert result.skipped_noop_write
            cursor = await conn.execute(f"SELECT COUNT(*) FROM {QUEUE_TABLE}")
            assert (await cursor.fetchone())[0] == 4
        finally:
            await conn.close()

    asyncio.run(scenario())


def test_bounded_cleanup_schedule_does_not_provoke_quota_spikes() -> None:
    assert CLEANUP_BATCH_SIZE <= 100
    assert CLEANUP_FOLLOWUP_SECONDS == 60
    assert CLEANUP_OVERFLOW_PROBE_SECONDS >= 15 * 60
