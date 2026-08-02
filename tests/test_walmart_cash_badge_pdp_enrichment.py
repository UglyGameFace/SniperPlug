from __future__ import annotations

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_cash_offers import build_walmart_cash_summary_embed
from sniperplug.services.walmart_cash_pipeline import (
    detect_confirmed_walmart_cash_amount,
    detect_walmart_cash_badge,
)


def candidate(**kwargs) -> SourceCandidate:
    base = {
        "source_key": "walmart",
        "retailer": "Walmart",
        "title": "Glad ForceFlex Trash Bags",
        "product_url": "https://www.walmart.com/ip/123",
        "direct_product_url": "https://www.walmart.com/ip/123",
        "current_price": 5.92,
        "product_id": "123",
        "sku": "123",
    }
    base.update(kwargs)
    return SourceCandidate(**base)


def test_search_row_badge_creates_private_candidate_state():
    row = candidate(variant_attributes={"badge": "Walmart Cash available"})
    badge = detect_walmart_cash_badge(row)
    assert badge is not None
    assert badge.proof_path == "variant_attributes.badge"


def test_badge_without_amount_does_not_confirm_offer():
    item = {
        "itemId": "123",
        "name": "Glad ForceFlex Trash Bags",
        "badges": [{"text": "Walmart Cash available"}],
    }
    assert not detect_confirmed_walmart_cash_amount(item, current_price=5.92)


def test_official_feed_payload_with_exact_amount_can_confirm_offer():
    item = {
        "itemId": "123",
        "name": "Glad ForceFlex Trash Bags",
        "promo": {"headline": "Earn $5 Walmart Cash"},
    }
    assert detect_confirmed_walmart_cash_amount(item, current_price=5.92)


def test_nested_detail_promo_object_with_amount_confirms_offer():
    item = {
        "itemId": "123",
        "offers": [
            {
                "type": "Walmart Cash",
                "amount": 3.0,
                "description": "Walmart Cash reward",
            }
        ],
    }
    assert detect_confirmed_walmart_cash_amount(item, current_price=9.99)


def test_user_query_or_plain_title_does_not_prove_badge():
    row = candidate(title="Walmart Cash detergent")
    assert detect_walmart_cash_badge(row) is None


def test_supported_feed_summary_reports_badge_hints_without_pdp_wall():
    embed = build_walmart_cash_summary_embed(
        "detergent",
        ("supported-feed-route",),
        checked=2,
        found=0,
        warnings=(),
        detail_checked=2,
        promo_counts={
            "cash_badge_seen": 2,
            "detail_rows_attempted": 2,
            "detail_rows_checked": 2,
            "confirmed_walmart_cash_amount_rows": 0,
            "badge_rows_without_amount": 2,
            "clearance": 1,
        },
    )
    rendered = str(embed.to_dict())

    assert "Cash badges seen" in rendered
    assert "badge hints without an amount" in rendered
    assert "No API-proven Walmart Cash in this scan" in rendered
    assert "PDP fallback" not in rendered
    assert "Clearance signal" not in rendered
    assert "Search routes actually checked" not in rendered
