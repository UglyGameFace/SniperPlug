from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services import walmart_exact_queue_bulk_runtime as bulk_runtime
from sniperplug.services.walmart_exact_verification_queue import QUEUE_TABLE
from sniperplug.services.walmart_exact_verification_queue_bulk import (
    enqueue_walmart_exact_verification_candidates_bulk,
)
from sniperplug.services.walmart_fresh_discovery_proof import (
    DISCOVERY_PROOF_MAX_AGE_SECONDS,
    build_fresh_discovery_offer_snapshot,
    promote_fresh_discovery_offer_snapshot,
)


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


class NoNetworkProvider:
    def __init__(self):
        self.calls: list[str] = []

    async def fetch_product_detail_payload(self, item_id: str):
        self.calls.append(item_id)
        raise AssertionError("fresh catalog proof should avoid Walmart refetch")


def aiho_gray_candidate(item_id: str = "123456789") -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Aiho 3-Piece Outdoor Bistro Set, 3-Piece Gray",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=73.99,
        typical_price=None,
        api_current_price=73.99,
        api_reference_price=None,
        api_price_path="selectedOffer.salePrice",
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id="offer-aiho-3-piece-gray",
        variant_label="3-Piece / Gray",
        variant_attributes={
            "selectedOfferNodeSource": "selectedOffer",
            "selectedOfferSeller": "Aiho",
            "selectedOfferId": "offer-aiho-3-piece-gray",
            "selectedOfferFulfillment": "shipping",
            "selectedOfferCondition": "new",
            "selectedOfferMarketplace": "yes",
            "selectedOfferItemPrice": "73.99",
            "selectedOfferItemPriceSource": "selectedOffer.salePrice",
            "selectedOfferShippingCost": "19.99",
            "selectedOfferShippingSource": "selectedOffer.shippingPrice",
            "selectedOfferShippingStatus": "paid",
            "selectedOfferDeliveredPrice": "93.98",
            "selectedOfferDeliveredPriceSource": "selectedOffer.salePrice+selectedOffer.shippingPrice",
            "selectedOfferPublicPriceStatus": "verified_delivered",
            "referencePriceTrusted": "no",
            # This intentionally represents tempting sibling/context pricing.
            # It must never become the selected gray offer's reference proof.
            "referenceContextPrice": "169.99",
            "referenceContextSource": "sibling variant context",
            "crossOfferReferenceBlocked": "yes",
            "color": "Gray",
            "size": "3-Piece",
        },
        color="Gray",
        seller_name="Aiho",
        fulfillment_type="shipping",
        condition="new",
        stock_status="in stock",
        can_add_to_cart=True,
    )


def walmart_same_offer_markdown(item_id: str = "987654321") -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Walmart exact selected offer markdown",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=39.99,
        typical_price=99.99,
        api_current_price=39.99,
        api_reference_price=99.99,
        api_reference_path="selectedOffer.wasPrice",
        api_price_path="selectedOffer.salePrice",
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id="offer-walmart-selected",
        variant_attributes={
            "selectedOfferNodeSource": "selectedOffer",
            "selectedOfferSeller": "Walmart",
            "selectedOfferId": "offer-walmart-selected",
            "selectedOfferMarketplace": "no",
            "selectedOfferItemPrice": "39.99",
            "selectedOfferItemPriceSource": "selectedOffer.salePrice",
            "selectedOfferReferencePrice": "99.99",
            "selectedOfferReferenceSource": "selectedOffer.wasPrice",
            "selectedOfferReferenceTrusted": "yes",
            "selectedOfferReferenceStatus": "verified_same_offer",
            "referencePriceTrusted": "yes",
            "trustedReferencePrice": "99.99",
            "trustedReferenceSource": "selectedOffer.wasPrice",
        },
        seller_name="Walmart",
        condition="new",
        can_add_to_cart=True,
    )


def test_aiho_snapshot_uses_delivered_total_and_does_not_borrow_sibling_reference() -> None:
    candidate = aiho_gray_candidate()
    assert candidate.current_price == 93.98
    assert candidate.api_current_price == 93.98
    assert candidate.shipping_cost == 19.99
    assert candidate.delivered_price == 93.98

    snapshot = build_fresh_discovery_offer_snapshot(candidate)
    assert snapshot
    promoted = promote_fresh_discovery_offer_snapshot(
        snapshot,
        snapshot_at=datetime.now(timezone.utc).isoformat(),
        item_id=candidate.product_id or "",
        apparent_current_cents=9398,
    )

    assert promoted is not None
    assert promoted.current_price == 93.98
    assert promoted.api_current_price == 93.98
    assert promoted.item_price == 73.99
    assert promoted.shipping_cost == 19.99
    assert promoted.seller_name == "Aiho"
    assert promoted.color == "Gray"
    assert promoted.typical_price is None
    assert promoted.api_reference_price is None
    assert promoted.variant_attributes["referencePriceTrusted"] == "no"
    assert promoted.variant_attributes["exactDetailPriceProof"] == "yes"
    assert promoted.variant_attributes["exactDetailPriceProofSource"] == "affiliate_catalog_fresh_snapshot"


def test_same_selected_offer_reference_survives_fresh_promotion() -> None:
    candidate = walmart_same_offer_markdown()
    snapshot = build_fresh_discovery_offer_snapshot(candidate)
    promoted = promote_fresh_discovery_offer_snapshot(
        snapshot,
        snapshot_at=datetime.now(timezone.utc).isoformat(),
        item_id=candidate.product_id or "",
        apparent_current_cents=3999,
    )

    assert promoted is not None
    assert promoted.api_reference_price == 99.99
    assert promoted.api_reference_path == "selectedOffer.wasPrice"
    assert promoted.variant_attributes["referencePriceTrusted"] == "yes"
    assert promoted.variant_attributes["trustedReferencePrice"] == "99.99"


