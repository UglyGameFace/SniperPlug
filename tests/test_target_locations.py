from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace

import aiosqlite

from sniperplug.services.target_locations import (
    CATALOG_TABLE,
    LOCATION_TABLE,
    ensure_target_location_tables,
    get_guild_target_location,
    list_unique_active_target_locations,
    save_guild_target_location,
    save_user_target_location,
    stage_due_target_location_batches,
    target_card_matches_guild_location,
    target_card_matches_user_location,
    upsert_target_catalog_seeds,
)
from sniperplug.target_watcher.parser import TargetProductSeed
from sniperplug.target_watcher.storage import PRODUCT_TABLE


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


LOCATION = {
    "zip_code": "06604",
    "store_id": "1956",
    "store_name": "Target Trumbull",
    "address_line": "120 Hawley Ln",
    "city": "Trumbull",
    "state": "CT",
    "postal_code": "06611",
    "latitude": "41.2300",
    "longitude": "-73.1500",
}


async def _database() -> tuple[FakeDatabase, aiosqlite.Connection]:
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    return FakeDatabase(conn), conn


def test_target_locations_group_servers_and_users_by_unique_store() -> None:
    async def run() -> None:
        db, conn = await _database()
        await save_guild_target_location(db, guild_id=1, **LOCATION)
        await save_guild_target_location(db, guild_id=2, **LOCATION)
        await save_user_target_location(db, user_id=3, **LOCATION)

        unique = await list_unique_active_target_locations(db)
        assert len(unique) == 1
        assert unique[0].store_id == "1956"
        assert unique[0].zip_code == "06604"

        saved = await get_guild_target_location(db, 2)
        assert saved is not None
        assert saved.store_name == "Target Trumbull"
        await conn.close()

    asyncio.run(run())


def test_target_fanout_requires_exact_saved_store_and_zip() -> None:
    async def run() -> None:
        db, conn = await _database()
        await save_guild_target_location(db, guild_id=1, **LOCATION)
        await save_user_target_location(db, user_id=2, **LOCATION)
        matching = SimpleNamespace(
            variant_attributes={
                "targetLocationScope": "local",
                "targetStoreId": "1956",
                "targetZip": "06604",
            }
        )
        wrong_store = SimpleNamespace(
            variant_attributes={
                "targetLocationScope": "local",
                "targetStoreId": "9999",
                "targetZip": "06604",
            }
        )
        wrong_zip = SimpleNamespace(
            variant_attributes={
                "targetLocationScope": "local",
                "targetStoreId": "1956",
                "targetZip": "99999",
            }
        )
        assert await target_card_matches_guild_location(
            db, guild_id=1, card=matching
        )
        assert await target_card_matches_user_location(
            db, user_id=2, card=matching
        )
        assert not await target_card_matches_guild_location(
            db, guild_id=1, card=wrong_store
        )
        assert not await target_card_matches_user_location(
            db, user_id=2, card=wrong_zip
        )
        assert not await target_card_matches_guild_location(
            db, guild_id=999, card=matching
        )
        await conn.close()

    asyncio.run(run())


def test_one_catalog_slice_is_staged_once_for_a_shared_location() -> None:
    async def run() -> None:
        db, conn = await _database()
        now = datetime(2026, 8, 3, 1, 45, tzinfo=timezone.utc)
        await save_guild_target_location(db, guild_id=1, **LOCATION)
        await save_guild_target_location(db, guild_id=2, **LOCATION)
        seeds = [
            TargetProductSeed(
                tcin=f"9123456{index}",
                product_url=f"https://www.target.com/p/example-{index}/-/A-9123456{index}",
            )
            for index in range(3)
        ]
        assert await upsert_target_catalog_seeds(db, seeds, now=now) == 3
        staged_locations, staged_products = await stage_due_target_location_batches(
            db,
            locations_per_cycle=5,
            products_per_location=2,
            scan_spacing_seconds=15,
            now=now,
        )
        assert staged_locations == 1
        assert staged_products == 2

        cursor = await conn.execute(f"SELECT COUNT(*) FROM {PRODUCT_TABLE}")
        assert (await cursor.fetchone())[0] == 2
        catalog = await conn.execute(f"SELECT COUNT(*) FROM {CATALOG_TABLE}")
        assert (await catalog.fetchone())[0] == 3
        await conn.close()

    asyncio.run(run())


def test_unsafe_target_enrollment_is_removed_without_saved_location() -> None:
    async def run() -> None:
        db, conn = await _database()
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
        await conn.execute(
            """
            INSERT INTO guild_public_alert_settings (
                guild_id, enabled, retailers_json, channel_id, created_at, updated_at
            ) VALUES (1, 1, ?, 'ch:100', 'now', 'now')
            """,
            (json.dumps(["walmart", "hp", "target"]),),
        )
        await conn.commit()

        await ensure_target_location_tables(db)
        cursor = await conn.execute(
            "SELECT retailers_json FROM guild_public_alert_settings WHERE guild_id = 1"
        )
        retailers = json.loads((await cursor.fetchone())[0])
        assert retailers == ["walmart", "hp"]
        marker = await conn.execute(
            "SELECT COUNT(*) FROM sniperplug_data_migrations "
            "WHERE migration_key = '20260802_remove_target_without_location_v1'"
        )
        assert (await marker.fetchone())[0] == 1
        await conn.close()

    asyncio.run(run())
