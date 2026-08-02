from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import threading

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services.walmart_exact_queue_health import load_walmart_exact_queue_health
from sniperplug.services.walmart_exact_queue_runtime import (
    maintain_terminal_identity_rows,
    process_actionable_walmart_exact_queue_batch,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    _claim_due_rows,
    enqueue_walmart_exact_verification_candidates,
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


class RecordingInner:
    def __init__(self):
        self.thread_ids: list[int] = []
        self.delegate = WalmartProvider(
            WalmartAffiliateConfig(
                enabled=True,
                consumer_id="test",
                private_key_b64="unused",
            )
        )

    def _candidate_from_item(self, item, *, request):
        self.thread_ids.append(threading.get_ident())
        return self.delegate._candidate_from_item(item, request=request)


class FakeDetailProvider:
    def __init__(self, payloads: dict[str, dict]):
        self.payloads = payloads
        self.inner = RecordingInner()
        self.calls: list[str] = []

    async def fetch_product_detail_payload(self, item_id: str) -> dict:
        self.calls.append(item_id)
        return self.payloads[item_id]


def search_candidate(item_id: str) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Search item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=20.0,
        typical_price=100.0,
        api_current_price=20.0,
        api_reference_price=100.0,
        api_reference_path="search.wasPrice",
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id=item_id,
        variant_attributes={
            "finderSourceQuery": "electronics clearance",
            "referencePriceTrusted": "yes",
            "trustedReferencePrice": "100.00",
            "trustedReferenceSource": "search.wasPrice",
        },
    )


def test_exact_candidate_building_runs_off_discord_event_loop() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        await enqueue_walmart_exact_verification_candidates(
            db,
            [search_candidate("654321")],
            min_discount=50,
            source_label="global:test",
        )
        provider = FakeDetailProvider(
            {
                "654321": {
                    "itemId": 654321,
                    "name": "Exact official item",
                    "salePrice": 19.99,
                    "wasPrice": 59.99,
                    "isMarketPlaceItem": False,
                    "availableOnline": True,
                }
            }
        )
        event_loop_thread = threading.get_ident()

        result = await process_actionable_walmart_exact_queue_batch(
            db,
            provider=provider,
            limit=1,
            concurrency=1,
            min_discount=50,
        )

        assert result.claimed == 1
        assert result.verified == 1
        assert provider.inner.thread_ids
        assert all(thread_id != event_loop_thread for thread_id in provider.inner.thread_ids)
        await conn.close()

    asyncio.run(run())


def test_missing_seller_is_terminal_and_does_not_consume_future_batches() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        await enqueue_walmart_exact_verification_candidates(
            db,
            [search_candidate("777777")],
            min_discount=50,
            source_label="global:test",
        )
        provider = FakeDetailProvider(
            {
                "777777": {
                    "itemId": 777777,
                    "name": "Seller omitted by product detail",
                    "salePrice": 12.0,
                    "wasPrice": 30.0,
                    "availableOnline": True,
                }
            }
        )

        first = await process_actionable_walmart_exact_queue_batch(
            db,
            provider=provider,
            limit=1,
            concurrency=1,
            min_discount=50,
        )
        assert first.claimed == 1
        assert first.identity_blocked == 1
        assert first.identity_missing_seller == 1
        assert first.failed == 0
        assert provider.calls == ["777777"]

        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        await conn.execute(
            f"UPDATE {QUEUE_TABLE} SET next_attempt_at = ? WHERE item_id = ?",
            (past, "777777"),
        )
        await conn.commit()

        second = await process_actionable_walmart_exact_queue_batch(
            db,
            provider=provider,
            limit=1,
            concurrency=1,
            min_discount=50,
        )
        assert second.claimed == 0
        assert second.terminal_quarantined == 1
        assert provider.calls == ["777777"]

        cursor = await conn.execute(
            f"SELECT status, next_attempt_at, last_error FROM {QUEUE_TABLE} WHERE item_id = ?",
            ("777777",),
        )
        status, next_attempt_at, last_error = await cursor.fetchone()
        assert status == "incomplete_identity"
        assert next_attempt_at.startswith("9999-12-31")
        assert "missing seller identity" in last_error
        await conn.close()

    asyncio.run(run())


def test_terminal_identity_rows_do_not_inflate_actionable_health_or_claims() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        await enqueue_walmart_exact_verification_candidates(
            db,
            [search_candidate("111111"), search_candidate("222222")],
            min_discount=50,
            source_label="global:test",
        )
        now = datetime.now(timezone.utc)
        past = (now - timedelta(minutes=1)).isoformat()
        await conn.execute(
            f"""
            UPDATE {QUEUE_TABLE}
            SET status = 'incomplete_identity',
                last_attempt_at = ?,
                next_attempt_at = ?,
                last_error = 'missing seller identity'
            WHERE item_id = '111111'
            """,
            (now.isoformat(), past),
        )
        await conn.commit()

        before = await load_walmart_exact_queue_health(db)
        assert before.identity_blocked == 1
        assert before.due_now == 1

        maintenance = await maintain_terminal_identity_rows(db, now=now)
        assert maintenance.quarantined == 1

        after = await load_walmart_exact_queue_health(db)
        assert after.identity_blocked == 1
        assert after.due_now == 1

        claims = await _claim_due_rows(conn, now=now, limit=10)
        assert [claim.item_id for claim in claims] == ["222222"]
        await conn.close()

    asyncio.run(run())
