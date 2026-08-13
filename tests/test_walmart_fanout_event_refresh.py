from __future__ import annotations

import asyncio
from types import SimpleNamespace

import aiosqlite

from sniperplug.services import walmart_global_deal_fanout_bulk as bulk


class Database:
    def __init__(self, conn) -> None:
        self.conn = conn

    def require_conn(self):
        return self.conn


def card(*, reference: float, discount: float, score: int = 100):
    return SimpleNamespace(
        retailer="walmart",
        selected_offer_id="offer-123",
        sku="item-123",
        upc="",
        url="https://www.walmart.com/ip/123",
        current_price=10.0,
        api_current_price=10.0,
        typical_price=reference,
        api_reference_price=reference,
        discount=discount,
        api_discount_percent=discount,
        score=score,
        label="Example exact Walmart item",
        embed=None,
        variant_attributes={},
        seller_name="Walmart.com",
        variant_label="Standard",
        condition="new",
        fulfillment_type="shipping",
        stock_status="available",
        can_add_to_cart=True,
        should_alert=True,
    )


def test_same_current_price_with_new_reference_gets_new_event_version() -> None:
    before = card(reference=15.0, discount=33.33)
    after = card(reference=20.0, discount=50.0)

    assert bulk._delivery_deal_key(before) == bulk._delivery_deal_key(after)
    assert bulk._fanout_event_key(before, retailer="walmart") != bulk._fanout_event_key(
        after,
        retailer="walmart",
    )


def test_same_alert_proof_is_stable_across_reverification() -> None:
    first = card(reference=20.0, discount=50.0, score=145)
    repeated = card(reference=20.0, discount=50.0, score=145)

    assert bulk._fanout_event_key(first, retailer="walmart") == bulk._fanout_event_key(
        repeated,
        retailer="walmart",
    )


def test_score_change_reopens_filter_evaluation_without_changing_delivery_key() -> None:
    low_score = card(reference=20.0, discount=50.0, score=70)
    passing_score = card(reference=20.0, discount=50.0, score=90)

    assert bulk._delivery_deal_key(low_score) == bulk._delivery_deal_key(passing_score)
    assert bulk._fanout_event_key(low_score, retailer="walmart") != bulk._fanout_event_key(
        passing_score,
        retailer="walmart",
    )


def test_bulk_ingest_emits_new_version_when_same_price_crosses_threshold(monkeypatch) -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        await conn.execute(
            f"""
            CREATE TABLE {bulk.QUEUE_TABLE} (
                item_id TEXT PRIMARY KEY,
                verified_at TEXT,
                snapshot_json TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending'
            )
            """
        )
        await conn.commit()
        db = Database(conn)
        await bulk.ensure_global_deal_event_tables_once(db)

        cards = {
            "below": card(reference=15.0, discount=33.33),
            "passing": card(reference=20.0, discount=50.0),
        }
        monkeypatch.setattr(bulk, "_candidate_from_snapshot", lambda value: value)
        monkeypatch.setattr(bulk.legacy, "_exact_card_for_candidate", lambda value: cards[value])

        await conn.execute(
            f"""
            INSERT INTO {bulk.QUEUE_TABLE} (item_id, verified_at, snapshot_json, status)
            VALUES ('123', '2026-08-13T00:00:00+00:00', 'below', 'verified_markdown')
            """
        )
        await conn.commit()
        first = await bulk._ingest_verified_queue_events_bulk(db, limit=10)
        assert first.new_events == 1

        await conn.execute(
            f"""
            UPDATE {bulk.QUEUE_TABLE}
            SET verified_at = '2026-08-13T00:01:00+00:00', snapshot_json = 'passing'
            WHERE item_id = '123'
            """
        )
        await conn.commit()
        second = await bulk._ingest_verified_queue_events_bulk(db, limit=10)
        assert second.new_events == 1

        await conn.execute(
            f"""
            UPDATE {bulk.QUEUE_TABLE}
            SET verified_at = '2026-08-13T00:02:00+00:00'
            WHERE item_id = '123'
            """
        )
        await conn.commit()
        third = await bulk._ingest_verified_queue_events_bulk(db, limit=10)
        assert third.new_events == 0

        count = await (
            await conn.execute(f"SELECT COUNT(*) FROM {bulk.legacy.EVENT_TABLE}")
        ).fetchone()
        assert count == (2,)
        await conn.close()

    asyncio.run(run())


def test_bulk_dm_receipts_use_stable_delivery_key_static() -> None:
    source = open(
        "sniperplug/services/walmart_global_deal_fanout_bulk.py",
        encoding="utf-8",
    ).read()
    assert "delivery_deal_key = _delivery_deal_key(event.card)" in source
    assert source.count("deal_key=delivery_deal_key") == 2
    assert "deal_key=event.deal_key" not in source.split("for preference in preferences:", 1)[1].split(
        "if event_errors:",
        1,
    )[0]
