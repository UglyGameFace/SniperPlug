from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_global_offer_memory import (
    GLOBAL_OFFER_MEMORY_TABLE,
    ensure_global_offer_memory_table,
    exact_offer_identity,
    observe_exact_offer,
)
from sniperplug.services.walmart_observed_price_memory import (
    build_observed_price_drop_card,
    select_observed_price_drop_cards,
)


class MemoryDB:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def candidate(
    *,
    item_id: str = "123456789",
    offer_id: str = "offer-1",
    seller_id: str = "seller-1",
    seller_name: str = "Exact Seller",
    price: float = 100.0,
    color: str = "black",
    exact: bool = True,
) -> SourceCandidate:
    attrs = {
        "sellerId": seller_id,
        "seller": seller_name,
        "walmartSeller": "no",
        "color": color,
        "size": "4 pack",
        "exactDetailItemId": item_id,
        "exactDetailPriceProof": "yes" if exact else "no",
        "exactDetailCurrentSource": "salePrice",
    }
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Exact product",
        product_url=f"https://www.walmart.com/ip/{item_id}",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        current_price=price,
        api_current_price=price,
        product_id=item_id,
        product_id_type="sku",
        sku=item_id,
        selected_offer_id=offer_id,
        seller_name=seller_name,
        condition="New",
        fulfillment_type="shipping",
        color=color,
        variant_label=f"Color: {color}",
        variant_attributes=attrs,
        stock_status="In stock",
        can_add_to_cart=True,
    )


def run(coro):
    return asyncio.run(coro)


def test_exact_offer_identity_changes_for_seller_variant_and_offer() -> None:
    base = exact_offer_identity(candidate())
    other_seller = exact_offer_identity(candidate(seller_id="seller-2"))
    other_variant = exact_offer_identity(candidate(color="blue"))
    other_offer = exact_offer_identity(candidate(offer_id="offer-2"))

    assert base is not None
    assert other_seller is not None
    assert other_variant is not None
    assert other_offer is not None
    assert len(
        {
            base.identity_key,
            other_seller.identity_key,
            other_variant.identity_key,
            other_offer.identity_key,
        }
    ) == 4


def test_search_only_candidate_cannot_train_price_memory() -> None:
    assert exact_offer_identity(candidate(exact=False)) is None


def test_global_schema_is_compact_and_not_duplicated_per_guild() -> None:
    async def scenario() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = MemoryDB(conn)
        await ensure_global_offer_memory_table(db)
        cursor = await conn.execute(f"PRAGMA table_info({GLOBAL_OFFER_MEMORY_TABLE})")
        columns = {row["name"] for row in await cursor.fetchall()}
        assert "guild_id" not in columns
        assert "title" not in columns
        assert "url" not in columns
        assert "image_url" not in columns
        assert {
            "item_id",
            "offer_id",
            "seller_key",
            "variant_key",
            "condition_key",
            "fulfillment_key",
            "current_price_cents",
            "stable_price_cents",
        }.issubset(columns)
        await conn.close()

    run(scenario())


def test_stable_price_requires_separated_confirmations_before_drop() -> None:
    async def scenario() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = MemoryDB(conn)
        await ensure_global_offer_memory_table(db)

        first_candidate = candidate(price=100.0)
        identity = exact_offer_identity(first_candidate)
        assert identity is not None
        start = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)

        first = await observe_exact_offer(
            conn,
            candidate=first_candidate,
            identity=identity,
            now=start,
            min_discount=50,
        )
        await conn.commit()
        assert first.status == "learning"
        assert first.stable_reference_price is None

        too_soon = await observe_exact_offer(
            conn,
            candidate=candidate(price=100.0),
            identity=identity,
            now=start + timedelta(minutes=30),
            min_discount=50,
        )
        await conn.commit()
        assert too_soon.status == "learning"
        assert too_soon.stable_reference_price is None

        confirm = await observe_exact_offer(
            conn,
            candidate=candidate(price=100.0),
            identity=identity,
            now=start + timedelta(hours=4),
            min_discount=50,
        )
        await conn.commit()
        assert confirm.status == "learning"

        drop_candidate = candidate(price=40.0)
        drop = await observe_exact_offer(
            conn,
            candidate=drop_candidate,
            identity=identity,
            now=start + timedelta(hours=8),
            min_discount=50,
        )
        await conn.commit()
        assert drop.status == "new_low"
        assert drop.should_public_post is True
        assert drop.stable_reference_price == 100.0
        assert drop.current_price == 40.0
        assert drop.drop_percent == 60.0
        assert drop.stable_seen_count >= 2

        decision = type(
            "Decision",
            (),
            {
                "stable_reference_price": drop.stable_reference_price,
                "current_price": drop.current_price,
                "drop_percent": drop.drop_percent,
                "drop_dollars": drop.drop_dollars,
                "stable_seen_count": drop.stable_seen_count,
                "reason": drop.reason,
            },
        )()
        card = build_observed_price_drop_card(
            drop_candidate,
            identity,
            decision,
            min_discount=50,
        )
        assert card.api_reference_price == 100.0
        assert card.api_current_price == 40.0
        assert card.variant_attributes["priceMemorySellerKey"] == identity.seller_key
        assert card.variant_attributes["priceMemoryVariantKey"] == identity.variant_key
        assert card.variant_attributes["trustedReferenceSource"] == (
            "sniperplug.global_exact_offer_memory.stable_price"
        )
        await conn.close()

    run(scenario())


