from sniperplug.cogs.deal_scanner import HuntPreset
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.verified_discount_hunt import HUNT_PRESETS
from sniperplug.services.walmart_review_candidates import build_review_candidate_cards, review_match_query


def test_deal_week_routes_include_high_intent_targets():
    preset = HUNT_PRESETS["deal_week"]
    joined = " | ".join(preset.queries).lower()

    for expected in (
        "dolce gabbana the one",
        "gaming monitor rollback",
        "motor oil rollback",
        "gold chain clearance",
        "hyperx headset rollback",
        "ssd rollback",
        "hart tools clearance",
    ):
        assert expected in joined


def test_review_match_query_uses_route_provenance_when_no_manual_query():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Dell 24 inch Gaming Monitor",
        product_url="https://www.walmart.com/ip/123",
        current_price=79.95,
        product_id="123",
        sku="123",
        variant_attributes={"finderSourceQuery": "gaming monitor rollback"},
    )

    assert review_match_query(candidate, None) == "gaming monitor rollback"


def test_route_aware_review_candidate_rescues_exact_product_lead():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Dell 24 inch Gaming Monitor",
        product_url="https://www.walmart.com/ip/123",
        current_price=79.95,
        product_id="123",
        sku="123",
        stock_status="Available",
        can_add_to_cart=True,
        variant_attributes={
            "finderSourceQuery": "gaming monitor rollback",
            "availableOnline": "yes",
            "seller": "Walmart",
            "brand": "Dell",
        },
        signals=["rollback"],
    )

    result = build_review_candidate_cards([candidate], limit=5)

    assert result.exact_match_count == 1
    assert result.cards
    assert "Finder query" in str(result.cards[0].embed.to_dict())


def test_autoscan_memory_seeds_are_not_added_unless_price_memory_enabled():
    source = open("sniperplug/services/verified_discount_hunt.py", encoding="utf-8").read()

    assert "if use_price_memory and db is not None and guild_id is not None:" in source
    assert "preset_queries = tuple(dedupe_strings([*preset.queries, *memory_seeds]))" in source
