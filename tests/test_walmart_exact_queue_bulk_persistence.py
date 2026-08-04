from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_exact_queue_bulk_persistence import (
    ExactQueuePersistenceOutcome,
    persist_exact_queue_outcomes_bulk,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    _QueueClaim,
    ensure_walmart_exact_verification_queue,
)
from sniperplug.services.walmart_global_offer_memory import (
    GLOBAL_OFFER_MEMORY_TABLE,
    ensure_global_offer_memory_table,
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


class RecordingConnection:
    def __init__(self, conn):
        self.conn = conn
        self.execute_calls = 0

    async def execute(self, sql, params=None):
        self.execute_calls += 1
        if params is None:
            return await self.conn.execute(sql)
        return await self.conn.execute(sql, params)


def exact_candidate(item_id: str, *, current: float = 20.0) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart_exact_detail_queue",
        retailer="Walmart",
        title=f"Exact item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=current,
        typical_price=100.0,
        api_current_price=current,
        api_reference_price=100.0,
        api_reference_path="detail.wasPrice",
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id=f"offer-{item_id}",
        seller_name="Walmart",
        fulfillment_type="shipping",
        condition="new",
        stock_status="in stock",
        can_add_to_cart=True,
        variant_attributes={
            "exactDetailPriceProof": "yes",
            "exactDetailItemId": item_id,
            "walmartSeller": "yes",
            "seller": "Walmart",
            "referencePriceTrusted": "yes",
            "trustedReferencePrice": "100.00",
            "trustedReferenceSource": "detail.wasPrice",
        },
    )


