from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from sniperplug.target_watcher.leased_storage import (
    claim_due_sitemap_sources,
    claim_products_for_offer_poll,
    complete_product_work,
    complete_sitemap_source,
    record_exact_offer,
)
from sniperplug.target_watcher.parser import TargetOffer, TargetProductSeed
from sniperplug.target_watcher.storage import (
    ensure_target_watcher_tables,
    upsert_product_seeds,
    upsert_sitemap_sources,
)


TCIN = "91234567"
STORE = "1956"
ZIP = "06604"
PRODUCT_URL = f"https://www.target.com/p/example/-/A-{TCIN}"
SITEMAP_URL = "https://www.target.com/sitemap_pdp-1.xml.gz"


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_sitemap_lease_blocks_duplicate_worker_and_stale_completion() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_target_watcher_tables(db)
        now = datetime(2026, 8, 2, 20, 0, tzinfo=timezone.utc)
        await upsert_sitemap_sources(db, [SITEMAP_URL], now=now)

        first = await claim_due_sitemap_sources(
            db,
            limit=1,
            now=now,
            lease_seconds=30,
        )
        assert len(first) == 1
        assert first[0].claim_token
        assert await claim_due_sitemap_sources(
            db,
            limit=1,
            now=now,
            lease_seconds=30,
        ) == []

        reclaimed_at = now + timedelta(seconds=31)
        reclaimed = await claim_due_sitemap_sources(
            db,
            limit=1,
            now=reclaimed_at,
            lease_seconds=30,
        )
        assert len(reclaimed) == 1
        assert reclaimed[0].claim_token != first[0].claim_token

        stale_completed = await complete_sitemap_source(
            db,
            source=first[0],
            etag="stale",
            last_modified="stale",
            refresh_minutes=30,
            now=reclaimed_at,
        )
        assert stale_completed is False
        assert await complete_sitemap_source(
            db,
            source=reclaimed[0],
            etag="fresh",
            last_modified="fresh",
            refresh_minutes=30,
            now=reclaimed_at,
        ) is True
        await conn.close()

    asyncio.run(run())


def test_product_lease_blocks_duplicate_worker_and_requires_live_token() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        db = FakeDatabase(conn)
        await ensure_target_watcher_tables(db)
        now = datetime(2026, 8, 2, 20, 0, tzinfo=timezone.utc)
        await upsert_product_seeds(
            db,
            [TargetProductSeed(TCIN, PRODUCT_URL)],
            store_id=STORE,
            zip_code=ZIP,
            now=now,
        )

        first = await claim_products_for_offer_poll(
            db,
            limit=1,
            big_ticket_min_reference_price=200,
            price_error_min_discount_percent=69,
            now=now,
            lease_seconds=30,
        )
        assert len(first) == 1
        assert await claim_products_for_offer_poll(
            db,
            limit=1,
            big_ticket_min_reference_price=200,
            price_error_min_discount_percent=69,
            now=now,
            lease_seconds=30,
        ) == []

        reclaimed_at = now + timedelta(seconds=31)
        reclaimed = await claim_products_for_offer_poll(
            db,
            limit=1,
            big_ticket_min_reference_price=200,
            price_error_min_discount_percent=69,
            now=reclaimed_at,
            lease_seconds=30,
        )
        assert len(reclaimed) == 1
        assert reclaimed[0].claim_token != first[0].claim_token

        offer = TargetOffer(
            tcin=TCIN,
            title="Example Console",
            product_url=PRODUCT_URL,
            current_price=99.99,
            regular_price=399.99,
            seller_name="Target",
            shipping_available=True,
            pickup_available=True,
            can_add_to_cart=True,
        )
        with pytest.raises(RuntimeError, match="expired or was reclaimed"):
            await record_exact_offer(
                db,
                product=first[0],
                offer=offer,
                min_event_discount_percent=10,
                normal_interval_minutes=30,
                markdown_interval_seconds=90,
                big_ticket_min_reference_price=200,
                price_error_min_discount_percent=69,
                big_ticket_interval_seconds=45,
                now=reclaimed_at,
            )

        decision = await record_exact_offer(
            db,
            product=reclaimed[0],
            offer=offer,
            min_event_discount_percent=10,
            normal_interval_minutes=30,
            markdown_interval_seconds=90,
            big_ticket_min_reference_price=200,
            price_error_min_discount_percent=69,
            big_ticket_interval_seconds=45,
            now=reclaimed_at,
        )
        assert decision.should_publish is True
        assert await complete_product_work(
            db,
            product=reclaimed[0],
            now=reclaimed_at,
        ) is True
        await conn.close()

    asyncio.run(run())
