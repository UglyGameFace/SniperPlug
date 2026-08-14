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


def _candidate(item_id: str, *, current: float) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"No-reference item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=current,
        api_current_price=current,
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id=item_id,
        variant_attributes={"finderSourceQuery": "clearance"},
    )


def test_material_catalog_change_wakes_weekly_no_reference_row_immediately() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        source = "global_catalog_autoscan:global_catalog_autoscan"
        item_id = "940001"

        await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [_candidate(item_id, current=20.0)],
            min_discount=10,
            source_label=source,
        )
        now = datetime.now(timezone.utc)
        future = (now + timedelta(days=7)).isoformat()
        await conn.execute(
            f"""
            UPDATE {QUEUE_TABLE}
            SET status = 'verified_no_reference',
                verified_at = ?,
                next_attempt_at = ?
            WHERE item_id = ?
            """,
            (now.isoformat(), future, item_id),
        )
        await conn.commit()

        result = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [_candidate(item_id, current=15.0)],
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
            (item_id,),
        )
        row = await cursor.fetchone()
        assert row["status"] == "pending"
        assert row["apparent_current_cents"] == 1500
        assert row["next_attempt_at"] < future
        await conn.close()

    asyncio.run(run())
