from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import aiosqlite

from sniperplug.hp_watcher.storage import PRODUCT_TABLE, upsert_product_urls


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_large_hp_sitemap_upsert_is_batched_and_idempotent() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        now = datetime.now(timezone.utc)
        urls = [
            f"https://www.hp.com/us-en/shop/pdp/catalog-product-{index}"
            for index in range(205)
        ]

        assert await upsert_product_urls(db, urls, now=now) == 205
        assert await upsert_product_urls(db, urls, now=now) == 0
        cursor = await conn.execute(f"SELECT COUNT(*) FROM {PRODUCT_TABLE}")
        assert (await cursor.fetchone())[0] == 205
        await conn.close()

    asyncio.run(run())
