from __future__ import annotations

import asyncio
import json

import aiosqlite

from sniperplug.services.public_alert_config import (
    HP_RETAILER_MIGRATION,
    ensure_public_alert_table,
    get_public_alert_config,
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_existing_enabled_walmart_destinations_receive_hp_once() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await conn.execute(
            """
            CREATE TABLE guild_public_alert_settings (
                guild_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                retailers_json TEXT NOT NULL DEFAULT '[]',
                channel_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        await conn.executemany(
            """
            INSERT INTO guild_public_alert_settings
                (guild_id, enabled, retailers_json, channel_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'now', 'now')
            """,
            (
                (1, 1, json.dumps(["walmart"]), "ch:100"),
                (2, 0, json.dumps(["walmart"]), "ch:200"),
                (3, 1, json.dumps(["amazon"]), "ch:300"),
                (4, 1, json.dumps(["walmart", "hp"]), "ch:400"),
            ),
        )
        await conn.commit()

        await ensure_public_alert_table(db)
        enabled = await get_public_alert_config(db, 1)
        disabled = await get_public_alert_config(db, 2)
        custom = await get_public_alert_config(db, 3)
        already = await get_public_alert_config(db, 4)
        assert enabled["retailers"] == ("walmart", "hp")
        assert disabled["retailers"] == ("walmart",)
        assert custom["retailers"] == ("amazon",)
        assert already["retailers"] == ("walmart", "hp")

        marker = await conn.execute(
            "SELECT COUNT(*) FROM sniperplug_data_migrations WHERE migration_key = ?",
            (HP_RETAILER_MIGRATION,),
        )
        assert (await marker.fetchone())[0] == 1
        await ensure_public_alert_table(db)
        repeat = await get_public_alert_config(db, 1)
        assert repeat["retailers"] == ("walmart", "hp")
        await conn.close()

    asyncio.run(run())
