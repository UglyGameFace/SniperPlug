from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sniperplug.services.product_identity_match import (
    authorized_sold_comp_observation,
    canonical_gtin,
    match_exact_product_identity,
)


def _match(**overrides):
    data = {
        "retailer_gtin": "036000291452",
        "sold_gtin": "0036000291452",
        "retailer_condition": "Brand New",
        "sold_condition": "new",
    }
    data.update(overrides)
    return match_exact_product_identity(**data)


def test_upc_and_ean_13_canonicalize_to_identical_gtin14():
    assert canonical_gtin("036000291452") == canonical_gtin("0036000291452")
    assert _match().matched


def test_invalid_identifier_and_mismatched_barcode_are_rejected():
    for bad in ("", "0", "036000291453", "123456", "abc123", "03600029145x"):
        assert not _match(sold_gtin=bad).matched
    assert not _match(sold_gtin="4006381333931").matched


def test_conditions_and_variant_properties_require_exact_comparability():
    assert not _match(sold_condition="Refurbished").matched
    assert not _match(sold_condition="Open Box").matched
    assert not _match(sold_condition="").matched
    assert not _match(retailer_pack="2 pack", sold_pack="single").matched
    assert not _match(retailer_pack="2 pack").matched
    assert _match(retailer_pack="2 Pack", sold_pack="2 pack").matched
    assert not _match(retailer_model="HAC-001", sold_model="HAC-002").matched
    assert not _match(retailer_variant="blue", sold_variant="red").matched


def test_authorized_sold_provenance_requires_fresh_aware_timestamp():
    now = datetime.now(timezone.utc)
    good = {
        "ebaySoldCompSource": "ebay_marketplace_insights",
        "ebaySoldCompFetchedAt": (now - timedelta(minutes=10)).isoformat(),
    }
    assert authorized_sold_comp_observation(good, now=now)
    assert not authorized_sold_comp_observation({**good, "ebaySoldCompSource": "ebay_active_listings"}, now=now)
    assert not authorized_sold_comp_observation({**good, "ebaySoldCompFetchedAt": (now - timedelta(days=7)).isoformat()}, now=now)
    assert not authorized_sold_comp_observation({**good, "ebaySoldCompFetchedAt": "2026-11-12T15:00:00"}, now=now)
    assert not authorized_sold_comp_observation({**good, "ebaySoldCompFetchedAt": "not-a-date"}, now=now)
