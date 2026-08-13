from pathlib import Path

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.deal_finder_engine import (
    apply_direct_product_fallback,
    build_direct_product_match_cards,
    private_product_match_score,
)
from sniperplug.services.walmart_review_candidates import ReviewCandidateResult


ROOT = Path(__file__).resolve().parents[1]


def walmart_candidate(
    *,
    title: str,
    product_id: str,
    current_price: float | None = 14.88,
    typical_price: float | None = None,
) -> SourceCandidate:
    return SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title=title,
        product_url=f"https://www.walmart.com/ip/{product_id}",
        current_price=current_price,
        typical_price=typical_price,
        product_id=product_id,
        selected_offer_id=product_id,
        seller_name="Walmart",
        variant_attributes={
            "isMarketPlaceItem": "false",
            "currentPriceSource": "salePrice",
            "referencePriceTrusted": "yes" if typical_price else "no",
            **({"trustedReferencePrice": f"{typical_price:.2f}", "trustedReferenceSource": "wasPrice"} if typical_price else {}),
        },
    )


def rendered_text(card) -> str:
    return str(card.embed.to_dict())


def test_private_product_match_supports_normal_two_word_search_without_fake_discount():
    candidate = walmart_candidate(title="Magnetic Phone Mount for Car Dashboard", product_id="1001")

    cards = build_direct_product_match_cards([candidate], query="phone mount")

    assert len(cards) == 1
    card = cards[0]
    assert card.product_search_match is True
    assert card.product_match_score == 1.0
    assert card.should_alert is False
    assert card.manual_share_allowed is False
    assert card.deal_lane == "product_search_match"
    text = rendered_text(card)
    assert "Search match — not deal proof" in text
    assert "Current product price: **$14.88**" in text
    assert "Was/reference: **not returned or not trusted by Walmart API**" in text


def test_private_product_match_supports_single_word_searches_like_monitor():
    candidate = walmart_candidate(title="27 inch Gaming Monitor 165Hz", product_id="1002", current_price=129.0)

    assert private_product_match_score("monitor", candidate.title, product_id=candidate.product_id) == 1.0
    cards = build_direct_product_match_cards([candidate], query="monitor")
    assert [card.label for card in cards]


def test_private_product_match_rejects_weak_partial_two_word_match():
    candidate = walmart_candidate(title="Protective Phone Case with Stand", product_id="1003")

    assert private_product_match_score("phone mount", candidate.title, product_id=candidate.product_id) == 0.5
    assert build_direct_product_match_cards([candidate], query="phone mount") == []


def test_private_product_match_requires_a_payable_current_price():
    candidate = walmart_candidate(title="Magnetic Phone Mount", product_id="1004", current_price=None)

    assert build_direct_product_match_cards([candidate], query="phone mount") == []


def test_product_fallback_only_runs_when_verified_and_review_lanes_are_empty():
    candidate = walmart_candidate(title="Magnetic Phone Mount for Car", product_id="1005")
    empty_review = ReviewCandidateResult(
        cards=[],
        missing_reference_count=1,
        no_value_signal_count=1,
        exact_match_count=1,
        ignored_reference_sources=(("context only", 2),),
        no_value_reasons=(("search match only", 1),),
    )

    fallback = apply_direct_product_fallback(
        verified_cards=[],
        review=empty_review,
        candidates=[candidate],
        query="phone mount",
    )
    assert len(fallback.cards) == 1
    assert fallback.cards[0].product_search_match is True
    assert fallback.missing_reference_count == 1
    assert fallback.no_value_signal_count == 1
    assert fallback.ignored_reference_sources == (("context only", 2),)
    assert fallback.no_value_reasons == (("search match only", 1),)

    existing_review = ReviewCandidateResult(cards=fallback.cards)
    same_review = apply_direct_product_fallback(
        verified_cards=[],
        review=existing_review,
        candidates=[candidate],
        query="phone mount",
    )
    assert same_review is existing_review

    blocked_by_verified = apply_direct_product_fallback(
        verified_cards=[fallback.cards[0]],
        review=empty_review,
        candidates=[candidate],
        query="phone mount",
    )
    assert blocked_by_verified is empty_review


def test_unified_deal_finder_wires_private_product_fallback_after_review_and_scout():
    source = (ROOT / "sniperplug/services/deal_finder_engine.py").read_text(encoding="utf-8")
    assert "review = merge_scout_review_cards(review, scout_cards)" in source
    assert "review = apply_direct_product_fallback(" in source
    assert source.index("review = merge_scout_review_cards(review, scout_cards)") < source.index("review = apply_direct_product_fallback(")
    assert "card.should_alert = False" in source
    assert 'card.deal_lane = "product_search_match"' in source
