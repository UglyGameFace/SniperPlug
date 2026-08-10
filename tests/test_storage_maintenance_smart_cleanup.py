from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.services import storage_maintenance as maintenance


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


async def _noop_ensure(_db) -> None:
    return None


async def _noop_prune(_db) -> int:
    return 0


def test_smart_cleanup_drops_terminal_hot_rows_but_keeps_price_history(monkeypatch) -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        now = datetime.now(timezone.utc)
        old = (now - timedelta(days=10)).isoformat()
        recent = (now - timedelta(hours=1)).isoformat()

        await conn.executescript(
            """
            CREATE TABLE guild_deal_feedback_targets (expires_at TEXT);
            CREATE TABLE guild_deal_feedback_events (created_at TEXT);
            CREATE TABLE guild_active_deal_cache (
                guild_id INTEGER,
                active_key TEXT,
                status TEXT,
                last_seen_at TEXT
            );
            CREATE TABLE guild_auto_scan_report_history (ran_at TEXT);
            CREATE TABLE guild_public_deal_posts (status TEXT, first_seen_at TEXT);
            CREATE TABLE provider_response_cache (expires_at TEXT);
            CREATE TABLE scan_result_cache (expires_at TEXT);
            CREATE TABLE alert_dedupe (expires_at TEXT);
            CREATE TABLE store_cache (expires_at TEXT);
            CREATE TABLE scan_runs (started_at TEXT);
            CREATE TABLE price_observations (
                observation_id TEXT PRIMARY KEY,
                retailer TEXT,
                product_key TEXT,
                current_price REAL,
                observed_at TEXT
            );
            """
        )
        await conn.execute(
            "INSERT INTO guild_active_deal_cache VALUES (1, 'old-active', 'active', ?)",
            (old,),
        )
        await conn.execute(
            "INSERT INTO guild_active_deal_cache VALUES (1, 'old-stale', 'stale', ?)",
            (old,),
        )
        await conn.execute(
            "INSERT INTO guild_active_deal_cache VALUES (1, 'recent', 'active', ?)",
            (recent,),
        )
        await conn.execute(
            "INSERT INTO price_observations VALUES ('history-1', 'walmart', 'item-1', 19.99, ?)",
            (old,),
        )
        for table in (
            "provider_response_cache",
            "scan_result_cache",
            "alert_dedupe",
            "store_cache",
        ):
            await conn.execute(f"INSERT INTO {table} VALUES (?)", (old,))
        await conn.execute("INSERT INTO scan_runs VALUES (?)", (old,))
        await conn.commit()

        monkeypatch.setattr(maintenance, "ensure_deal_feedback_tables", _noop_ensure)
        monkeypatch.setattr(maintenance, "ensure_public_post_tables", _noop_ensure)
        monkeypatch.setattr(maintenance, "ensure_autoscan_history_table", _noop_ensure)
        monkeypatch.setattr(maintenance, "prune_active_deal_history", _noop_prune)
        monkeypatch.setattr(maintenance, "prune_walmart_recheck_audit", _noop_prune)

        result = await maintenance.run_storage_maintenance(
            db,
            cleanup_batch_size=50,
        )

        cursor = await conn.execute(
            "SELECT active_key, status FROM guild_active_deal_cache ORDER BY active_key"
        )
        assert await cursor.fetchall() == [("recent", "active")]

        cursor = await conn.execute("SELECT COUNT(*) FROM price_observations")
        assert (await cursor.fetchone())[0] == 1
        assert result.old_inactive_active_deals == 2
        assert result.expired_provider_cache == 1
        assert result.expired_scan_result_cache == 1
        assert result.expired_alert_dedupe == 1
        assert result.expired_store_cache == 1
        assert result.old_scan_runs == 1
        await conn.close()

    asyncio.run(run())


def test_bounded_cleanup_never_changes_more_than_requested_rows() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        await conn.execute("CREATE TABLE scratch (state TEXT)")
        await conn.executemany(
            "INSERT INTO scratch (state) VALUES ('old')",
            [() for _ in range(12)],
        )
        await conn.commit()

        updated = await maintenance.update_bounded_rows(
            conn,
            table="scratch",
            set_sql="state = 'expired'",
            where_sql="state = ?",
            params=("old",),
            limit=5,
        )
        deleted = await maintenance.delete_bounded_rows(
            conn,
            table="scratch",
            where_sql="state = ?",
            params=("expired",),
            limit=3,
        )

        assert updated == 5
        assert deleted == 3
        cursor = await conn.execute(
            "SELECT state, COUNT(*) FROM scratch GROUP BY state ORDER BY state"
        )
        assert await cursor.fetchall() == [("expired", 2), ("old", 7)]
        await conn.close()

    asyncio.run(run())
