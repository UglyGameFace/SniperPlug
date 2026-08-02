from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_cash_offers import (
    MIN_USEFUL_WALMART_CASH_AMOUNT,
    build_walmart_cash_summary_embed,
    find_walmart_cash_offer,
)


def test_cashfinder_hides_legacy_blocked_pdp_diagnostics_from_normal_output():
    embed = build_walmart_cash_summary_embed(
        "deodorant",
        ("deodorant",),
        checked=15,
        found=0,
        warnings=(
            "Exact Walmart PDP checked at https://www.walmart.com/ip/10898692; Robot or Human; html_chars=15190; scripts=6",
        ),
        detail_checked=12,
        promo_counts={
            "detail_rows_attempted": 12,
            "detail_rows_checked": 12,
            "confirmed_walmart_cash_amount_rows": 0,
            "clearance": 12,
        },
    )
    rendered = str(embed.to_dict()).lower()

    assert "official walmart api only" in rendered
    assert "no api-proven walmart cash in this scan" in rendered
    assert "robot or human" not in rendered
    assert "html_chars" not in rendered
    assert "walmart.com/ip/10898692" not in rendered
    assert "blocked the public pdp" not in rendered


def test_cashfinder_accepts_small_positive_strict_api_cash_amount():
    candidate = SourceCandidate(
        source_key="walmart",
        retailer="Walmart",
        title="Trash bags",
        product_url="https://www.walmart.com/ip/123",
        current_price=9.99,
        variant_attributes={
            "walmartCashApiProof": "yes",
            "walmartCashProofMode": "strict_api_field_amount",
            "walmartCashAmount": "0.75",
            "walmartCashProofPath": "items[0].manufacturerOffer.walmartCashAmount",
            "walmartCashProofText": "Earn $0.75 Walmart Cash",
        },
    )
    deal = candidate.to_normalized_deal()

    assert MIN_USEFUL_WALMART_CASH_AMOUNT == 0.01
    offer = find_walmart_cash_offer(candidate, deal)
    assert offer is not None
    assert offer.amount == 0.75


def test_cashfinder_no_longer_invents_low_value_hidden_lane():
    embed = build_walmart_cash_summary_embed(
        "trash bags",
        ("trash bags",),
        checked=6,
        found=1,
        warnings=(),
        detail_checked=6,
        promo_counts={
            "detail_rows_attempted": 6,
            "detail_rows_checked": 6,
            "confirmed_walmart_cash_amount_rows": 1,
        },
    )
    rendered = str(embed.to_dict())

    assert "Confirmed Cash offers: **1**" in rendered
    assert "Low-value Cash hidden" not in rendered
    assert "$2.00" not in rendered
