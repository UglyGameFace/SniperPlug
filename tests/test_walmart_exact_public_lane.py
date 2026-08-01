from pathlib import Path

import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.services import public_deal_quality as quality
from sniperplug.services.walmart_exact_public_lane import (
    WALMART_CASH_FIELD,
    normalize_exact_verified_walmart_card,
)


def exact_card(*, item_id: str = "123456", lane: str = quality.LANE_CART_PROMO) -> DealCard:
    embed = discord.Embed(
        title="80% exact Walmart markdown",
        description="review/scout-only card; not verified markdown",
    )
    embed.add_field(
        name="Private review",
        value="Staff review required; not deal proof.",
        inline=False,
    )
    card = DealCard(
        embed=embed,
        url=f"https://www.walmart.com/ip/{item_id}",
        label="review-only Walmart lead",
        score=250,
        discount=80.0,
        deal_lane=lane,
        api_current_price=20.0,
        api_reference_price=100.0,
        api_discount_percent=80.0,
        api_reference_path="priceInfo.wasPrice",
        direct_product_url=f"https://www.walmart.com/ip/{item_id}",
        variant_attributes={
            "dealLane": lane,
            "exactDetailPriceProof": "yes",
            "exactDetailItemId": item_id,
            "exactDetailOfferIdentityStatus": "verified",
            "referencePriceTrusted": "yes",
            "trustedReferencePrice": "100.00",
            "trustedReferenceSource": "priceInfo.wasPrice",
            "apiPromotionText": "Extra offer available in cart",
            "cartPromo": "yes",
        },
    )
    card.selected_offer_id = "offer-123"
    card.current_price = 20.0
    card.seller_name = "Walmart"
    card.fulfillment_type = "shipping"
    return card


def test_exact_markdown_is_not_demoted_by_auxiliary_cart_promo() -> None:
    card = exact_card()

    assert normalize_exact_verified_walmart_card(card, min_discount=50) is True
    assert card.deal_lane == quality.LANE_VERIFIED_MARKDOWN
    assert card.variant_attributes["publicMarkdownIndependentOfPromo"] == "yes"
    assert card.variant_attributes["auxiliaryPromoPresent"] == "yes"
    assert quality.has_verified_api_threshold_discount(card, min_discount=50) is True


def test_exact_markdown_clears_stale_review_only_markers() -> None:
    card = exact_card()

    assert normalize_exact_verified_walmart_card(card, min_discount=50) is True
    assert "review-only" not in card.label.lower()
    assert "review/scout-only" not in str(card.embed.description or "").lower()
    assert not any("staff review" in str(field.value or "").lower() for field in card.embed.fields)
    assert quality.is_review_or_watchlist(card) is False


def test_exact_card_can_cross_repeated_public_gates_without_self_poisoning() -> None:
    card = exact_card()

    assert normalize_exact_verified_walmart_card(card, min_discount=50) is True
    assert quality.prepare_public_deal_candidate(card, min_discount=50) is True

    # The legacy explanation mentions rejected scout signals. The exact-card
    # refresher removes that earlier field before the next quality boundary.
    assert normalize_exact_verified_walmart_card(card, min_discount=50) is True
    assert quality.is_review_or_watchlist(card) is False
    assert quality.prepare_public_deal_candidate(card, min_discount=50) is True


def test_strict_walmart_cash_amount_is_rendered_on_verified_card() -> None:
    card = exact_card()
    card.variant_attributes.update(
        {
            "walmartCashApiProof": "yes",
            "walmartCashAmount": "5.00",
            "walmartCashSavings": "5.00",
            "walmartCashProofPath": "promotions[0].walmartCash.amount",
            "walmartCashProofLabel": "Walmart Cash offer",
        }
    )

    assert normalize_exact_verified_walmart_card(card, min_discount=50) is True
    cash_fields = [field for field in card.embed.fields if str(field.name or "") == WALMART_CASH_FIELD]
    assert len(cash_fields) == 1
    assert "$5.00 Walmart Cash" in str(cash_fields[0].value)
    assert "not included in the markdown percentage" in str(cash_fields[0].value)
    assert card.variant_attributes["walmartCashDisplayed"] == "yes"

    # Idempotent refresh updates the same field instead of duplicating it.
    assert normalize_exact_verified_walmart_card(card, min_discount=50) is True
    assert len([field for field in card.embed.fields if str(field.name or "") == WALMART_CASH_FIELD]) == 1


def test_unproven_walmart_cash_is_not_rendered() -> None:
    card = exact_card()
    card.variant_attributes["walmartCashAmount"] = "5.00"

    assert normalize_exact_verified_walmart_card(card, min_discount=50) is True
    assert not any(str(field.name or "") == WALMART_CASH_FIELD for field in card.embed.fields)


def test_promo_only_card_stays_private_without_exact_trusted_markdown() -> None:
    card = exact_card()
    card.variant_attributes["referencePriceTrusted"] = "no"
    card.api_reference_price = None

    assert normalize_exact_verified_walmart_card(card, min_discount=50) is False
    assert card.deal_lane == quality.LANE_CART_PROMO
    assert quality.has_verified_api_threshold_discount(card, min_discount=50) is False


def test_item_identity_mismatch_cannot_be_promoted() -> None:
    card = exact_card(item_id="123456")
    card.variant_attributes["exactDetailItemId"] = "999999"

    assert normalize_exact_verified_walmart_card(card, min_discount=50) is False
    assert card.deal_lane == quality.LANE_CART_PROMO


def test_confidence_ranking_runs_only_after_public_proof_gate() -> None:
    source = Path("sniperplug/cogs/native_auto_scan_runner.py").read_text(encoding="utf-8")
    proof_index = source.index("proof_ready_cards = legacy.select_public_deal_candidates")
    confidence_index = source.index("confidence_selection = legacy.select_confident_public_cards")
    fresh_index = source.index("fresh_selection = await legacy.select_fresh_deal_cards")
    final_post_index = source.index("public_result = await legacy.maybe_post_public_deal_cards")

    assert proof_index < confidence_index < fresh_index < final_post_index
    assert "legacy.select_confident_public_cards(\n            proof_ready_cards" in source
    assert "normalize_exact_verified_walmart_cards(\n            public_candidates" in source
    assert "normalize_exact_verified_walmart_cards(\n            shown_cards" in source
    assert "for card in legacy.select_public_deal_candidates" not in source
    assert "Confidence-ready now means proof-ready too" in source
