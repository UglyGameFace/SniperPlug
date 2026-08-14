from __future__ import annotations

import asyncio

import aiosqlite

from sniperplug.services import storage_maintenance as maintenance


def test_cleanup_indexes_install_when_optional_tables_appear_after_startup() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        await conn.executescript(
            """
            CREATE TABLE guild_deal_feedback_targets (created_at TEXT);
            CREATE TABLE guild_deal_feedback_events (created_at TEXT);
            CREATE TABLE guild_active_deal_cache (status TEXT, last_seen_at TEXT);
            CREATE TABLE guild_auto_scan_report_history (ran_at TEXT);
            CREATE TABLE guild_public_deal_posts (status TEXT, first_seen_at TEXT, posted_at TEXT);
            CREATE TABLE store_cache (expires_at TEXT);
            CREATE TABLE scan_runs (started_at TEXT);
            """
        )
        maintenance._CLEANUP_INDEX_CONNECTIONS.clear()

        # First startup-style pass: optional runtime tables do not exist yet.
        await maintenance.ensure_quota_safe_cleanup_indexes(conn)

        await conn.executescript(
            """
            CREATE TABLE guild_retailer_auto_scan_runs (ran_at TEXT);
            CREATE TABLE user_dm_deal_alert_receipts (delivered_at TEXT);
            """
        )

        # Later maintenance must notice the newly-created tables rather than
        # returning early just because base cleanup indexes were already ready.
        await maintenance.ensure_quota_safe_cleanup_indexes(conn)

        cursor = await conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'index'
              AND name IN (
                'idx_retailer_auto_scan_runs_ran_cleanup',
                'idx_dm_deal_alert_receipts_delivered_cleanup'
              )
            ORDER BY name
            """
        )
        names = [row[0] for row in await cursor.fetchall()]
        assert names == [
            "idx_dm_deal_alert_receipts_delivered_cleanup",
            "idx_retailer_auto_scan_runs_ran_cleanup",
        ]
        await conn.close()

    asyncio.run(run())
