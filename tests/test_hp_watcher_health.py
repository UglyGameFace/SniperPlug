from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.hp_watcher.parser import ProductPageIdentity
from sniperplug.hp_watcher.storage import (
    PRODUCT_TABLE,
    set_health_value,
    store_product_identity,
    upsert_product_urls,
)
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.hp_watcher_health import HPWatcherHealth, load_hp_watcher_health
from sniperplug.services.verified_retailer_events import publish_verified_retailer_event


PRODUCT_URL = "https://www.hp.com/us-en/shop/pdp/hp-travel-backpack"


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_stale_healthy_status_is_not_reported_as_healthy() -> None:
    health = HPWatcherHealth(status="healthy", stale=True)
    assert health.ok is False
    assert "**stale**" in health.summary_line()


def test_hp_watcher_health_reads_shared_catalog_and_event_state() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        now = datetime.now(timezone.utc)
        await set_health_value(db, "service_status", "healthy", now=now)
        await set_health_value(db, "last_successful_cycle_at", now.isoformat(), now=now)
        await set_health_value(db, "last_cycle_error", "old recovered failure", now=now)
        await upsert_product_urls(db, [PRODUCT_URL], now=now)
        cursor = await conn.execute(
            f"SELECT product_key FROM {PRODUCT_TABLE} WHERE product_url = ?",
            (PRODUCT_URL,),
        )
        product_key = (await cursor.fetchone())[0]
        await store_product_identity(
            db,
            product_key=product_key,
            identity=ProductPageIdentity(
                product_url=PRODUCT_URL,
                sku="6B8U4AA",
                catalog_entry_id="3074457345619999999",
                title="HP Travel Backpack",
            ),
            refresh_hours=24,
            now=now,
        )
        await conn.execute(
            f"""
            UPDATE {PRODUCT_TABLE}
            SET current_price_cents = 1299,
                reference_price_cents = 5499,
                offer_next_check_at = ?
            WHERE product_key = ?
            """,
            ((now - timedelta(minutes=1)).isoformat(), product_key),
        )
        await conn.commit()
        candidate = SourceCandidate(
            source_key="hp_store_watcher",
            retailer="HP",
            title="HP Travel Backpack",
            product_url=PRODUCT_URL,
            current_price=12.99,
            typical_price=54.99,
        )
        await publish_verified_retailer_event(
            db,
            event_key="hp-event:v1:health",
            retailer="hp",
            product_key=product_key,
            event_type="msrp_markdown",
            candidate=candidate,
            source_verified_at=now.isoformat(),
        )

        health = await load_hp_watcher_health(db)
        assert health.ok is True
        assert health.products == 1
        assert health.identified_products == 1
        assert health.active_markdowns == 1
        assert health.due_offers == 1
        assert health.pending_events == 1
        assert health.last_error == ""
        assert "**healthy**" in health.summary_line()
        await conn.close()

    asyncio.run(run())
