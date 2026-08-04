from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from sniperplug.services.walmart_exact_queue_drain import (
    claim_due_rows_batched,
    tiered_recheck_delay,
)
from sniperplug.services.walmart_exact_queue_health import WalmartExactQueueHealth
from sniperplug.services.walmart_fresh_work_policy import (
    catalog_backpressure_reason,
    should_use_drain_mode,
)


@pytest.mark.asyncio
async def test_atomic_claim_leases_one_ordered_batch() -> None:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.execute(
        """
        CREATE TABLE walmart_exact_detail_queue (
            item_id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            product_url TEXT NOT NULL DEFAULT '',
            image_url TEXT NOT NULL DEFAULT '',
            apparent_current_cents INTEGER,
            apparent_reference_cents INTEGER,
            route_hint TEXT NOT NULL DEFAULT '',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            next_attempt_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            status TEXT NOT NULL,
            lease_token TEXT NOT NULL DEFAULT '',
            lease_until TEXT,
            priority_score REAL NOT NULL DEFAULT 0
        )
        """
    )
    now = datetime.now(timezone.utc)
    due = (now - timedelta(minutes=1)).isoformat()
    seen = now.isoformat()
    await conn.executemany(
        """
        INSERT INTO walmart_exact_detail_queue (
            item_id, title, next_attempt_at, last_seen_at, status, priority_score
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            ("1001", "first", due, seen, "pending", 10),
            ("1002", "second", due, seen, "retry", 9),
            ("1003", "third", due, seen, "verified_markdown", 8),
        ),
    )
    await conn.commit()

    claims = await claim_due_rows_batched(conn, now=now, limit=2)

    assert [claim.item_id for claim in claims] == ["1001", "1002"]
    assert claims[0].lease_token
    assert claims[0].lease_token == claims[1].lease_token
    cursor = await conn.execute(
        "SELECT item_id, status, lease_token FROM walmart_exact_detail_queue ORDER BY item_id"
    )
    rows = await cursor.fetchall()
    assert rows[0]["status"] == "verifying"
    assert rows[1]["status"] == "verifying"
    assert rows[2]["status"] == "verified_markdown"
    await conn.close()


def test_catalog_pauses_before_fresh_work_snowballs() -> None:
    health = WalmartExactQueueHealth(
        due_now=650,
        initial_due_now=13,
        recheck_due_now=637,
    )
    reason = catalog_backpressure_reason(health)
    assert reason is not None
    assert "fresh exact-detail backpressure" in reason
    assert should_use_drain_mode(health) is False


def test_total_backlog_pauses_catalog_without_forcing_drain() -> None:
    health = WalmartExactQueueHealth(
        due_now=600,
        initial_due_now=0,
        recheck_due_now=600,
    )
    reason = catalog_backpressure_reason(health)
    assert reason is not None
    assert "emergency backpressure" in reason
    assert should_use_drain_mode(health) is False


def test_recheck_cadence_is_sustainable() -> None:
    assert tiered_recheck_delay("verified_markdown", 70) == timedelta(hours=4)
    assert tiered_recheck_delay("verified_markdown", 40) == timedelta(hours=12)
    assert tiered_recheck_delay("verified_markdown", 20) == timedelta(hours=24)
