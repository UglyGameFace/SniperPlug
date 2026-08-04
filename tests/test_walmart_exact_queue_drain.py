from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services import walmart_exact_queue_runtime as runtime
from sniperplug.services.walmart_exact_queue_drain import (
    claim_due_rows_batched,
    tiered_recheck_delay,
)
from sniperplug.services.walmart_exact_queue_health import (
    load_walmart_exact_queue_health,
)
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    enqueue_walmart_exact_verification_candidates,
    ensure_walmart_exact_verification_queue,
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


class CountingConnection:
    def __init__(self, inner):
        self.inner = inner
        self.execute_count = 0
        self.commit_count = 0

    async def execute(self, sql, params=()):
        self.execute_count += 1
        return await self.inner.execute(sql, params)

    async def commit(self):
        self.commit_count += 1
        await self.inner.commit()


class FakeDetailProvider:
    def __init__(self, payloads: dict[str, dict]):
        self.payloads = payloads
        self.inner = WalmartProvider(
            WalmartAffiliateConfig(
                enabled=True,
                consumer_id="test",
                private_key_b64="unused",
            )
        )

    async def fetch_product_detail_payload(self, item_id: str) -> dict:
        return self.payloads[item_id]


def search_candidate(item_id: str, *, current: float = 20.0, reference: float = 100.0) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Search item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=current,
        typical_price=reference,
        api_current_price=current,
        api_reference_price=reference,
        api_reference_path="search.wasPrice",
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id=item_id,
        variant_attributes={
            "referencePriceTrusted": "yes",
            "trustedReferencePrice": f"{reference:.2f}",
            "trustedReferenceSource": "search.wasPrice",
        },
    )


def exact_payload(item_id: str, *, current: float = 20.0, reference: float = 100.0) -> dict:
    return {
        "itemId": int(item_id),
        "name": f"Exact item {item_id}",
        "salePrice": current,
        "wasPrice": reference,
        "isMarketPlaceItem": False,
        "availableOnline": True,
    }


def test_batch_claim_uses_one_atomic_remote_statement_and_excludes_terminal_rows() -> None:
    async def run() -> None:
        inner = await aiosqlite.connect(":memory:")
        await ensure_walmart_exact_verification_queue(FakeDatabase(inner))
        now = datetime.now(timezone.utc)
        due = (now - timedelta(minutes=1)).isoformat()
        for item_id, status in (
            ("100001", "pending"),
            ("100002", "verified_markdown"),
            ("100003", "incomplete_identity"),
        ):
            await inner.execute(
                f"""
                INSERT INTO {QUEUE_TABLE} (
                    item_id, first_seen_at, last_seen_at, status, next_attempt_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (item_id, now.isoformat(), now.isoformat(), status, due),
            )
        await inner.commit()

        conn = CountingConnection(inner)
        claims = await claim_due_rows_batched(conn, now=now, limit=10)

        assert {claim.item_id for claim in claims} == {"100001", "100002"}
        assert len({claim.lease_token for claim in claims}) == 1
        assert conn.execute_count == 1
        assert conn.commit_count == 1

        cursor = await inner.execute(
            f"SELECT item_id, status, lease_token FROM {QUEUE_TABLE} ORDER BY item_id"
        )
        rows = await cursor.fetchall()
        assert rows[0][1] == "verifying" and rows[0][2]
        assert rows[1][1] == "verifying" and rows[1][2] == rows[0][2]
        assert rows[2][1] == "incomplete_identity" and rows[2][2] == ""
        await inner.close()

    asyncio.run(run())


def test_recheck_cadence_prioritizes_public_alert_candidates_sustainably() -> None:
    assert tiered_recheck_delay("verified_markdown", 69) == timedelta(hours=4)
    assert tiered_recheck_delay("verified_markdown", 40) == timedelta(hours=12)
    assert tiered_recheck_delay("verified_markdown", 20) == timedelta(hours=24)
    assert tiered_recheck_delay("verified_no_reference", 0) == timedelta(hours=24)
    assert tiered_recheck_delay("verified_under_threshold", 0) == timedelta(hours=24)
    assert tiered_recheck_delay("not_buyable", 0) == timedelta(hours=24)


def test_health_splits_first_time_work_from_scheduled_rechecks() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)
        now = datetime.now(timezone.utc)
        due = (now - timedelta(minutes=1)).isoformat()
        for item_id, status in (
            ("200001", "pending"),
            ("200002", "verified_markdown"),
            ("200003", "identity_mismatch"),
        ):
            await conn.execute(
                f"""
                INSERT INTO {QUEUE_TABLE} (
                    item_id, first_seen_at, last_seen_at, status, next_attempt_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (item_id, now.isoformat(), now.isoformat(), status, due),
            )
        await conn.commit()

        health = await load_walmart_exact_queue_health(db)

        assert health.due_now == 2
        assert health.initial_due_now == 1
        assert health.recheck_due_now == 1
        assert health.identity_blocked == 1
        await conn.close()

    asyncio.run(run())


def test_runtime_enters_bounded_drain_mode(monkeypatch) -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        db = FakeDatabase(conn)
        candidates = [search_candidate(str(300000 + index)) for index in range(4)]
        await enqueue_walmart_exact_verification_candidates(
            db,
            candidates,
            min_discount=10,
            source_label="global:test",
        )
        payloads = {
            candidate.product_id: exact_payload(
                candidate.product_id,
                current=80.0,
                reference=100.0,
            )
            for candidate in candidates
        }
        provider = FakeDetailProvider(payloads)
        monkeypatch.setattr(runtime, "DRAIN_ACTIONABLE_THRESHOLD", 2)
        monkeypatch.setattr(runtime, "DRAIN_BATCH_SIZE", 4)
        monkeypatch.setattr(runtime, "DRAIN_CONCURRENCY", 2)

        result = await runtime.process_actionable_walmart_exact_queue_batch(
            db,
            provider=provider,
            limit=1,
            concurrency=1,
            min_discount=10,
        )

        assert result.mode == "drain"
        assert result.batch_size == 4
        assert result.concurrency == 2
        assert result.claimed == 4
        assert result.verified == 4
        assert result.pending_total == 0
        assert result.claim_seconds >= 0
        assert result.fetch_seconds >= 0
        assert result.store_seconds >= 0

        cursor = await conn.execute(
            f"SELECT MIN(next_attempt_at), MAX(next_attempt_at) FROM {QUEUE_TABLE}"
        )
        earliest, latest = await cursor.fetchone()
        earliest_dt = datetime.fromisoformat(earliest)
        latest_dt = datetime.fromisoformat(latest)
        assert earliest_dt - datetime.now(timezone.utc) > timedelta(hours=11)
        assert latest_dt - earliest_dt < timedelta(minutes=1)
        await conn.close()

    asyncio.run(run())