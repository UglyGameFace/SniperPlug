from __future__ import annotations

from sniperplug.services.active_deal_cache import (
    ACTIVE_CACHE_QUERY_LIMIT,
    classify_scan_freshness,
    row_from_mapping,
)


class Card:
    retailer = "walmart"
    selected_offer_id = None
    sku = "sku-20"
    upc = None
    url = "https://example.com/item/20"
    label = "Example item"
    current_price = 20.0


def test_snapshot_query_limit_is_large_enough_for_freshness_dedupe() -> None:
    assert ACTIVE_CACHE_QUERY_LIMIT >= 100


def test_malformed_cached_score_does_not_break_cache_reads() -> None:
    row = row_from_mapping(
        {
            "active_key": "walmart:sku-20",
            "retailer": "walmart",
            "title": "Example item",
            "url": "https://example.com/item/20",
            "current_price": 20.0,
            "discount": 50.0,
            "score": "not-a-number",
            "source_label": "test",
            "status": "active",
            "first_seen_at": "2026-07-30T00:00:00+00:00",
            "last_seen_at": "2026-07-30T00:00:00+00:00",
        }
    )
    assert row.score is None


def test_cached_repeat_is_not_misclassified_as_new() -> None:
    cached = row_from_mapping(
        {
            "active_key": "walmart:sku-20",
            "retailer": "walmart",
            "title": "Example item",
            "url": "https://example.com/item/20",
            "current_price": 20.0,
            "discount": 50.0,
            "score": 95,
            "source_label": "test",
            "status": "active",
            "first_seen_at": "2026-07-30T00:00:00+00:00",
            "last_seen_at": "2026-07-30T00:00:00+00:00",
        }
    )
    freshness = classify_scan_freshness([Card()], {cached.active_key: cached})
    assert freshness.new_count == 0
    assert freshness.repeat_count == 1