def test_stale_or_price_conflicting_snapshot_falls_through() -> None:
    candidate = aiho_gray_candidate()
    snapshot = build_fresh_discovery_offer_snapshot(candidate)
    stale = datetime.now(timezone.utc) - timedelta(
        seconds=DISCOVERY_PROOF_MAX_AGE_SECONDS + 1
    )
    assert (
        promote_fresh_discovery_offer_snapshot(
            snapshot,
            snapshot_at=stale.isoformat(),
            item_id=candidate.product_id or "",
            apparent_current_cents=9398,
        )
        is None
    )
    assert (
        promote_fresh_discovery_offer_snapshot(
            snapshot,
            snapshot_at=datetime.now(timezone.utc).isoformat(),
            item_id=candidate.product_id or "",
            apparent_current_cents=7399,
        )
        is None
    )


def test_unknown_marketplace_shipping_is_not_snapshot_eligible() -> None:
    item_id = "555555555"
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Third party offer with unknown shipping",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=20.0,
        api_current_price=20.0,
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id="third-party-offer",
        seller_name="Marketplace Seller",
        variant_attributes={
            "selectedOfferSeller": "Marketplace Seller",
            "selectedOfferId": "third-party-offer",
            "selectedOfferMarketplace": "yes",
            "selectedOfferItemPrice": "20.00",
            "selectedOfferShippingStatus": "unknown",
        },
    )
    assert candidate.current_price is None
    assert build_fresh_discovery_offer_snapshot(candidate) == ""


def test_runtime_verifies_fresh_catalog_offer_without_network_and_classifies_no_reference() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        candidate = aiho_gray_candidate("444444444")
        result = await enqueue_walmart_exact_verification_candidates_bulk(
            db,
            [candidate],
            min_discount=40,
            source_label="global_catalog_autoscan:test",
        )
        assert result.persisted_rows == 1

        provider = NoNetworkProvider()
        runtime_result = await bulk_runtime.process_actionable_walmart_exact_queue_batch(
            db,
            provider=provider,
            limit=1,
            concurrency=1,
            min_discount=40,
        )

        assert runtime_result.claimed == 1
        assert runtime_result.verified == 1
        assert runtime_result.fresh_catalog_verified == 1
        assert runtime_result.no_reference == 1
        assert runtime_result.failed == 0
        assert provider.calls == []

        cursor = await conn.execute(
            f"""
            SELECT status, exact_current_cents, exact_reference_cents,
                   exact_seller_key, snapshot_json
            FROM {QUEUE_TABLE}
            WHERE item_id = ?
            """,
            ("444444444",),
        )
        row = await cursor.fetchone()
        assert row["status"] == "verified_no_reference"
        assert row["exact_current_cents"] == 9398
        assert row["exact_reference_cents"] is None
        assert "aiho" in row["exact_seller_key"]
        assert "affiliate_catalog_fresh_snapshot" in row["snapshot_json"]
        await conn.close()

    asyncio.run(run())


def test_existing_queue_schema_is_migrated_with_discovery_snapshot_columns() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        try:
            await conn.execute(
                f"""
                CREATE TABLE {QUEUE_TABLE} (
                    item_id TEXT PRIMARY KEY,
                    priority_score INTEGER NOT NULL DEFAULT 0,
                    apparent_current_cents INTEGER,
                    apparent_reference_cents INTEGER,
                    apparent_discount_bps INTEGER NOT NULL DEFAULT 0,
                    title TEXT NOT NULL DEFAULT '',
                    product_url TEXT NOT NULL DEFAULT '',
                    image_url TEXT NOT NULL DEFAULT '',
                    route_hint TEXT NOT NULL DEFAULT '',
                    source_label TEXT NOT NULL DEFAULT '',
                    discovered_count INTEGER NOT NULL DEFAULT 1,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_attempt_at TEXT,
                    next_attempt_at TEXT NOT NULL,
                    last_error TEXT NOT NULL DEFAULT '',
                    lease_token TEXT NOT NULL DEFAULT '',
                    lease_until TEXT,
                    verified_at TEXT,
                    exact_current_cents INTEGER,
                    exact_reference_cents INTEGER,
                    exact_discount_bps INTEGER NOT NULL DEFAULT 0,
                    exact_reference_source TEXT NOT NULL DEFAULT '',
                    exact_offer_id TEXT NOT NULL DEFAULT '',
                    exact_seller_key TEXT NOT NULL DEFAULT '',
                    exact_variant_key TEXT NOT NULL DEFAULT '',
                    exact_condition_key TEXT NOT NULL DEFAULT '',
                    exact_fulfillment_key TEXT NOT NULL DEFAULT '',
                    snapshot_json TEXT NOT NULL DEFAULT ''
                )
                """
            )
            await conn.commit()
            db = FakeDatabase(conn)
            from sniperplug.services.walmart_exact_verification_queue import (
                ensure_walmart_exact_verification_queue,
            )

            await ensure_walmart_exact_verification_queue(db)
            cursor = await conn.execute(f"PRAGMA table_info({QUEUE_TABLE})")
            columns = {row[1] for row in await cursor.fetchall()}
            assert "discovery_snapshot_json" in columns
            assert "discovery_snapshot_at" in columns
        finally:
            await conn.close()

    asyncio.run(run())
