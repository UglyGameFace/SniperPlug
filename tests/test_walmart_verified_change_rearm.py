from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_exact_verification_queue import QUEUE_TABLE
from sniperplug.services.walmart_exact_verification_queue_bulk import (
    enqueue_walmart_exact_verification_candidates_bulk,
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
    offer_id: str | None = None,
    safe_snapshot: bool = False,
) -> SourceCandidate:
    selected_offer_id = offer_id or item_id
    attrs = {"finderSourceQuery": "clearance"}
    if safe_snapshot:
        attrs.update(
            {
                "selectedOfferMarketplace": "false",
                "selectedOfferSeller": "Walmart",
                "selectedOfferId": selected_offer_id,
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
        selected_offer_id=selected_offer_id,
        seller_name="Walmart" if safe_snapshot else None,
        fulfillment_type="shipping" if safe_snapshot else None,
        condition="new" if safe_snapshot else None,
        stock_status="in stock" if safe_snapshot else None,
        can_add_to_cart=True if safe_snapshot else None,
        variant_attributes=attrs,
    )


async def _verified_row(
    conn,
    *,
    item_id: str,
    next_attempt_at: str,
) -> None:
    await conn.execute(
        f"""
        UPDATE {QUEUE_TABLE}
        SET status = 'verified_markdown', next_attempt_at = ?
        WHERE item_id = ?
        """,
        (next_attempt_at, item_id),
    )
    await conn.commit()


def test_material_price_change_rearms_verified_row_immediately() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        source = "global_catalog_autoscan:global_catalog_autoscan"
        await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [candidate("930001", current=20.0)],
            min_discount=10,
            source_label=source,
        )
        future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        await _verified_row(conn, item_id="930001", next_attempt_at=future)

        result = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [candidate("930001", current=15.0)],
            min_discount=10,
            source_label=source,
        )

        assert result.persisted_rows == 1
        assert result.verified_change_rearmed == 1
        cursor = await conn.execute(
            f"""
            SELECT status, next_attempt_at, apparent_current_cents
            FROM {QUEUE_TABLE}
            WHERE item_id = ?
            """,
            ("930001",),
        )
        row = await cursor.fetchone()
        assert row["status"] == "pending"
        assert row["apparent_current_cents"] == 1500
        assert row["next_attempt_at"] < future
        await conn.close()

    asyncio.run(run())


def test_cosmetic_title_change_does_not_wake_verified_row() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        source = "global_catalog_autoscan:global_catalog_autoscan"
        await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [candidate("930002", title="Old title")],
            min_discount=10,
            source_label=source,
        )
        future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        await _verified_row(conn, item_id="930002", next_attempt_at=future)

        result = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [candidate("930002", title="New title")],
            min_discount=10,
            source_label=source,
        )

        assert result.persisted_rows == 1
        assert result.verified_change_rearmed == 0
        cursor = await conn.execute(
            f"SELECT status, next_attempt_at, title FROM {QUEUE_TABLE} WHERE item_id = ?",
            ("930002",),
        )
        row = await cursor.fetchone()
        assert row["status"] == "verified_markdown"
        assert row["next_attempt_at"] == future
        assert row["title"] == "New title"
        await conn.close()

    asyncio.run(run())


def test_selected_offer_proof_change_rearms_verified_row_at_same_price() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        source = "global_catalog_autoscan:global_catalog_autoscan"
        await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [
                candidate(
                    "930003",
                    offer_id="offer-a",
                    safe_snapshot=True,
                )
            ],
            min_discount=10,
            source_label=source,
        )
        future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        await _verified_row(conn, item_id="930003", next_attempt_at=future)

        result = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [
                candidate(
                    "930003",
                    offer_id="offer-b",
                    safe_snapshot=True,
                )
            ],
            min_discount=10,
            source_label=source,
        )

        assert result.persisted_rows == 1
        assert result.verified_change_rearmed == 1
        cursor = await conn.execute(
            f"SELECT status, next_attempt_at FROM {QUEUE_TABLE} WHERE item_id = ?",
            ("930003",),
        )
        row = await cursor.fetchone()
        assert row["status"] == "pending"
        assert row["next_attempt_at"] < future
        await conn.close()

    asyncio.run(run())
