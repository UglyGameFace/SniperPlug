from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.cogs.unified_deal_scanner import send_private_card_batches
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.autoscan_observed_price_memory import (
    select_current_search_queue_candidates,
)
from sniperplug.services.deal_finder_engine import (
    build_direct_product_match_cards,
    merge_direct_product_matches,
)
from sniperplug.services.deal_ranking import rank_review_cards
from sniperplug.services.deal_search_modes import MODE_BEST, rank_for_search_mode
from sniperplug.services.embed_delivery import send_summary_and_embeds
from sniperplug.services.walmart_review_candidates import ReviewCandidateResult


class StrictFollowup:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send(self, *args, **kwargs):
        assert not ("view" in kwargs and kwargs["view"] is None)
        self.calls.append(dict(kwargs))
        return None


class FakeInteraction:
    def __init__(self) -> None:
        self.followup = StrictFollowup()


def walmart_candidate(*, title: str, product_id: str, price: float = 14.88) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=title,
        product_url=f"https://www.walmart.com/ip/{product_id}",
        current_price=price,
        product_id=product_id,
        selected_offer_id=product_id,
        seller_name="Walmart",
        variant_attributes={
            "isMarketPlaceItem": "false",
            "currentPriceSource": "salePrice",
            "referencePriceTrusted": "no",
        },
    )


def generic_review_card(index: int) -> DealCard:
    embed = discord.Embed(
        title=f"Popular branded review lead {index}",
        description="Apple Samsung Walmart seller reviews 9999 rating 5.0 clearance rollback",
    )
    card = DealCard(
        embed=embed,
        url=f"https://example.com/{index}",
        label=f"generic-{index}",
        score=250,
        discount=39.0,
    )
    card.current_price = 1.0 + index
    card.selected_offer_id = f"generic-{index}"
    card.should_alert = False
    return card


def oversized_review_card(index: int) -> DealCard:
    embed = discord.Embed(
        title=f"Large private result {index}",
        description="x" * 4000,
    )
    card = DealCard(
        embed=embed,
        url=f"https://example.com/large/{index}",
        label=f"large-{index}",
        score=0,
        discount=0.0,
    )
    card.selected_offer_id = f"large-{index}"
    card.should_alert = False
    return card


@pytest.mark.asyncio
async def test_shared_embed_delivery_never_passes_explicit_null_view_across_batches():
    interaction = FakeInteraction()
    summary = discord.Embed(title="summary")
    embeds = [
        discord.Embed(title="one", description="a" * 4000),
        discord.Embed(title="two", description="b" * 4000),
    ]

    await send_summary_and_embeds(
        interaction,
        summary=summary,
        embeds=embeds,
        view=None,
        ephemeral=True,
    )

    assert len(interaction.followup.calls) == 3
    assert all(call.get("view", "omitted") is not None for call in interaction.followup.calls)


@pytest.mark.asyncio
async def test_private_deal_batches_never_pass_null_view_on_intermediate_batch():
    interaction = FakeInteraction()
    view = discord.ui.View(timeout=60)

    await send_private_card_batches(
        interaction,
        [oversized_review_card(1), oversized_review_card(2)],
        content="private results",
        view=view,
    )

    assert len(interaction.followup.calls) == 2
    assert "view" not in interaction.followup.calls[0]
    assert interaction.followup.calls[1]["view"] is view


def test_phone_mount_direct_matches_coexist_with_review_leads_and_stay_private():
    direct = build_direct_product_match_cards(
        [walmart_candidate(title="Magnetic Phone Mount for Car Dashboard", product_id="1001")],
        query="phone mount",
    )
    assert len(direct) == 1

    review = ReviewCandidateResult(cards=[generic_review_card(index) for index in range(8)])
    merged = merge_direct_product_matches(review, direct)
    ranked = rank_review_cards(merged.cards)
    mode_ranked = rank_for_search_mode([], ranked, MODE_BEST, limit=5)

    matching = [card for card in mode_ranked.review if getattr(card, "product_search_match", False)]
    assert matching
    card = matching[0]
    assert card.product_match_score == 1.0
    assert card.should_alert is False
    assert card.manual_share_allowed is False
    assert card.deal_lane == "product_search_match"
    assert "Search match — not deal proof" in str(card.embed.to_dict())


def test_direct_match_ranking_caps_visible_pool_before_mode_rerank_can_bury_it():
    direct = build_direct_product_match_cards(
        [walmart_candidate(title="Magnetic Phone Mount for Car Dashboard", product_id="2001")],
        query="phone mount",
    )[0]
    cards = [*[generic_review_card(index) for index in range(8)], direct]

    ranked = rank_review_cards(cards)

    assert len(ranked) == 5
    assert direct in ranked
    assert direct is ranked[0]
    assert direct in rank_for_search_mode([], ranked, MODE_BEST, limit=5).review


def test_discover_queue_readback_only_allows_current_search_item_ids():
    current_overflow = SimpleNamespace(product_id="3001", sku=None)
    unrelated_global = SimpleNamespace(product_id="9999", sku=None)
    foreground_duplicate = SimpleNamespace(product_id="3002", sku=None)
    missing_identity = SimpleNamespace(product_id="", sku=None)

    selected = select_current_search_queue_candidates(
        [unrelated_global, foreground_duplicate, current_overflow, missing_identity],
        search_item_ids={"3001", "3002", "3003"},
        foreground_item_ids={"3002"},
        limit=12,
    )

    assert selected == [current_overflow]