async def insert_claimed_row(
    conn,
    *,
    item_id: str,
    lease_token: str,
    attempt_count: int = 0,
) -> _QueueClaim:
    now = datetime.now(timezone.utc).isoformat()
    await conn.execute(
        f"""
        INSERT INTO {QUEUE_TABLE} (
            item_id, priority_score, apparent_current_cents,
            apparent_reference_cents, apparent_discount_bps,
            title, product_url, image_url, route_hint, source_label,
            discovered_count, first_seen_at, last_seen_at, status,
            attempt_count, next_attempt_at, lease_token, lease_until
        ) VALUES (?, 100, 2000, 10000, 8000, ?, ?, '', '', 'test',
                  1, ?, ?, 'verifying', ?, ?, ?, ?)
        """,
        (
            item_id,
            f"Exact item {item_id}",
            f"https://www.walmart.com/ip/{item_id}",
            now,
            now,
            attempt_count,
            now,
            lease_token,
            (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
        ),
    )
    return _QueueClaim(
        item_id=item_id,
        title=f"Exact item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        image_url="",
        apparent_current_cents=2000,
        apparent_reference_cents=10000,
        route_hint="",
        lease_token=lease_token,
        attempt_count=attempt_count,
    )


def test_24_item_batch_uses_three_persistence_statements() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)
        await ensure_global_offer_memory_table(db)

        observed_at = datetime.now(timezone.utc)
        outcomes = []
        for index in range(24):
            item_id = str(800000 + index)
            claim = await insert_claimed_row(
                conn,
                item_id=item_id,
                lease_token="batch-token",
            )
            outcomes.append(
                ExactQueuePersistenceOutcome(
                    claim=claim,
                    candidate=exact_candidate(item_id),
                    status="verified",
                    error="",
                    observed_at=observed_at,
                )
            )
        await conn.commit()

        recording = RecordingConnection(conn)
        result = await persist_exact_queue_outcomes_bulk(
            recording,
            outcomes,
            min_discount=50,
        )
        await conn.commit()

        assert result.queue_rows == 24
        assert result.offer_rows == 24
        assert result.sql_statements == 3
        assert recording.execute_calls == 3

        cursor = await conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {QUEUE_TABLE}
            WHERE status = 'verified_markdown'
              AND lease_token = ''
              AND exact_current_cents = 2000
              AND exact_reference_cents = 10000
            """
        )
        assert (await cursor.fetchone())[0] == 24

        cursor = await conn.execute(
            f"SELECT COUNT(*) FROM {GLOBAL_OFFER_MEMORY_TABLE}"
        )
        assert (await cursor.fetchone())[0] == 24
        await conn.close()

    asyncio.run(run())


def test_failure_bulk_update_preserves_previous_exact_snapshot() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)
        await ensure_global_offer_memory_table(db)

        claim = await insert_claimed_row(
            conn,
            item_id="900001",
            lease_token="failure-token",
            attempt_count=2,
        )
        await conn.execute(
            f"""
            UPDATE {QUEUE_TABLE}
            SET verified_at = '2026-01-01T00:00:00+00:00',
                exact_current_cents = 2500,
                exact_reference_cents = 5000,
                exact_discount_bps = 5000,
                snapshot_json = '{{"existing":true}}'
            WHERE item_id = '900001'
            """
        )
        await conn.commit()

        result = await persist_exact_queue_outcomes_bulk(
            conn,
            [
                ExactQueuePersistenceOutcome(
                    claim=claim,
                    candidate=None,
                    status="retry",
                    error="temporary detail timeout",
                    observed_at=datetime.now(timezone.utc),
                )
            ],
            min_discount=50,
        )
        await conn.commit()

        assert result.queue_rows == 1
        assert result.offer_rows == 0
        assert result.sql_statements == 1

        cursor = await conn.execute(
            f"""
            SELECT status, attempt_count, last_error, lease_token,
                   exact_current_cents, exact_reference_cents,
                   exact_discount_bps, snapshot_json
            FROM {QUEUE_TABLE}
            WHERE item_id = '900001'
            """
        )
        row = await cursor.fetchone()
        assert row["status"] == "retry"
        assert row["attempt_count"] == 3
        assert row["last_error"] == "temporary detail timeout"
        assert row["lease_token"] == ""
        assert row["exact_current_cents"] == 2500
        assert row["exact_reference_cents"] == 5000
        assert row["exact_discount_bps"] == 5000
        assert row["snapshot_json"] == '{"existing":true}'
        await conn.close()

    asyncio.run(run())


def test_bulk_offer_memory_preserves_learning_and_new_low_semantics() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)
        await ensure_global_offer_memory_table(db)

        item_id = "910001"
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)

        first_claim = await insert_claimed_row(
            conn,
            item_id=item_id,
            lease_token="first",
        )
        await persist_exact_queue_outcomes_bulk(
            conn,
            [
                ExactQueuePersistenceOutcome(
                    claim=first_claim,
                    candidate=exact_candidate(item_id, current=100.0),
                    status="verified",
                    error="",
                    observed_at=start,
                )
            ],
            min_discount=50,
        )
        await conn.commit()

        cursor = await conn.execute(
            f"""
            SELECT candidate_seen_count, stable_price_cents, last_status
            FROM {GLOBAL_OFFER_MEMORY_TABLE}
            """
        )
        first = await cursor.fetchone()
        assert first["candidate_seen_count"] == 1
        assert first["stable_price_cents"] is None
        assert first["last_status"] == "learning"

        await conn.execute(
            f"""
            UPDATE {QUEUE_TABLE}
            SET status = 'verifying', lease_token = 'second',
                lease_until = ?, next_attempt_at = ?
            WHERE item_id = ?
            """,
            (
                (start + timedelta(hours=4, minutes=2)).isoformat(),
                (start + timedelta(hours=4, minutes=1)).isoformat(),
                item_id,
            ),
        )
        second_claim = _QueueClaim(
            item_id=item_id,
            title=f"Exact item {item_id}",
            product_url=f"https://www.walmart.com/ip/{item_id}",
            image_url="",
            apparent_current_cents=10000,
            apparent_reference_cents=10000,
            route_hint="",
            lease_token="second",
            attempt_count=1,
        )
        await persist_exact_queue_outcomes_bulk(
            conn,
            [
                ExactQueuePersistenceOutcome(
                    claim=second_claim,
                    candidate=exact_candidate(item_id, current=100.0),
                    status="verified",
                    error="",
                    observed_at=start + timedelta(hours=4, minutes=1),
                )
            ],
            min_discount=50,
        )
        await conn.commit()

        cursor = await conn.execute(
            f"""
            SELECT candidate_seen_count, stable_price_cents,
                   stable_seen_count, last_status
            FROM {GLOBAL_OFFER_MEMORY_TABLE}
            """
        )
        stable = await cursor.fetchone()
        assert stable["candidate_seen_count"] == 2
        assert stable["stable_price_cents"] == 10000
        assert stable["stable_seen_count"] == 2
        assert stable["last_status"] == "learning"

        await conn.execute(
            f"""
            UPDATE {QUEUE_TABLE}
            SET status = 'verifying', lease_token = 'third',
                lease_until = ?, next_attempt_at = ?
            WHERE item_id = ?
            """,
            (
                (start + timedelta(hours=8, minutes=3)).isoformat(),
                (start + timedelta(hours=8, minutes=2)).isoformat(),
                item_id,
            ),
        )
        third_claim = _QueueClaim(
            item_id=item_id,
            title=f"Exact item {item_id}",
            product_url=f"https://www.walmart.com/ip/{item_id}",
            image_url="",
            apparent_current_cents=2000,
            apparent_reference_cents=10000,
            route_hint="",
            lease_token="third",
            attempt_count=2,
        )
        await persist_exact_queue_outcomes_bulk(
            conn,
            [
                ExactQueuePersistenceOutcome(
                    claim=third_claim,
                    candidate=exact_candidate(item_id, current=20.0),
                    status="verified",
                    error="",
                    observed_at=start + timedelta(hours=8, minutes=2),
                )
            ],
            min_discount=50,
        )
        await conn.commit()

        cursor = await conn.execute(
            f"""
            SELECT current_price_cents, candidate_seen_count,
                   stable_price_cents, lowest_seen_cents, last_status
            FROM {GLOBAL_OFFER_MEMORY_TABLE}
            """
        )
        dropped = await cursor.fetchone()
        assert dropped["current_price_cents"] == 2000
        assert dropped["candidate_seen_count"] == 1
        assert dropped["stable_price_cents"] == 10000
        assert dropped["lowest_seen_cents"] == 2000
        assert dropped["last_status"] == "new_low"
        await conn.close()

    asyncio.run(run())
