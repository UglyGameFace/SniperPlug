from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.verified_retailer_events import (
    EVENT_MAX_ATTEMPTS,
    EVENT_TABLE,
    claim_verified_retailer_events,
    ensure_verified_retailer_event_table,
    mark_verified_retailer_event_processed,
    publish_verified_retailer_event,
    release_verified_retailer_event,
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def hp_candidate() -> SourceCandidate:
    return SourceCandidate(
        source_key="hp_store_watcher",
        retailer="HP",
        title="HP Travel Backpack",
        product_url="https://www.hp.com/us-en/shop/pdp/hp-travel-backpack",
        direct_product_url="https://www.hp.com/us-en/shop/pdp/hp-travel-backpack",
        current_price=12.99,
        typical_price=54.99,
        api_current_price=12.99,
        api_reference_price=54.99,
        api_discount_percent=76.38,
        api_reference_path="hp.services.priceData.lPrice.msrp",
        api_price_path="hp.services.priceData.price",
        product_id="3074457345619999999",
        product_id_type="catalog_entry_id",
        sku="6B8U4AA",
        selected_offer_id="hp:3074457345619999999:6B8U4AA",
        seller_name="HP.com",
        variant_attributes={
            "hpStructuredPriceProof": "yes",
            "hpCatalogEntryId": "3074457345619999999",
            "hpNormalizedSku": "6B8U4AA",
            "trustedReferencePrice": "54.99",
            "trustedReferenceSource": "hp.services.priceData.lPrice.msrp",
        },
    )


async def publish_event(db, event_key: str) -> None:
    inserted = await publish_verified_retailer_event(
        db,
        event_key=event_key,
        retailer="hp",
        product_key=f"hp-product:{event_key}",
        event_type="msrp_markdown",
        candidate=hp_candidate(),
        source_verified_at=datetime.now(timezone.utc).isoformat(),
    )
    assert inserted is True


def test_shared_verified_event_outbox_is_durable_and_idempotent() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_verified_retailer_event_table(db)
        verified_at = datetime.now(timezone.utc).isoformat()

        inserted = await publish_verified_retailer_event(
            db,
            event_key="hp-event:v1:abc",
            retailer="hp",
            product_key="hp-product:abc",
            event_type="msrp_markdown",
            candidate=hp_candidate(),
            source_verified_at=verified_at,
        )
        assert inserted is True
        duplicate = await publish_verified_retailer_event(
            db,
            event_key="hp-event:v1:abc",
            retailer="hp",
            product_key="hp-product:abc",
            event_type="msrp_markdown",
            candidate=hp_candidate(),
            source_verified_at=verified_at,
        )
        assert duplicate is False

        claimed = await claim_verified_retailer_events(db, limit=10)
        assert len(claimed) == 1
        event = claimed[0]
        assert event.retailer == "hp"
        assert event.candidate.sku == "6B8U4AA"
        assert event.candidate.api_current_price == 12.99
        assert event.claim_token

        overlapping = await claim_verified_retailer_events(db, limit=10)
        assert overlapping == []

        await mark_verified_retailer_event_processed(
            db,
            event_key=event.event_key,
            claim_token=event.claim_token,
        )
        after = await claim_verified_retailer_events(db, limit=10)
        assert after == []

        cursor = await conn.execute(
            f"SELECT processed_at, attempt_count FROM {EVENT_TABLE} WHERE event_key = ?",
            (event.event_key,),
        )
        row = await cursor.fetchone()
        assert row["processed_at"]
        assert row["attempt_count"] == 1
        await conn.close()

    asyncio.run(run())


def test_failed_old_event_backs_off_so_newer_event_can_be_claimed() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_verified_retailer_event_table(db)
        await publish_event(db, "hp-event:v1:old")
        await publish_event(db, "hp-event:v1:new")
        now = datetime.now(timezone.utc) + timedelta(seconds=1)

        first = await claim_verified_retailer_events(db, limit=1, now=now)
        assert [event.event_key for event in first] == ["hp-event:v1:old"]
        release = await release_verified_retailer_event(
            db,
            event_key=first[0].event_key,
            claim_token=first[0].claim_token,
            error="one destination is temporarily unavailable",
            now=now,
        )
        assert release.released is True
        assert release.dead_lettered is False
        assert release.attempt_count == 1
        assert datetime.fromisoformat(release.retry_at) > now

        while_old_is_deferred = await claim_verified_retailer_events(db, limit=1, now=now)
        assert [event.event_key for event in while_old_is_deferred] == ["hp-event:v1:new"]
        await mark_verified_retailer_event_processed(
            db,
            event_key=while_old_is_deferred[0].event_key,
            claim_token=while_old_is_deferred[0].claim_token,
            now=now,
        )

        retry_time = datetime.fromisoformat(release.retry_at) + timedelta(seconds=1)
        retried = await claim_verified_retailer_events(db, limit=1, now=retry_time)
        assert [event.event_key for event in retried] == ["hp-event:v1:old"]
        await conn.close()

    asyncio.run(run())


def test_persistently_failing_event_dead_letters_after_max_attempts() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_verified_retailer_event_table(db)
        await publish_event(db, "hp-event:v1:terminal")
        await conn.execute(
            f"UPDATE {EVENT_TABLE} SET attempt_count = ? WHERE event_key = ?",
            (EVENT_MAX_ATTEMPTS - 1, "hp-event:v1:terminal"),
        )
        await conn.commit()
        now = datetime.now(timezone.utc) + timedelta(seconds=1)

        claimed = await claim_verified_retailer_events(db, limit=1, now=now)
        assert len(claimed) == 1
        release = await release_verified_retailer_event(
            db,
            event_key=claimed[0].event_key,
            claim_token=claimed[0].claim_token,
            error="permanent destination failure",
            now=now,
        )
        assert release.dead_lettered is True
        assert release.attempt_count == EVENT_MAX_ATTEMPTS
        assert release.retry_at == ""

        assert await claim_verified_retailer_events(
            db,
            limit=1,
            now=now + timedelta(days=1),
        ) == []
        cursor = await conn.execute(
            f"SELECT processed_at, last_error, claim_token, lease_until FROM {EVENT_TABLE} WHERE event_key = ?",
            ("hp-event:v1:terminal",),
        )
        row = await cursor.fetchone()
        assert row["processed_at"] == now.isoformat()
        assert "dead-lettered after 8 attempts" in row["last_error"]
        assert row["claim_token"] == ""
        assert row["lease_until"] is None
        await conn.close()

    asyncio.run(run())
