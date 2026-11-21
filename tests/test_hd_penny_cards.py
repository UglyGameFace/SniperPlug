"""Home Depot penny cards: builder copy/proof, public gate, fanout wiring."""

from __future__ import annotations

import asyncio

import aiosqlite

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.dm_deal_routes import verified_card_route
from sniperplug.services.hd_penny_cards import build_hd_penny_card
from sniperplug.services.hd_penny_public_posts import (
    is_verified_hd_penny_public_card,
    maybe_post_hd_penny_cards,
)
from sniperplug.services.hd_penny_tracker import EVENT_TYPE_FINAL, EVENT_TYPE_PENNY
from sniperplug.services.public_alert_config import ensure_public_alert_table
from sniperplug.services.routing import HOT_DEALS_ROUTE, PRICE_GLITCH_ROUTE
from sniperplug.services.verified_retailer_event_fanout import retailer_fanout_handler


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def penny_candidate(**overrides) -> SourceCandidate:
    base = dict(
        source_key="home_depot_penny_tracker",
        retailer="Home Depot",
        title="Milwaukee M18 Drill Kit",
        product_url="https://www.homedepot.com/p/100123456",
        direct_product_url="https://www.homedepot.com/p/100123456",
        current_price=0.01,
        typical_price=199.99,
        api_current_price=0.01,
        api_reference_price=199.99,
        api_discount_percent=99.99,
        api_price_path="serpapi.home_depot_product.price",
        api_reference_path="serpapi.home_depot_product.original_price",
        deal_lane="hd_penny_penny",
        product_id="100123456",
        product_id_type="home_depot_product_id",
        sku="100123456",
        seller_name="Home Depot",
        variant_attributes={
            "hdPennyProof": "yes",
            "hdPennyStage": "penny",
            "hdPennyEventType": EVENT_TYPE_PENNY,
            "hdPennyStore": "online",
            "hdPennyStageEnteredAt": "2026-11-20T10:00:00+00:00",
            "hdPennyObservedAt": "2026-11-20T12:00:00+00:00",
        },
    )
    base.update(overrides)
    return SourceCandidate(**base)


def final_candidate(**overrides) -> SourceCandidate:
    base = dict(
        source_key="home_depot_penny_tracker",
        retailer="Home Depot",
        title="Glacier Bay Vanity",
        product_url="https://www.homedepot.com/p/100999888",
        direct_product_url="https://www.homedepot.com/p/100999888",
        current_price=25.03,
        typical_price=99.99,
        api_current_price=25.03,
        api_reference_price=99.99,
        api_discount_percent=74.97,
        api_price_path="serpapi.home_depot_product.price",
        api_reference_path="serpapi.home_depot_product.original_price",
        deal_lane="hd_penny_final_stage",
        product_id="100999888",
        product_id_type="home_depot_product_id",
        sku="100999888",
        seller_name="Home Depot",
        variant_attributes={
            "hdPennyProof": "yes",
            "hdPennyStage": "final",
            "hdPennyEventType": EVENT_TYPE_FINAL,
            "hdPennyStore": "1234",
            "hdPennyStageEnteredAt": "2026-11-20T10:00:00+00:00",
            "hdPennyObservedAt": "2026-11-20T12:00:00+00:00",
        },
    )
    base.update(overrides)
    return SourceCandidate(**base)


def _embed_text(card) -> str:
    embed = card.embed
    parts = [embed.title or "", embed.description or ""]
    for field in embed.fields:
        parts.append(f"{field.name} {field.value}")
    if embed.footer:
        parts.append(embed.footer.text or "")
    return "\n".join(parts)


def test_fanout_dispatches_home_depot() -> None:
    handler = retailer_fanout_handler("home_depot")
    assert handler is not None
    assert handler.label == "Home Depot"
    assert handler.build_card is build_hd_penny_card
    assert handler.post_cards is maybe_post_hd_penny_cards


def test_penny_card_states_the_proven_fact_without_promises() -> None:
    card = build_hd_penny_card(penny_candidate(), event_key="hd-penny:test")
    text = _embed_text(card)
    assert "PENNY PRICE" in (card.embed.title or "")
    assert "$0.01" in text
    assert "scans at $0.01" in text
    assert "guaranteed" not in text.lower()
    assert card.api_current_price == 0.01
    assert card.public_post_key == "hd-penny:test"
    # A penny routes as a price glitch for personal DM subscribers.
    assert verified_card_route(card) == PRICE_GLITCH_ROUTE


def test_final_card_uses_sourced_final_stage_copy() -> None:
    card = build_hd_penny_card(final_candidate(), event_key="hd-penny:test2")
    text = _embed_text(card)
    assert "FINAL MARKDOWN" in (card.embed.title or "")
    assert "final markdown stage" in text
    assert "drop to a penny next" in text
    assert "guaranteed" not in text.lower()
    assert verified_card_route(card) in {PRICE_GLITCH_ROUTE, HOT_DEALS_ROUTE}


def test_public_gate_penny_price_is_the_proof() -> None:
    card = build_hd_penny_card(penny_candidate())
    # Even a strict server threshold cannot block a proven penny.
    assert is_verified_hd_penny_public_card(card, min_discount=40) is True


def test_public_gate_final_stage_needs_real_markdown_depth() -> None:
    deep = build_hd_penny_card(final_candidate())
    assert is_verified_hd_penny_public_card(deep, min_discount=10) is True

    shallow = build_hd_penny_card(
        final_candidate(
            current_price=25.03,
            typical_price=30.00,
            api_current_price=25.03,
            api_reference_price=30.00,
            api_discount_percent=16.57,
        )
    )
    # A .03 ending on a 17% markdown is exactly the coincidence the
    # scan-data research warns about — it must not post publicly.
    assert is_verified_hd_penny_public_card(shallow, min_discount=10) is False


def test_public_gate_rejects_missing_proof_and_wrong_retailer() -> None:
    no_proof = penny_candidate()
    no_proof.variant_attributes = {"hdPennyStage": "penny"}
    assert is_verified_hd_penny_public_card(build_hd_penny_card(no_proof), min_discount=10) is False

    wrong_retailer = build_hd_penny_card(penny_candidate())
    wrong_retailer.retailer = "Walmart"
    assert is_verified_hd_penny_public_card(wrong_retailer, min_discount=10) is False

    wrong_url = build_hd_penny_card(
        penny_candidate(
            product_url="https://example.com/p/100123456",
            direct_product_url="https://example.com/p/100123456",
        )
    )
    assert is_verified_hd_penny_public_card(wrong_url, min_discount=10) is False


def test_public_posts_respect_guild_retailer_pick() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        await ensure_public_alert_table(db)
        now = "2026-11-20T00:00:00+00:00"
        await conn.execute(
            "INSERT INTO guild_public_alert_settings "
            "(guild_id, enabled, retailers_json, channel_id, created_at, updated_at) "
            "VALUES (?, 1, ?, ?, ?, ?)",
            (555, '["walmart"]', "777", now, now),
        )
        await conn.commit()

        class FakeBot:
            pass

        bot = FakeBot()
        bot.db = db
        card = build_hd_penny_card(penny_candidate())
        result = await maybe_post_hd_penny_cards(bot=bot, guild_id=555, cards=[card])
        assert result.skipped_wrong_retailer == 1
        assert result.posted == 0

    asyncio.run(run())
