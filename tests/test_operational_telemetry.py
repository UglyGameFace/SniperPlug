from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json

import aiosqlite

from sniperplug.services.operational_telemetry import (
    ENDPOINT, error_class, publish_once, read_snapshot, safe_walmart_url, sign_payload,
)


class _DB:
    backend = "sqlite"
    def __init__(self, conn):
        self.conn = conn
    def require_conn(self):
        return self.conn


class _Bot:
    guilds = [object(), object()]
    def __init__(self, db):
        self.db = db
    def is_ready(self):
        return True


class _Response:
    status = 202
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False


class _Session:
    def __init__(self, secret):
        self.secret = secret
        self.captured = None
    def post(self, url, *, data, headers, allow_redirects):
        assert url == ENDPOINT
        assert not allow_redirects
        timestamp = headers["x-sniperplug-timestamp"]
        expected = "sha256=" + hmac.new(
            self.secret.encode(), timestamp.encode() + b"." + data,
            hashlib.sha256,
        ).hexdigest()
        assert hmac.compare_digest(headers["x-sniperplug-signature"], expected)
        self.captured = json.loads(data)
        return _Response()


async def _run():
    async with aiosqlite.connect(":memory:") as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript("""
            CREATE TABLE walmart_exact_detail_queue (
                item_id TEXT, title TEXT, status TEXT, route_hint TEXT,
                first_seen_at TEXT, last_seen_at TEXT, verified_at TEXT,
                last_attempt_at TEXT, last_error TEXT,
                exact_current_cents INTEGER, exact_reference_cents INTEGER,
                apparent_current_cents INTEGER, next_attempt_at TEXT, lease_until TEXT
            );
            CREATE TABLE walmart_worker_cycle_health (
                worker TEXT, total_cycles INTEGER, total_failures INTEGER,
                total_timeouts INTEGER, consecutive_failures INTEGER,
                last_batch_claimed INTEGER, last_api_price_missing INTEGER,
                last_cycle_at TEXT, last_success_at TEXT, last_failure_at TEXT
            );
            CREATE TABLE walmart_global_catalog_autoscan_state (
                state_key TEXT, completed_routes INTEGER, completed_passes INTEGER,
                last_started_at TEXT, last_completed_at TEXT, lease_until TEXT
            );
            CREATE TABLE walmart_global_exact_deal_events (
                first_seen_at TEXT, processed_at TEXT, last_error TEXT
            );
            CREATE TABLE guild_public_deal_posts (
                status TEXT, posted_at TEXT, first_seen_at TEXT
            );
        """)
        recent = datetime.now(timezone.utc).isoformat()
        await conn.execute(
            """INSERT INTO walmart_exact_detail_queue
              (item_id,title,status,route_hint,first_seen_at,last_seen_at,
               verified_at,last_attempt_at,last_error,exact_current_cents,
               exact_reference_cents,apparent_current_cents,next_attempt_at)
              VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("12345678", "Monitor ABC", "verified_markdown", "monitors",
             recent, recent, recent, recent, "", 1999, 17999, 1999, recent),
        )
        await conn.execute(
            """INSERT INTO walmart_worker_cycle_health VALUES
              ('exact',45,2,1,0,20,3,?,?,?)""", (recent, recent, None),
        )
        await conn.execute(
            """INSERT INTO walmart_global_catalog_autoscan_state VALUES
              ('walmart',190,2,?,?,NULL)""", (recent, recent),
        )
        await conn.execute(
            "INSERT INTO walmart_global_exact_deal_events VALUES (?,?,?)",
            (recent, recent, ""),
        )
        await conn.execute(
            "INSERT INTO guild_public_deal_posts VALUES ('posted',?,?)",
            (recent, recent),
        )
        await conn.commit()
        await conn.execute("PRAGMA query_only=ON")
        db = _DB(conn)
        snap = await read_snapshot(db)
        assert snap["queue"]["verified"] == 1
        assert snap["queue"]["due"] == 0
        assert snap["posts"]["posts_24h"] == 1
        assert snap["events"]["events_24h"] == 1
        assert snap["catalog"]["completed_routes"] == 190
        assert snap["workers"]["exact"]["total_timeouts"] == 1
        assert snap["deals"][0]["exact_current_cents"] == 1999
        assert snap["deals"][0]["product_url"] == "https://www.walmart.com/ip/12345678"
        assert "last_error" not in snap["deals"][0]
        assert not snap["missing_tables"]

        secret = "s" * 40
        import os
        original = os.environ.get("SNIPERPLUG_OPS_INGEST_SECRET")
        os.environ["SNIPERPLUG_OPS_INGEST_SECRET"] = secret
        try:
            outgoing = _Session(secret)
            success = await publish_once(_Bot(db), session=outgoing)
            assert success
            assert outgoing.captured["bot"]["ready"]
            assert outgoing.captured["bot"]["backend"] == "sqlite"
            assert outgoing.captured["metrics"]["posts"]["posts_24h"] == 1
            assert "guild_id" not in json.dumps(outgoing.captured)
        finally:
            if original is None:
                os.environ.pop("SNIPERPLUG_OPS_INGEST_SECRET", None)
            else:
                os.environ["SNIPERPLUG_OPS_INGEST_SECRET"] = original

    async with aiosqlite.connect(":memory:") as empty:
        empty.row_factory = aiosqlite.Row
        missing = await read_snapshot(_DB(empty))
        assert missing["queue"] is None and missing["posts"] is None
        assert len(missing["missing_tables"]) == 5


def test_operational_telemetry_is_read_only_and_signed():
    asyncio.run(_run())


def test_operational_telemetry_redaction():
    assert sign_payload("key", 123, b"{}") == hmac.new(
        b"key", b"123.{}", hashlib.sha256).hexdigest()
    assert safe_walmart_url("not-an-id") is None
    assert error_class("HTTP 429 token=very-secret") == "rate_limited"
    assert error_class("database locked password=hunter2") == "database_busy"
