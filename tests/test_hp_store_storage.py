from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.hp_watcher.parser import HPPriceOffer, ProductPageIdentity
from sniperplug.hp_watcher.storage import (
    OFFER_TABLE,
    PRODUCT_TABLE,
    CatalogProduct,
    claim_products_for_offer_poll,
    claim_products_for_page_refresh,
    ensure_hp_watcher_tables,
    record_exact_offer,
    store_product_identity,
    upsert_product_urls,
)


PRODUCT_URL = "https://www.hp.com/us-en/shop/pdp/hp-travel-25-liter-156-iron-grey-laptop-backpack"
PRODUCT_ID = "3074457345619999999"
SKU = "6B8U4AA"


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_first_seen_hp_msrp_markdown_and_later_price_drop_are_events() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys=ON")
        db = FakeDatabase(conn)
        await ensure_hp_watcher_tables(db)
        now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)

        assert await upsert_product_urls(db, [PRODUCT_URL], now=now) == 1
        page_claims = await claim_products_for_page_refresh(db, limit=10, now=now)
        assert len(page_claims) == 1
        await store_product_identity(
            db,
            product_key=page_claims[0].product_key,
            identity=ProductPageIdentity(
                product_url=PRODUCT_URL,
                sku=SKU,
                catalog_entry_id=PRODUCT_ID,
                title="HP Travel Backpack",
                image_url="https://example.hp.com/backpack.png",
            ),
            refresh_hours=24,
            now=now,
        )

        offers_due = await claim_products_for_offer_poll(db, limit=10, now=now)
        assert len(offers_due) == 1
        product = offers_due[0]
        first = await record_exact_offer(
            db,
            product=product,
            offer=HPPriceOffer(
                product_id=PRODUCT_ID,
                part_number=f"{SKU}#ABA",
                sku=SKU,
                current_price=12.99,
                msrp_price=54.99,
                in_stock=True,
                can_add_to_cart=True,
            ),
            min_event_discount_percent=10,
            normal_interval_minutes=30,
            markdown_interval_seconds=90,
            now=now,
        )
        assert first.should_publish is True
        assert first.event_type == "msrp_markdown"
        assert first.reference_source == "hp.services.priceData.lPrice.msrp"
        assert first.current_price == 12.99
        assert first.reference_price == 54.99
        assert first.discount_percent > 76
        assert first.event_key.startswith("hp-event:v1:")

        duplicate = await record_exact_offer(
            db,
            product=product,
            offer=HPPriceOffer(
                product_id=PRODUCT_ID,
                part_number=f"{SKU}#ABA",
                sku=SKU,
                current_price=12.99,
                msrp_price=54.99,
                in_stock=True,
                can_add_to_cart=True,
            ),
            min_event_discount_percent=10,
            normal_interval_minutes=30,
            markdown_interval_seconds=90,
            now=now + timedelta(minutes=2),
        )
        assert duplicate.should_publish is False
        assert duplicate.event_key == ""

        lower = await record_exact_offer(
            db,
            product=product,
            offer=HPPriceOffer(
                product_id=PRODUCT_ID,
                part_number=f"{SKU}#ABA",
                sku=SKU,
                current_price=9.99,
                msrp_price=54.99,
                in_stock=True,
                can_add_to_cart=True,
            ),
            min_event_discount_percent=10,
            normal_interval_minutes=30,
            markdown_interval_seconds=90,
            now=now + timedelta(minutes=4),
        )
        assert lower.should_publish is True
        assert lower.event_type == "price_drop"
        assert lower.event_key != first.event_key
        await conn.close()

    asyncio.run(run())


