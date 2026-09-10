from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_exact_verification_queue import (
    CONFIRMED_API_PRICE_MISSING_RETRY_SECONDS,
    QUEUE_RETRY_BASE_SECONDS,
    QUEUE_TABLE,
    _failure_storage_policy,
    _is_confirmed_api_price_missing,
)
from sniperplug.services.walmart_exact_verification_queue_bulk import (
    enqueue_walmart_exact_verification_candidates_bulk,
)


CONFIRMED_NO_PRICE_ERROR = (
    "retry_kind=api_price_missing exact detail did not provide a numeric current price; "
    "recovery_kind=api_price_missing"
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def candidate(
    item_id: str,
    *,
    current: float = 20.0,
    reference: float = 100.0,
    title: str | None = None,
    safe_snapshot: bool = False,
) -> SourceCandidate:
    attrs = {"finderSourceQuery": "electronics clearance"}
    if safe_snapshot:
        attrs.update(
            {
                "selectedOfferMarketplace": "false",
                "selectedOfferSeller": "Walmart",
                "selectedOfferId": item_id,
                "referencePriceTrusted": "yes",
                "trustedReferencePrice": f"{reference:.2f}",
                "trustedReferenceSource": "search.wasPrice",
            }
        )
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=title or f"Item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        image_url=f"https://i5.walmartimages.com/{item_id}.jpg",
        current_price=current,
        typical_price=reference,
        api_current_price=current,
        api_reference_price=reference,
        api_reference_path="search.wasPrice",
        api_discount_percent=round((reference - current) / reference * 100, 2),
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id=item_id,
        seller_name="Walmart" if safe_snapshot else None,
        fulfillment_type="shipping" if safe_snapshot else None,
        condition="new" if safe_snapshot else None,
        stock_status="in stock" if safe_snapshot else None,
        can_add_to_cart=True if safe_snapshot else None,
        variant_attributes=attrs,
    )


async def _new_db():
    conn = await aiosqlite.connect(":memory:")
    return conn, FakeDatabase(conn)


async def _seed_confirmed_no_price_retry(
    conn,
    db,
    item: SourceCandidate,
    *,
    future: str,
) -> None:
    await enqueue_walmart_exact_verification_candidates_bulk(
        db,
        [item],
        min_discount=50,
        source_label="global_catalog_autoscan:global_catalog_autoscan",
    )
    await conn.execute(
        f"""
        UPDATE {QUEUE_TABLE}
        SET status = 'retry',
            next_attempt_at = ?,
            last_error = ?
        WHERE item_id = ?
        """,
        (future, CONFIRMED_NO_PRICE_ERROR, item.product_id),
    )
    await conn.commit()


def test_confirmed_api_price_missing_uses_existing_retry_ceiling() -> None:
    assert _is_confirmed_api_price_missing(CONFIRMED_NO_PRICE_ERROR) is True
    status, delay = _failure_storage_policy(
        status="retry",
        error=CONFIRMED_NO_PRICE_ERROR,
        attempts=1,
    )
    assert status == "retry"
    assert delay == CONFIRMED_API_PRICE_MISSING_RETRY_SECONDS
    assert delay > QUEUE_RETRY_BASE_SECONDS


def test_single_api_price_missing_keeps_normal_transient_backoff() -> None:
    status, delay = _failure_storage_policy(
        status="retry",
        error="retry_kind=api_price_missing primary detail was incomplete",
        attempts=1,
    )
    assert status == "retry"
    assert delay == QUEUE_RETRY_BASE_SECONDS


def test_unchanged_weak_rediscovery_does_not_rearm_confirmed_no_price_retry() -> None:
    async def run() -> None:
        conn, db = await _new_db()
        try:
            item = candidate("910001")
            future = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
            await _seed_confirmed_no_price_retry(conn, db, item, future=future)

            result = await enqueue_walmart_exact_verification_candidates_bulk(
                db,
                [item],
                min_discount=50,
                source_label="global_catalog_autoscan:global_catalog_autoscan",
            )

            assert result.persisted_rows == 0
            cursor = await conn.execute(
                f"SELECT next_attempt_at FROM {QUEUE_TABLE} WHERE item_id = ?",
                ("910001",),
            )
            assert (await cursor.fetchone())[0] == future
        finally:
            await conn.close()

    asyncio.run(run())


def test_cosmetic_rediscovery_can_persist_without_breaking_no_price_cooldown() -> None:
    async def run() -> None:
        conn, db = await _new_db()
        try:
            item = candidate("910002")
            future = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
            await _seed_confirmed_no_price_retry(conn, db, item, future=future)

            result = await enqueue_walmart_exact_verification_candidates_bulk(
                db,
                [candidate("910002", title="Updated catalog title")],
                min_discount=50,
                source_label="global_catalog_autoscan:global_catalog_autoscan",
            )

            assert result.persisted_rows == 1
            cursor = await conn.execute(
                f"SELECT title, next_attempt_at FROM {QUEUE_TABLE} WHERE item_id = ?",
                ("910002",),
            )
            title, next_attempt_at = await cursor.fetchone()
            assert title == "Updated catalog title"
            assert next_attempt_at == future
        finally:
            await conn.close()

    asyncio.run(run())


def test_complete_fresh_snapshot_wakes_confirmed_no_price_retry() -> None:
    async def run() -> None:
        conn, db = await _new_db()
        try:
            item = candidate("910003")
            future = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
            await _seed_confirmed_no_price_retry(conn, db, item, future=future)

            result = await enqueue_walmart_exact_verification_candidates_bulk(
                db,
                [candidate("910003", safe_snapshot=True)],
                min_discount=50,
                source_label="global_catalog_autoscan:global_catalog_autoscan",
            )

            assert result.persisted_rows == 1
            assert result.safe_snapshot_rows == 1
            cursor = await conn.execute(
                f"SELECT next_attempt_at, discovery_snapshot_json FROM {QUEUE_TABLE} WHERE item_id = ?",
                ("910003",),
            )
            next_attempt_at, snapshot = await cursor.fetchone()
            assert next_attempt_at < future
            assert snapshot
        finally:
            await conn.close()

    asyncio.run(run())


def test_material_catalog_price_change_wakes_confirmed_no_price_retry() -> None:
    async def run() -> None:
        conn, db = await _new_db()
        try:
            item = candidate("910004")
            future = (datetime.now(timezone.utc) + timedelta(hours=4)).isoformat()
            await _seed_confirmed_no_price_retry(conn, db, item, future=future)

            result = await enqueue_walmart_exact_verification_candidates_bulk(
                db,
                [candidate("910004", current=15.0)],
                min_discount=50,
                source_label="global_catalog_autoscan:global_catalog_autoscan",
            )

            assert result.persisted_rows == 1
            cursor = await conn.execute(
                f"SELECT apparent_current_cents, next_attempt_at FROM {QUEUE_TABLE} WHERE item_id = ?",
                ("910004",),
            )
            current_cents, next_attempt_at = await cursor.fetchone()
            assert current_cents == 1500
            assert next_attempt_at < future
        finally:
            await conn.close()

    asyncio.run(run())
