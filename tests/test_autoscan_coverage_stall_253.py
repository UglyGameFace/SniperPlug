from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services import walmart_exact_queue_bulk_runtime as bulk_runtime
from sniperplug.services.hp_watcher_health import HPWatcherHealth
from sniperplug.services.target_watcher_health import TargetWatcherHealth
from sniperplug.services.walmart_catalog_discovery_only import (
    CATALOG_MAX_PAGE,
    catalog_pages_for_completed_passes,
)
from sniperplug.services.walmart_exact_queue_health import WalmartExactQueueHealth
from sniperplug.services.walmart_exact_verification_queue import (
    QUEUE_TABLE,
    ensure_walmart_exact_verification_queue,
)
from sniperplug.services.walmart_exact_verification_queue_bulk import (
    enqueue_walmart_exact_verification_candidates_bulk,
)
from sniperplug.services.walmart_fanout_decisions import (
    classify_public_fanout_result,
    decision_now,
    load_walmart_fanout_decisions,
    record_walmart_fanout_decisions,
)


class FakeDatabase:
    def __init__(self, conn) -> None:
        self.conn = conn

    def require_conn(self):
        return self.conn


def safe_walmart_candidate(item_id: str, *, current: float = 39.99) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Safe fresh Walmart item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=current,
        typical_price=99.99,
        api_current_price=current,
        api_reference_price=99.99,
        api_reference_path="selectedOffer.wasPrice",
        api_price_path="selectedOffer.salePrice",
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id=f"offer-{item_id}",
        seller_name="Walmart",
        condition="new",
        stock_status="in stock",
        can_add_to_cart=True,
        variant_attributes={
            "selectedOfferNodeSource": "selectedOffer",
            "selectedOfferSeller": "Walmart",
            "selectedOfferId": f"offer-{item_id}",
            "selectedOfferCondition": "new",
            "selectedOfferMarketplace": "no",
            "selectedOfferItemPrice": f"{current:.2f}",
            "selectedOfferItemPriceSource": "selectedOffer.salePrice",
            "selectedOfferReferencePrice": "99.99",
            "selectedOfferReferenceSource": "selectedOffer.wasPrice",
            "selectedOfferReferenceTrusted": "yes",
            "referencePriceTrusted": "yes",
            "trustedReferencePrice": "99.99",
            "trustedReferenceSource": "selectedOffer.wasPrice",
        },
    )


def unsafe_walmart_candidate(item_id: str) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=f"Unsafe incomplete Walmart item {item_id}",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=39.99,
        api_current_price=39.99,
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        # Deliberately no selected offer or seller: strict fresh proof must fail.
        selected_offer_id=None,
        seller_name=None,
        condition="new",
        stock_status="in stock",
        can_add_to_cart=True,
        variant_attributes={},
    )


def test_open_page_circuit_keeps_tiny_api_only_recovery_claim_lane(monkeypatch) -> None:
    async def run() -> None:
        captured: dict[str, int] = {}

        async def noop_schema(_db) -> None:
            return None

        async def fake_maintenance(_db):
            return SimpleNamespace(quarantined=0, rearmed=0)

        async def noop_prune(_conn, *, now) -> None:
            return None

        async def fake_pressure(_db, *, cap):
            return WalmartExactQueueHealth(due_now=1074, recovery_due_now=1074)

        async def fake_claim(_conn, *, now, limit, page_recovery_claim_cap):
            captured["cap"] = page_recovery_claim_cap
            captured["limit"] = limit
            return []

        monkeypatch.setattr(bulk_runtime, "ensure_exact_runtime_schema_once", noop_schema)
        monkeypatch.setattr(bulk_runtime, "maintain_terminal_identity_rows_bounded", fake_maintenance)
        monkeypatch.setattr(bulk_runtime, "maybe_prune_walmart_exact_queue_bounded", noop_prune)
        monkeypatch.setattr(bulk_runtime, "load_walmart_exact_queue_pressure", fake_pressure)
        monkeypatch.setattr(bulk_runtime, "claim_due_rows_batched", fake_claim)
        monkeypatch.setattr(bulk_runtime, "_page_challenge_circuit_remaining_seconds", lambda _provider: 600)
        monkeypatch.setattr(bulk_runtime, "_page_challenge_circuit_open", lambda _provider: True)

        result = await bulk_runtime.process_actionable_walmart_exact_queue_batch(
            FakeDatabase(object()),
            provider=object(),
            limit=8,
            concurrency=2,
        )

        assert captured["cap"] == bulk_runtime.API_ONLY_PAGE_RECOVERY_CLAIM_CAP
        assert captured["cap"] == 2
        assert captured["limit"] == 8
        assert result.page_circuit_open is True
        assert result.pending_total == 1074

    asyncio.run(run())


