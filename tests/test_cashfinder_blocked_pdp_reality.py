from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_cash_offers import (
    MIN_USEFUL_WALMART_CASH_AMOUNT,
    build_walmart_cash_summary_embed,
    find_walmart_cash_offer,
)


def test_cashfinder_blocked_pdp_summary_is_honest():
    embed = build_walmart_cash_summary_embed(
        "deodorant",
        ("deodorant", "deodorant walmart cash"),
        checked=15,
        found=0,
        warnings=(
            "Exact Walmart PDP checked at https://www.walmart.com/ip/10898692; no Walmart Cash wording was exposed. PDP diagnostics: possible_block=yes; title=Robot or Human?; html_chars=15190; scripts=6",
        ),
        detail_checked=12,
        promo_counts={
            "detail_rows_attempted": 12,
            "detail_rows_checked": 12,
            "pdp_fallback_attempted": 6,
            "pdp_fallback_checked": 6,
            "pdp_cash_wording_seen": 0,
            "confirmed_walmart_cash_amount_rows": 0,
            "clearance": 12,
        },
    )
    rendered = str(embed.to_dict())

    assert "Walmart blocked the public PDP fallback" in rendered
    assert "Robot/Human" in rendered
    assert "app/member-only Walmart Cash cannot be proven" in rendered
    assert "Cash Finder source blocked" in rendered


def test_cashfinder_hides_small_cash_amount_below_floor():
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
            "walmartCashProofPath": "walmart_pdp.text[0]",
            "walmartCashProofText": "Earn $0.75 Walmart Cash",
        },
    )
    deal = candidate.to_normalized_deal()

    assert MIN_USEFUL_WALMART_CASH_AMOUNT == 2.00
    assert find_walmart_cash_offer(candidate, deal) is None


def test_cashfinder_summary_reports_low_value_hidden():
    embed = build_walmart_cash_summary_embed(
        "trash bags",
        ("trash bags", "trash bags walmart cash"),
        checked=6,
        found=0,
        warnings=(),
        detail_checked=6,
        promo_counts={
            "detail_rows_attempted": 6,
            "detail_rows_checked": 6,
            "confirmed_walmart_cash_amount_rows": 1,
        },
    )
    rendered = str(embed.to_dict())

    assert "Low-value Cash hidden" in rendered
    assert "$2.00" in rendered
    assert "below" in rendered
