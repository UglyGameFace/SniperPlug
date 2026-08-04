from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sniperplug.services.walmart_exact_queue_maintenance import (
    CLEANUP_BATCH_SIZE,
    maybe_prune_walmart_exact_queue_bounded,
)


class RowsCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    async def fetchall(self):
        return list(self.rows)


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
            [{"item_id": "200"}, {"item_id": "300"}],
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
    assert len(conn.calls) == 3
    delete_sql, delete_params = conn.calls[-1]
    assert delete_sql.lstrip().upper().startswith("DELETE")
    assert "WHERE item_id IN (?, ?, ?)" in " ".join(delete_sql.split())
    assert delete_params == ("100", "200", "300")
    assert conn.commit_count == 1


def test_overlapping_stale_and_overflow_rows_still_fill_unique_batch() -> None:
    stale_ids = [str(index) for index in range(100)]
    overflow_ids = [
        *stale_ids,
        *(str(index) for index in range(100, CLEANUP_BATCH_SIZE + 100)),
    ]
    conn = CleanupConnection(
        [
            [{"item_id": item_id} for item_id in stale_ids],
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
    assert conn.calls[1][1][0] == CLEANUP_BATCH_SIZE
    delete_sql, delete_params = conn.calls[-1]
    assert delete_sql.lstrip().upper().startswith("DELETE")
    assert len(delete_params) == CLEANUP_BATCH_SIZE
    assert len(set(delete_params)) == CLEANUP_BATCH_SIZE
    assert delete_params[:3] == ("0", "1", "2")
    assert delete_params[-1] == "499"
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
