from __future__ import annotations

from types import SimpleNamespace

import discord

from sniperplug.models.deal import NormalizedDeal
from sniperplug.services.anomaly_score import score_deal_anomaly
from sniperplug.services.deal_search_modes import MODE_BEST, rank_for_search_mode
from sniperplug.services.product_demand_evidence import assess_product_demand


def _record(*, retailer="walmart", rating="4.7", reviews="800"):
    return SimpleNamespace(retailer=retailer, variant_attributes={"rating": rating, "reviews": reviews})


def test_walmart_reviews_are_engagement_proxy_not_sold_volume():
    evidence = assess_product_demand(_record())
    assert evidence.status == "retailer_engagement"
    assert evidence.rank_points > 0
    assert evidence.review_count == 800
    assert "proxy" in evidence.reason
    assert "sales" not in evidence.status


def test_unrelated_retailer_or_unknown_evidence_gets_no_bonus():
    assert assess_product_demand(_record(retailer="unknown")).rank_points == 0
    assert assess_product_demand(_record(retailer="ebay")).status == "unverified"


def test_malformed_or_low_quality_reviews_earn_no_priority():
    for rating, reviews in (
        ("4.9", "0"), ("3.1", "10000"), ("NaN", "500"),
        ("5.1", "200"), ("4.8", "-200"), ("4.8", "1e99"),
        ("4.8", "100.5"), ("broken", "1000"),
    ):
        assert assess_product_demand(_record(rating=rating, reviews=reviews)).rank_points == 0


def test_embed_words_cannot_fake_product_interest():
    record = SimpleNamespace(
        retailer="walmart", variant_attributes={},
        embed=discord.Embed(description="5 stars, 999999 reviews and viral"),
    )
    assert assess_product_demand(record).rank_points == 0


def test_real_normalized_deal_can_use_walmart_review_metadata():
    deal = NormalizedDeal(
        title="Xbox Wireless Controller", retailer="walmart",
        product_url="https://www.walmart.com/ip/123",
        current_price=50.0, typical_price=100.0,
        variant_attributes={"rating": "4.5", "reviews": "650"},
    )
    scored = score_deal_anomaly(deal)
    assert any("engagement proxy" in reason for reason in scored.reasons)
    assert not any("High-demand category" in reason for reason in scored.reasons)


def test_best_picks_prefers_item_engagement_when_offers_are_equal():
    def card(name, attrs):
        return SimpleNamespace(
            retailer="walmart", variant_attributes=attrs, label=name,
            current_price=75.0, discount=50.0, score=100, should_alert=True,
            embed=discord.Embed(title="Controller", description="Walmart verified deal"),
        )
    unknown = card("without proof", {})
    engagement = card("item-level engagement", {"rating": "4.8", "reviews": "2500"})
    result = rank_for_search_mode([unknown, engagement], [], MODE_BEST)
    assert result.verified[0] is engagement
    assert "not verified sales demand" in result.note


def test_exact_queue_prioritizes_source_backed_interest_without_extra_fetches():
    from sniperplug.models.candidate import SourceCandidate
    from sniperplug.services.walmart_exact_price_enrichment import _enrichment_priority

    def candidate(attrs):
        return SourceCandidate(
            source_key="walmart", retailer="walmart",
            title="Xbox Wireless Controller",
            product_url="https://www.walmart.com/ip/123",
            current_price=25.0, api_current_price=25.0,
            variant_attributes=attrs,
        )

    plain = _enrichment_priority(candidate({}), min_discount=40)
    popular = _enrichment_priority(
        candidate({"rating": "4.8", "reviews": "2300"}), min_discount=40
    )
    assert popular > plain
    assert popular - plain <= 45.0


def test_brand_and_hot_keyword_scoring_does_not_match_inside_generic_words():
    from sniperplug.services.deal_search_modes import (
        brand_score, global_demand_score, has_popular_brand,
    )
    record = SimpleNamespace(
        label="Algae extract for aquarium",
        embed=discord.Embed(description="Product links and maximum shipping information"),
    )
    assert brand_score(record) == 0.0
    assert not has_popular_brand(record)
    assert global_demand_score(record) == 0.0

    real_brand = SimpleNamespace(
        label="LG 27 inch 4K gaming monitor",
        embed=discord.Embed(description="Full product details"),
    )
    assert brand_score(real_brand) > 0
    assert global_demand_score(real_brand) >= 7.0



def test_bestseller_position_alone_never_claims_sales_demand():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    data = _record(rating="3.0", reviews="9999")
    data.variant_attributes.update({
        "walmartBestsellerSortRequested": "yes",
        "walmartBestsellerSearchPosition": "1",
        "walmartBestsellerSearchQuery": "console",
        "walmartBestsellerObservedAt": now,
    })
    assert assess_product_demand(data).rank_points == 0


def test_top_five_bestseller_request_gives_only_small_secondary_proxy():
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    baseline = _record()
    sampled = _record()
    sampled.variant_attributes.update({
        "walmartBestsellerSortRequested": "yes",
        "walmartBestsellerSearchPosition": "3",
        "walmartBestsellerSearchQuery": "console",
        "walmartBestsellerObservedAt": now,
    })
    assert assess_product_demand(sampled).rank_points == assess_product_demand(baseline).rank_points + 4
    assert assess_product_demand(sampled).bestseller_search_position == 3
    assert "API ordering unverified" in assess_product_demand(sampled).reason


def test_bestseller_sort_provenance_cannot_be_invented_by_unsorted_search():
    from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
    from sniperplug.providers.base import ProviderScanRequest
    provider = WalmartProvider(config=WalmartAffiliateConfig())
    payload = {"items": [
        {"name": "Xbox Wireless Controller", "itemId": 123456, "salePrice": 25.00},
        {"name": "Nintendo Switch Console", "itemId": 987654, "salePrice": 150.00},
    ]}
    ordinary = provider._candidates_from_payload(payload, ProviderScanRequest(source_key="walmart", query="console", sort="price"))
    assert all("walmartBestsellerSortRequested" not in c.variant_attributes for c in ordinary)
    sampled = provider._candidates_from_payload(payload, ProviderScanRequest(source_key="walmart", query="console", sort="bestseller"))
    assert [c.variant_attributes.get("walmartBestsellerSearchPosition") for c in sampled] == ["1", "2"]
    assert all(c.variant_attributes.get("walmartBestsellerSortRequested") == "yes" for c in sampled)