def test_strict_fresh_catalog_proof_rearms_terminal_identity_rows_immediately() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)
        now = datetime.now(timezone.utc)
        terminal_next = "9999-12-31T23:59:59+00:00"

        for item_id, status in (
            ("730001", "incomplete_identity"),
            ("730002", "identity_mismatch"),
        ):
            await conn.execute(
                f"""
                INSERT INTO {QUEUE_TABLE} (
                    item_id, priority_score, apparent_current_cents,
                    apparent_reference_cents, apparent_discount_bps,
                    title, product_url, source_label,
                    first_seen_at, last_seen_at, status, next_attempt_at
                ) VALUES (?, 1, 3999, 9999, 6000, ?, ?, 'old', ?, ?, ?, ?)
                """,
                (
                    item_id,
                    f"Old terminal item {item_id}",
                    f"https://www.walmart.com/ip/{item_id}",
                    (now - timedelta(days=8)).isoformat(),
                    now.isoformat(),
                    status,
                    terminal_next,
                ),
            )
        await conn.commit()

        result = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [safe_walmart_candidate("730001"), safe_walmart_candidate("730002")],
            min_discount=40,
            source_label="global_catalog_autoscan:test",
        )

        cursor = await conn.execute(
            f"""
            SELECT item_id, status, next_attempt_at, discovery_snapshot_json
            FROM {QUEUE_TABLE}
            WHERE item_id IN ('730001', '730002')
            ORDER BY item_id
            """
        )
        rows = await cursor.fetchall()
        assert len(rows) == 2
        assert all(row["status"] == "pending" for row in rows)
        assert all(row["discovery_snapshot_json"] for row in rows)
        assert all(row["next_attempt_at"] < terminal_next for row in rows)
        assert getattr(result, "terminal_identity_rearmed", 0) == 2
        assert "terminal identity rescued **2**" in result.summary_line()
        await conn.close()

    asyncio.run(run())


def test_incomplete_catalog_hint_cannot_escape_terminal_identity_quarantine() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_walmart_exact_verification_queue(db)
        now = datetime.now(timezone.utc)
        item_id = "730003"
        await conn.execute(
            f"""
            INSERT INTO {QUEUE_TABLE} (
                item_id, priority_score, apparent_current_cents,
                title, product_url, source_label,
                first_seen_at, last_seen_at, status, next_attempt_at
            ) VALUES (?, 1, 3999, ?, ?, 'global_catalog_autoscan:test', ?, ?, 'incomplete_identity', ?)
            """,
            (
                item_id,
                f"Unsafe incomplete Walmart item {item_id}",
                f"https://www.walmart.com/ip/{item_id}",
                (now - timedelta(days=8)).isoformat(),
                now.isoformat(),
                "9999-12-31T23:59:59+00:00",
            ),
        )
        await conn.commit()

        result = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [unsafe_walmart_candidate(item_id)],
            min_discount=40,
            source_label="global_catalog_autoscan:test",
        )

        cursor = await conn.execute(
            f"SELECT status, discovery_snapshot_json FROM {QUEUE_TABLE} WHERE item_id = ?",
            (item_id,),
        )
        row = await cursor.fetchone()
        assert row["status"] == "incomplete_identity"
        assert not row["discovery_snapshot_json"]
        assert getattr(result, "terminal_identity_rearmed", 0) == 0
        await conn.close()

    asyncio.run(run())


def test_catalog_page_window_advances_across_full_passes_and_wraps() -> None:
    assert CATALOG_MAX_PAGE == 40
    assert catalog_pages_for_completed_passes(0) == (1, 2)
    assert catalog_pages_for_completed_passes(1) == (3, 4)
    assert catalog_pages_for_completed_passes(18) == (37, 38)
    assert catalog_pages_for_completed_passes(19) == (39, 40)
    assert catalog_pages_for_completed_passes(20) == (1, 2)
    # Production screenshot was around 98 completed passes: it should no longer
    # return to page 1 merely because the route cursor wrapped many times.
    assert catalog_pages_for_completed_passes(98) == (37, 38)


def test_queue_health_explains_large_page_backlog_vs_per_batch_capacity() -> None:
    health = WalmartExactQueueHealth(
        total=20005,
        due_now=1074,
        recovery_due_now=1074,
        identity_blocked=12390,
        verified=4239,
        page_recovery_mode="api_only",
        page_recovery_batch_cap=2,
        page_circuit_remaining_seconds=600,
    )
    line = health.summary_line()
    assert "due backlog now **1074**" in line
    assert "API-only while Walmart HTML challenge circuit is open" in line
    assert "claimable this batch **2**/**1074**" in line
    assert "cap **2**" in line


def test_fanout_decision_provenance_round_trips_and_classifies_real_skip_reason() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)

        duplicate_result = SimpleNamespace(
            posted=0,
            skipped_recent_alert_duplicate=1,
            skipped_reserved_duplicate=0,
            skipped_disabled=0,
            skipped_wrong_retailer=0,
            skipped_not_alertable=0,
            not_alertable_reasons=(),
            errors=(),
        )
        outcome, detail = classify_public_fanout_result(
            duplicate_result,
            discount=63.0,
            threshold=40,
        )
        assert outcome == "recent_duplicate"
        assert "duplicate" in detail

        decisions = [
            decision_now(
                event_key="event-1",
                guild_id=123,
                outcome=outcome,
                threshold=40,
                detail=detail,
            ),
            decision_now(
                event_key="event-2",
                guild_id=123,
                outcome="below_threshold",
                threshold=50,
                detail="below historical 50% public threshold",
            ),
        ]
        assert await record_walmart_fanout_decisions(db, decisions) == 2
        loaded = await load_walmart_fanout_decisions(
            db,
            guild_id=123,
            event_keys=["event-1", "event-2", "missing"],
        )
        assert loaded["event-1"].outcome == "recent_duplicate"
        assert loaded["event-1"].threshold == 40
        assert loaded["event-2"].outcome == "below_threshold"
        assert loaded["event-2"].threshold == 50
        await conn.close()

    asyncio.run(run())


def test_not_started_standalone_watcher_health_is_actionable() -> None:
    hp = HPWatcherHealth().summary_line()
    target = TargetWatcherHealth().summary_line()
    assert "SniperPlug-HP-Watcher" in hp
    assert "hp_watcher_main.py" in hp
    assert "same Turso DB" in hp
    assert "SniperPlug-Target-Watcher" in target
    assert "target_watcher_main.py" in target
    assert "same Turso DB" in target