def test_missing_msrp_learns_exact_price_then_preserves_markdown_baseline() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_hp_watcher_tables(db)
        now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
        await upsert_product_urls(db, [PRODUCT_URL], now=now)
        product = (await claim_products_for_page_refresh(db, limit=1, now=now))[0]
        await store_product_identity(
            db,
            product_key=product.product_key,
            identity=ProductPageIdentity(
                product_url=PRODUCT_URL,
                sku=SKU,
                catalog_entry_id=PRODUCT_ID,
                title="HP Travel Backpack",
            ),
            refresh_hours=24,
            now=now,
        )
        product = (await claim_products_for_offer_poll(db, limit=1, now=now))[0]

        learned = await record_exact_offer(
            db,
            product=product,
            offer=HPPriceOffer(
                product_id=PRODUCT_ID,
                part_number=SKU,
                sku=SKU,
                current_price=50.0,
                msrp_price=None,
                in_stock=True,
                can_add_to_cart=True,
            ),
            min_event_discount_percent=10,
            normal_interval_minutes=30,
            markdown_interval_seconds=90,
            now=now,
        )
        assert learned.should_publish is False

        drop_time = now + timedelta(hours=1)
        drop = await record_exact_offer(
            db,
            product=product,
            offer=HPPriceOffer(
                product_id=PRODUCT_ID,
                part_number=SKU,
                sku=SKU,
                current_price=20.0,
                msrp_price=None,
                in_stock=True,
                can_add_to_cart=True,
            ),
            min_event_discount_percent=10,
            normal_interval_minutes=30,
            markdown_interval_seconds=90,
            now=drop_time,
        )
        assert drop.should_publish is True
        assert drop.event_type == "price_drop"
        assert drop.reference_price == 50.0
        assert drop.reference_source == "sniperplug.hp.exact_price_history.previous_price"

        stable_time = drop_time + timedelta(minutes=2)
        stable = await record_exact_offer(
            db,
            product=product,
            offer=HPPriceOffer(
                product_id=PRODUCT_ID,
                part_number=SKU,
                sku=SKU,
                current_price=20.0,
                msrp_price=None,
                in_stock=True,
                can_add_to_cart=True,
            ),
            min_event_discount_percent=10,
            normal_interval_minutes=30,
            markdown_interval_seconds=90,
            now=stable_time,
        )
        assert stable.should_publish is False
        assert stable.reference_price == 50.0
        assert stable.reference_source == "sniperplug.hp.exact_price_history.reference_price"
        assert stable.next_check_at == (stable_time + timedelta(seconds=90)).isoformat()

        offer_cursor = await conn.execute(
            f"SELECT reference_price_cents FROM {OFFER_TABLE}"
        )
        product_cursor = await conn.execute(
            f"SELECT reference_price_cents FROM {PRODUCT_TABLE} WHERE product_key = ?",
            (product.product_key,),
        )
        assert (await offer_cursor.fetchone())[0] == 5000
        assert (await product_cursor.fetchone())[0] == 5000
        await conn.close()

    asyncio.run(run())


def test_back_in_stock_event_requires_active_verified_markdown() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_hp_watcher_tables(db)
        now = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)
        await upsert_product_urls(db, [PRODUCT_URL], now=now)
        page_product = (await claim_products_for_page_refresh(db, limit=1, now=now))[0]
        await store_product_identity(
            db,
            product_key=page_product.product_key,
            identity=ProductPageIdentity(PRODUCT_URL, SKU, PRODUCT_ID, "HP Travel Backpack"),
            refresh_hours=24,
            now=now,
        )
        product = (await claim_products_for_offer_poll(db, limit=1, now=now))[0]

        unavailable = await record_exact_offer(
            db,
            product=product,
            offer=HPPriceOffer(PRODUCT_ID, SKU, SKU, 12.99, 54.99, in_stock=False, can_add_to_cart=False),
            min_event_discount_percent=10,
            normal_interval_minutes=30,
            markdown_interval_seconds=90,
            now=now,
        )
        assert unavailable.should_publish is False

        restored = await record_exact_offer(
            db,
            product=product,
            offer=HPPriceOffer(PRODUCT_ID, SKU, SKU, 12.99, 54.99, in_stock=True, can_add_to_cart=True),
            min_event_discount_percent=10,
            normal_interval_minutes=30,
            markdown_interval_seconds=90,
            now=now + timedelta(minutes=30),
        )
        assert restored.should_publish is True
        assert restored.event_type == "back_in_stock"
        await conn.close()

    asyncio.run(run())


def test_exact_offer_identity_mismatch_fails_closed() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_hp_watcher_tables(db)
        product = CatalogProduct(
            product_key="hp-product:test",
            product_url=PRODUCT_URL,
            sku=SKU,
            catalog_entry_id=PRODUCT_ID,
            title="HP Travel Backpack",
            image_url="",
        )
        try:
            await record_exact_offer(
                db,
                product=product,
                offer=HPPriceOffer("999999", SKU, SKU, 12.99, 54.99),
                min_event_discount_percent=10,
                normal_interval_minutes=30,
                markdown_interval_seconds=90,
            )
        except ValueError as error:
            assert "identity" in str(error)
        else:
            raise AssertionError("cross-product HP price response was accepted")
        await conn.close()

    asyncio.run(run())
