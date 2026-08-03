from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import aiosqlite
import pytest

from sniperplug.ebay_watcher.models import EbayAPIBudgetExceeded
from sniperplug.ebay_watcher.storage import ebay_api_usage, reserve_api_call


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_search_and_get_items_share_one_daily_browse_budget() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        now = datetime(2026, 8, 3, 2, 0, tzinfo=timezone.utc)

        assert await reserve_api_call(
            db,
            bucket="browse_standard",
            daily_limit=2,
            now=now,
        ) == 1
        assert await reserve_api_call(
            db,
            bucket="browse_get_items",
            daily_limit=2,
            now=now,
        ) == 2

        with pytest.raises(EbayAPIBudgetExceeded) as error:
            await reserve_api_call(
                db,
                bucket="browse_standard",
                daily_limit=2,
                now=now,
            )
        assert error.value.bucket == "browse_total"

        usage = await ebay_api_usage(db, now=now)
        assert usage == {
            "browse_total": 2,
            "browse_standard": 1,
            "browse_get_items": 1,
        }
        await conn.close()

    asyncio.run(run())
