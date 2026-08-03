from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import sqlite3

from sniperplug.target_watcher.parser import TargetOffer, TargetProductSeed
from sniperplug.target_watcher.storage import (
    TargetCatalogProduct,
    claim_products_for_offer_poll,
    ensure_target_watcher_tables,
    record_exact_offer,
    upsert_product_seeds,
)


TCIN = "91234567"
STORE = "1956"
ZIP = "06604"
URL = f"https://www.target.com/p/example/-/A-{TCIN}"


class AsyncCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    async def fetchone(self):
        return self.cursor.fetchone()

    async def fetchall(self):
        return self.cursor.fetchall()


class AsyncConnection:
    def __init__(self):
        self.raw = sqlite3.connect(":memory:")
        self.raw.row_factory = sqlite3.Row
        self.raw.execute("PRAGMA foreign_keys=ON")

    async def execute(self, sql, params=()):
        return AsyncCursor(self.raw.execute(sql, params))

    async def commit(self):
        self.raw.commit()


class FakeDatabase:
    def __init__(self):
        self.conn = AsyncConnection()

    def require_conn(self):
        return self.conn


def test_target_first_seen_regular_markdown_price_drop_and_duplicate_guard() -> None:
    async def run() -> None:
        db = FakeDatabase()
        await ensure_target_watcher_tables(db)
        now = datetime(2026, 8, 2, 20, 0, tzinfo=timezone.utc)
        assert await upsert_product_seeds(
            db,
            [TargetProductSeed(TCIN, URL)],
            store_id=STORE,
            zip_code=ZIP,
            now=now,
        ) == 1
        product = (
            await claim_products_for_offer_poll(
                db,
                limit=1,
                big_ticket_min_reference_price=200,
                price_error_min_discount_percent=69,
                now=now,
            )
        )[0]
        first = await record_exact_offer(
            db,
            product=product,
            offer=TargetOffer(
                tcin=TCIN,
                title="Example Console",
                product_url=URL,
                current_price=99.99,
                regular_price=399.99,
                shipping_available=True,
                pickup_available=True,
                can_add_to_cart=True,
            ),
            min_event_discount_percent=10,
            normal_interval_minutes=30,
            markdown_interval_seconds=90,
            big_ticket_min_reference_price=200,
            price_error_min_discount_percent=69,
            big_ticket_interval_seconds=45,
            now=now,
        )
        assert first.should_publish is True
        assert first.event_type == "regular_price_markdown"
        assert first.reference_source == "target.redsky.product.price.reg_retail"
        assert first.discount_percent > 74
        assert first.next_check_at == (now + timedelta(seconds=45)).isoformat()

        duplicate = await record_exact_offer(
            db,
            product=product,
            offer=TargetOffer(
                tcin=TCIN,
                title="Example Console",
                product_url=URL,
                current_price=99.99,
                regular_price=399.99,
                shipping_available=True,
                pickup_available=True,
                can_add_to_cart=True,
            ),
            min_event_discount_percent=10,
            normal_interval_minutes=30,
            markdown_interval_seconds=90,
            big_ticket_min_reference_price=200,
            price_error_min_discount_percent=69,
            big_ticket_interval_seconds=45,
            now=now + timedelta(minutes=2),
        )
        assert duplicate.should_publish is False

        lower = await record_exact_offer(
            db,
            product=product,
            offer=TargetOffer(
                tcin=TCIN,
                title="Example Console",
                product_url=URL,
                current_price=79.99,
                regular_price=399.99,
                shipping_available=True,
                pickup_available=True,
                can_add_to_cart=True,
            ),
            min_event_discount_percent=10,
            normal_interval_minutes=30,
            markdown_interval_seconds=90,
            big_ticket_min_reference_price=200,
            price_error_min_discount_percent=69,
            big_ticket_interval_seconds=45,
            now=now + timedelta(minutes=4),
        )
        assert lower.should_publish is True
        assert lower.event_type == "price_drop"
        assert lower.event_key != first.event_key

    asyncio.run(run())


def test_target_uncertain_or_unavailable_offer_never_publishes() -> None:
    async def run() -> None:
        db = FakeDatabase()
        await ensure_target_watcher_tables(db)
        product = TargetCatalogProduct(
            product_key=f"target:{STORE}:{ZIP}:{TCIN}",
            tcin=TCIN,
            store_id=STORE,
            zip_code=ZIP,
            title="Example Console",
            product_url=URL,
            image_url="",
        )
        await upsert_product_seeds(
            db,
            [TargetProductSeed(TCIN, URL)],
            store_id=STORE,
            zip_code=ZIP,
        )
        decision = await record_exact_offer(
            db,
            product=product,
            offer=TargetOffer(
                tcin=TCIN,
                title="Example Console",
                product_url=URL,
                current_price=99.99,
                regular_price=399.99,
                shipping_available=False,
                pickup_available=False,
                can_add_to_cart=False,
            ),
            min_event_discount_percent=10,
            normal_interval_minutes=30,
            markdown_interval_seconds=90,
            big_ticket_min_reference_price=200,
            price_error_min_discount_percent=69,
            big_ticket_interval_seconds=45,
        )
        assert decision.should_publish is False
        assert decision.discount_percent == 0

    asyncio.run(run())
