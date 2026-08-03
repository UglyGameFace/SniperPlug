from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.hp_watcher.parser import HPPriceOffer
from sniperplug.hp_watcher.price_error_service import (
    claim_price_error_offer_batch,
    is_big_ticket_price_error,
    is_big_ticket_product,
    reschedule_products,
)
from sniperplug.hp_watcher.storage import PRODUCT_TABLE, CatalogProduct, ensure_hp_watcher_tables


BIG_PRODUCT = CatalogProduct(
    product_key="hp-product:big",
    product_url="https://www.hp.com/us-en/shop/pdp/hp-big-ticket-test",
    sku="BIG123",
    catalog_entry_id="1001",
    title="HP Big Ticket Test",
    image_url="",
)
CHEAP_PRODUCT = CatalogProduct(
    product_key="hp-product:cheap",
    product_url="https://www.hp.com/us-en/shop/pdp/hp-cheap-test",
    sku="CHEAP123",
    catalog_entry_id="1002",
    title="HP Cheap Test",
    image_url="",
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def test_69_percent_big_ticket_drop_qualifies_as_price_error() -> None:
    offer = HPPriceOffer(
        product_id=BIG_PRODUCT.catalog_entry_id,
        part_number=BIG_PRODUCT.sku,
        sku=BIG_PRODUCT.sku,
        current_price=299.0,
        msrp_price=1000.0,
        in_stock=True,
        can_add_to_cart=True,
    )
    assert is_big_ticket_product(
        BIG_PRODUCT,
        offer,
        minimum_reference_price=200.0,
    ) is True
    assert is_big_ticket_price_error(
        BIG_PRODUCT,
        offer,
        minimum_reference_price=200.0,
        minimum_discount_percent=69,
    ) is True


def test_cheap_76_percent_accessory_drop_is_not_big_ticket() -> None:
    offer = HPPriceOffer(
        product_id=CHEAP_PRODUCT.catalog_entry_id,
        part_number=CHEAP_PRODUCT.sku,
        sku=CHEAP_PRODUCT.sku,
        current_price=12.99,
        msrp_price=54.99,
        in_stock=True,
        can_add_to_cart=True,
    )
    assert is_big_ticket_product(
        CHEAP_PRODUCT,
        offer,
        minimum_reference_price=200.0,
    ) is False
    assert is_big_ticket_price_error(
        CHEAP_PRODUCT,
        offer,
        minimum_reference_price=200.0,
        minimum_discount_percent=69,
    ) is False


def test_big_ticket_drop_below_69_percent_is_not_price_error() -> None:
    offer = HPPriceOffer(
        product_id=BIG_PRODUCT.catalog_entry_id,
        part_number=BIG_PRODUCT.sku,
        sku=BIG_PRODUCT.sku,
        current_price=310.01,
        msrp_price=1000.0,
        in_stock=True,
        can_add_to_cart=True,
    )
    assert is_big_ticket_price_error(
        BIG_PRODUCT,
        offer,
        minimum_reference_price=200.0,
        minimum_discount_percent=69,
    ) is False


def test_prior_exact_big_ticket_price_can_prove_error_without_msrp() -> None:
    product = CatalogProduct(
        **{
            **BIG_PRODUCT.__dict__,
            "previous_current_price": 800.0,
            "previous_reference_price": 800.0,
            "previous_in_stock": True,
        }
    )
    offer = HPPriceOffer(
        product_id=product.catalog_entry_id,
        part_number=product.sku,
        sku=product.sku,
        current_price=100.0,
        msrp_price=None,
        in_stock=True,
        can_add_to_cart=True,
    )
    assert is_big_ticket_price_error(
        product,
        offer,
        minimum_reference_price=200.0,
        minimum_discount_percent=69,
    ) is True


def test_known_big_ticket_product_gets_protected_claim_priority() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_hp_watcher_tables(db)
        now = datetime(2026, 8, 2, 20, 0, tzinfo=timezone.utc)
        old = (now - timedelta(hours=1)).isoformat()

        await conn.execute(
            f"""
            INSERT INTO {PRODUCT_TABLE} (
                product_key, product_url, sku, catalog_entry_id, title, image_url,
                first_seen_at, last_seen_at, page_next_check_at, offer_checked_at,
                offer_next_check_at, current_price_cents, reference_price_cents
            ) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                BIG_PRODUCT.product_key,
                BIG_PRODUCT.product_url,
                BIG_PRODUCT.sku,
                BIG_PRODUCT.catalog_entry_id,
                BIG_PRODUCT.title,
                old,
                old,
                old,
                old,
                old,
                100000,
                100000,
            ),
        )
        await conn.execute(
            f"""
            INSERT INTO {PRODUCT_TABLE} (
                product_key, product_url, sku, catalog_entry_id, title, image_url,
                first_seen_at, last_seen_at, page_next_check_at, offer_checked_at,
                offer_next_check_at, current_price_cents, reference_price_cents
            ) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                CHEAP_PRODUCT.product_key,
                CHEAP_PRODUCT.product_url,
                CHEAP_PRODUCT.sku,
                CHEAP_PRODUCT.catalog_entry_id,
                CHEAP_PRODUCT.title,
                old,
                old,
                old,
                old,
                old,
                5499,
                5499,
            ),
        )
        await conn.commit()

        claimed = await claim_price_error_offer_batch(
            db,
            limit=1,
            minimum_reference_price=200.0,
            now=now,
        )
        assert [product.product_key for product in claimed] == [BIG_PRODUCT.product_key]

        await reschedule_products(
            db,
            product_keys=[BIG_PRODUCT.product_key],
            delay=timedelta(seconds=45),
            now=now,
        )
        cursor = await conn.execute(
            f"SELECT offer_next_check_at FROM {PRODUCT_TABLE} WHERE product_key = ?",
            (BIG_PRODUCT.product_key,),
        )
        assert (await cursor.fetchone())[0] == (now + timedelta(seconds=45)).isoformat()
        await conn.close()

    asyncio.run(run())
