from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import aiosqlite

from sniperplug.services.walmart_missed_deal_trace import (
    _elapsed,
    _safe_error_class,
    trace_walmart_item,
)


class PoisonDb:
    def require_conn(self):
        raise AssertionError("must reject item before database access")


def test_invalid_item_id_does_not_access_database():
    trace = asyncio.run(trace_walmart_item(PoisonDb(), item_id="X'; DROP TABLE x", guild_id=123))
    assert trace.outcome == "invalid_item_id"


def test_elapsed_measurement_rejects_unknown_or_backward_timestamps():
    now = datetime.now(timezone.utc)
    assert _elapsed(now.isoformat(), (now + timedelta(seconds=75)).isoformat()) == 75
    assert _elapsed(now.isoformat(), (now - timedelta(seconds=1)).isoformat()) is None
    assert _elapsed("unknown", now.isoformat()) is None


def test_error_is_categorized_without_echoing_raw_api_exception():
    secret_error = "HTTP 429 with Authorization Token=secret_string"
    assert _safe_error_class(secret_error) == "rate_limited"


def test_read_only_trace_reports_queue_state_and_bounded_absence():
    async def run():
        conn = await aiosqlite.connect(":memory:")
        db = SimpleNamespace(require_conn=lambda: conn)
        now = datetime.now(timezone.utc)
        before = (now - timedelta(minutes=5)).isoformat()
        verified = (now - timedelta(minutes=3)).isoformat()
        await conn.execute(
            """CREATE TABLE walmart_exact_detail_queue (
                item_id TEXT PRIMARY KEY, status TEXT, first_seen_at TEXT,
                last_seen_at TEXT, verified_at TEXT, next_attempt_at TEXT,
                last_error TEXT
            )"""
        )
        await conn.execute(
            """INSERT INTO walmart_exact_detail_queue
                (item_id, status, first_seen_at, last_seen_at, verified_at,
                 next_attempt_at, last_error) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("123456", "verified_under_threshold", before, now.isoformat(),
             verified, now.isoformat(), ""),
        )
        await conn.commit()
        try:
            trace = await trace_walmart_item(db, item_id="123456", guild_id=123)
            assert trace.outcome == "queue_only"
            assert trace.queue_status == "verified_under_threshold"
            assert trace.discovery_to_verification_seconds == 120
            assert trace.sampled_events == 0
            assert trace.post_status == ""
            assert "Not proof" in trace.detail
            unknown = await trace_walmart_item(db, item_id="987654", guild_id=123)
            assert unknown.outcome == "not_in_retained_sample"
        finally:
            await conn.close()
    asyncio.run(run())


def test_read_only_trace_finds_actual_event_and_guild_post_receipt():
    async def run():
        conn = await aiosqlite.connect(":memory:")
        db = SimpleNamespace(require_conn=lambda: conn)
        now = datetime.now(timezone.utc)
        first = (now - timedelta(minutes=8)).isoformat()
        verified = (now - timedelta(minutes=6)).isoformat()
        event_seen = (now - timedelta(minutes=5)).isoformat()
        posted = (now - timedelta(minutes=2)).isoformat()
        item = "123456789"
        await conn.execute(
            """CREATE TABLE walmart_exact_detail_queue (
                item_id TEXT PRIMARY KEY, status TEXT, first_seen_at TEXT,
                last_seen_at TEXT, verified_at TEXT, next_attempt_at TEXT,
                last_error TEXT
            )"""
        )
        await conn.execute(
            """INSERT INTO walmart_exact_detail_queue
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (item, "verified_markdown", first, now.isoformat(), verified,
             now.isoformat(), "ignored secret text"),
        )
        await conn.execute(
            """CREATE TABLE walmart_global_exact_deal_events (
                deal_key TEXT PRIMARY KEY, snapshot_json TEXT, first_seen_at TEXT,
                source_verified_at TEXT, processed_at TEXT, last_error TEXT
            )"""
        )
        snapshot = json.dumps({
            "product_id": item,
            "title": "Walmart exact product",
            "product_url": f"https://www.walmart.com/ip/{item}",
            "api_current_price": 20.0,
            "api_reference_price": 100.0,
        })
        await conn.execute(
            """INSERT INTO walmart_global_exact_deal_events
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("event-abc", snapshot, event_seen, verified, event_seen, ""),
        )
        await conn.execute(
            """CREATE TABLE guild_public_deal_posts (
                guild_id INTEGER, deal_key TEXT, retailer TEXT, status TEXT,
                posted_at TEXT
            )"""
        )
        await conn.execute(
            """INSERT INTO guild_public_deal_posts VALUES (?, ?, ?, ?, ?)""",
            (123, "event-abc", "walmart", "posted", posted),
        )
        await conn.commit()
        try:
            trace = await trace_walmart_item(db, item_id=item, guild_id=123)
            assert trace.outcome == "posted"
            assert trace.sampled_events == 1
            assert trace.post_status == "posted"
            assert trace.discovery_to_verification_seconds == 120
            assert trace.discovery_to_post_seconds == 360
            assert trace.verification_to_post_seconds == 240
            trace_other_guild = await trace_walmart_item(db, item_id=item, guild_id=999)
            assert trace_other_guild.outcome == "no_post_receipt"
        finally:
            await conn.close()
    asyncio.run(run())


def test_missing_schema_does_not_create_any_tables():
    async def run():
        conn = await aiosqlite.connect(":memory:")
        db = SimpleNamespace(require_conn=lambda: conn)
        try:
            trace = await trace_walmart_item(db, item_id="123456", guild_id=123)
            assert trace.outcome == "unavailable"
            cur = await conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'")
            assert (await cur.fetchone())[0] == 0
        finally:
            await conn.close()
    asyncio.run(run())