def test_same_item_different_seller_never_reads_other_seller_baseline() -> None:
    async def scenario() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = MemoryDB(conn)
        await ensure_global_offer_memory_table(db)
        start = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)

        seller_one = candidate(seller_id="seller-1", price=100.0)
        identity_one = exact_offer_identity(seller_one)
        assert identity_one is not None
        await observe_exact_offer(conn, candidate=seller_one, identity=identity_one, now=start)
        await observe_exact_offer(
            conn,
            candidate=candidate(seller_id="seller-1", price=100.0),
            identity=identity_one,
            now=start + timedelta(hours=4),
        )
        await conn.commit()

        seller_two = candidate(seller_id="seller-2", price=20.0)
        identity_two = exact_offer_identity(seller_two)
        assert identity_two is not None
        decision = await observe_exact_offer(
            conn,
            candidate=seller_two,
            identity=identity_two,
            now=start + timedelta(hours=8),
        )
        await conn.commit()

        assert identity_one.identity_key != identity_two.identity_key
        assert decision.status == "learning"
        assert decision.stable_reference_price is None
        assert decision.should_public_post is False
        await conn.close()

    run(scenario())


def test_expired_stable_reference_cannot_create_public_drop() -> None:
    async def scenario() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = MemoryDB(conn)
        await ensure_global_offer_memory_table(db)
        start = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        base = candidate(price=100.0)
        identity = exact_offer_identity(base)
        assert identity is not None
        await observe_exact_offer(conn, candidate=base, identity=identity, now=start)
        await observe_exact_offer(
            conn,
            candidate=candidate(price=100.0),
            identity=identity,
            now=start + timedelta(hours=4),
        )
        await conn.commit()

        expired = await observe_exact_offer(
            conn,
            candidate=candidate(price=25.0),
            identity=identity,
            now=start + timedelta(days=31),
            min_discount=50,
        )
        await conn.commit()
        assert expired.status == "learning"
        assert expired.stable_reference_price is None
        assert expired.should_public_post is False
        await conn.close()

    run(scenario())


def test_selector_does_not_write_search_only_rows() -> None:
    async def scenario() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = MemoryDB(conn)
        result = await select_observed_price_drop_cards(
            db,
            guild_id=1,
            candidates=[candidate(exact=False)],
            min_discount=50,
        )
        cursor = await conn.execute(f"SELECT COUNT(*) AS count FROM {GLOBAL_OFFER_MEMORY_TABLE}")
        row = await cursor.fetchone()
        assert row["count"] == 0
        assert result.cards == []
        assert result.decisions[0].status == "unverified_identity"
        await conn.close()

    run(scenario())


def test_concurrent_first_observations_share_one_global_row() -> None:
    async def scenario() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = MemoryDB(conn)
        await ensure_global_offer_memory_table(db)
        item = candidate(price=100.0)
        identity = exact_offer_identity(item)
        assert identity is not None
        now = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)

        results = await asyncio.gather(
            observe_exact_offer(conn, candidate=item, identity=identity, now=now),
            observe_exact_offer(conn, candidate=candidate(price=100.0), identity=identity, now=now),
        )
        await conn.commit()

        cursor = await conn.execute(
            f"SELECT COUNT(*) AS count FROM {GLOBAL_OFFER_MEMORY_TABLE}"
        )
        row = await cursor.fetchone()
        assert row["count"] == 1
        assert all(result.status == "learning" for result in results)
        await conn.close()

    run(scenario())


def test_autoscan_does_not_use_legacy_guild_price_memory_for_public_proof() -> None:
    source = open(
        "sniperplug/services/autoscan_observed_price_memory.py",
        encoding="utf-8",
    ).read()
    assert "select_price_intelligent_cards" not in source
    assert "global exact-offer" in source
    assert "memory_cards = hunt.dedupe_cards([*verified_cards, *observed_memory.cards])" in source
