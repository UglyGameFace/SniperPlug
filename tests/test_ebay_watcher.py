from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import aiosqlite

from sniperplug.ebay_watcher.client import EbayBrowseClient, EbayJSONResponse
from sniperplug.ebay_watcher.config import EbayWatcherSettings
from sniperplug.ebay_watcher.models import (
    ComparableReference,
    EbayListing,
    EbayWatchRule,
    ListingHistory,
)
from sniperplug.ebay_watcher.parser import (
    build_listing_fingerprint,
    comparable_references,
    normalize_condition,
    parse_ebay_item,
)
from sniperplug.ebay_watcher.service import qualify_ebay_deal
from sniperplug.ebay_watcher.storage import (
    LISTING_TABLE,
    ensure_ebay_watcher_tables,
    list_watch_rules,
    seed_default_watch_rules,
    store_listing_observation,
)
from sniperplug.services.ebay_public_posts import is_verified_ebay_public_card


class FakeDatabase:
    def __init__(self, conn):
        self.conn = conn

    def require_conn(self):
        return self.conn


def _listing(
    *,
    item_id: str = "v1|123|0",
    seller: str = "seller-1",
    delivered: float = 299.0,
    condition: str = "new",
    fingerprint: str = "ebay-product:exact",
    exact: bool = True,
    title: str = "NVIDIA GeForce RTX 5090 Founders Edition",
) -> EbayListing:
    return EbayListing(
        item_id=item_id,
        legacy_item_id=item_id.split("|")[1] if "|" in item_id else item_id,
        title=title,
        product_url=f"https://www.ebay.com/itm/{item_id}",
        image_url="",
        item_price=delivered,
        shipping_price=0.0,
        delivered_price=delivered,
        currency="USD",
        shipping_known=True,
        condition_id="1000",
        condition_name="New",
        condition_bucket=condition,
        seller_id=seller,
        seller_feedback_percentage=99.8,
        seller_feedback_score=500,
        buying_options=("FIXED_PRICE",),
        item_creation_date="2026-08-02T00:00:00Z",
        item_end_date="2026-09-02T00:00:00Z",
        estimated_availability_status="IN_STOCK",
        gtin="0012345678905",
        epid="",
        brand="NVIDIA",
        model="RTX 5090",
        mpn="900-1G144-2530-000",
        aspects={"Memory Size": "32 GB", "Color": "Black"},
        fingerprint=fingerprint,
        exact_identity=exact,
    )


def _rule(*, sought: bool = False, min_reference: float = 200.0) -> EbayWatchRule:
    return EbayWatchRule(
        rule_id="rule-1",
        label="RTX 5090",
        query="RTX 5090",
        sought_after=sought,
        min_discount_percent=69,
        min_reference_price=min_reference,
        allowed_conditions=("new", "open_box", "used_good"),
        min_seller_feedback_percentage=97.0,
        min_seller_feedback_score=10,
    )


def _history(
    *,
    baseline: float | None = 1000.0,
    observations: int = 2,
    first_seen: str = "2026-08-02T20:00:00+00:00",
) -> ListingHistory:
    return ListingHistory(
        item_id="v1|123|0",
        first_seen_at=first_seen,
        previous_delivered_price=baseline,
        prior_baseline_price=baseline,
        prior_baseline_observations=observations,
        prior_baseline_first_seen_at=first_seen,
        last_alert_price=None,
        last_event_key="",
        is_new=False,
    )



def test_browse_client_uses_fixed_price_delivery_country_and_new_listing_sort() -> None:
    class CapturingClient(EbayBrowseClient):
        def __init__(self, settings):
            super().__init__(settings)
            self.captured = None

        async def _authorized_json(self, method, url, *, params=None):
            self.captured = (method, url, dict(params or {}))
            return EbayJSONResponse(url=url, status=200, payload={"itemSummaries": []})

    async def run() -> None:
        settings = EbayWatcherSettings(
            require_remote_database=False,
            client_id="client",
            client_secret="secret",
            buyer_country="US",
            buyer_postal_code="06604",
        )
        client = CapturingClient(settings)
        await client.search(_rule())
        method, url, params = client.captured
        assert method == "GET"
        assert url.endswith("/buy/browse/v1/item_summary/search")
        assert params["sort"] == "newlyListed"
        assert "buyingOptions:{FIXED_PRICE}" in params["filter"]
        assert "deliveryCountry:US" in params["filter"]
        headers = client._browse_headers("token")
        assert headers["X-EBAY-C-MARKETPLACE-ID"] == settings.marketplace_id
        assert headers["X-EBAY-C-ENDUSERCTX"] == (
            "contextualLocation=country%3DUS%2Czip%3D06604"
        )

    asyncio.run(run())

def test_parser_uses_delivered_price_and_structured_condition() -> None:
    payload = {
        "itemId": "v1|123|0",
        "legacyItemId": "123",
        "title": "RTX 5090 Founders Edition",
        "itemWebUrl": "https://www.ebay.com/itm/123",
        "price": {"value": "900.00", "currency": "USD"},
        "shippingOptions": [
            {"shippingCost": {"value": "25.00", "currency": "USD"}}
        ],
        "conditionId": "1500",
        "condition": "New other (see details)",
        "seller": {
            "username": "trusted-seller",
            "feedbackPercentage": "99.9",
            "feedbackScore": 1000,
        },
        "buyingOptions": ["FIXED_PRICE"],
        "localizedAspects": [
            {"name": "Brand", "value": "NVIDIA"},
            {"name": "MPN", "value": "900-1G144-2530-000"},
            {"name": "Memory Size", "value": "32 GB"},
        ],
    }
    listing = parse_ebay_item(payload)
    assert listing.delivered_price == 925.0
    assert listing.shipping_known is True
    assert listing.condition_bucket == "open_box"
    assert listing.exact_identity is True
    assert listing.fingerprint.startswith("ebay-product:")


def test_condition_fails_closed_for_generic_or_broken_items() -> None:
    assert normalize_condition("3000", "Used") == "used"
    assert normalize_condition("7000", "For parts or not working") == "for_parts"
    assert normalize_condition("", "") == "unknown"


def test_variant_aspects_change_the_exact_product_fingerprint() -> None:
    first, exact_first = build_listing_fingerprint(
        item_id="a",
        gtin="0012345678905",
        aspects={"Storage Capacity": "1 TB", "Color": "Black"},
    )
    second, exact_second = build_listing_fingerprint(
        item_id="b",
        gtin="0012345678905",
        aspects={"Storage Capacity": "512 GB", "Color": "Black"},
    )
    assert exact_first is True and exact_second is True
    assert first != second


def test_comparable_reference_requires_distinct_other_sellers() -> None:
    candidate = _listing(item_id="v1|1|0", seller="candidate", delivered=200.0)
    duplicated_vendor = [
        _listing(item_id=f"v1|dup-{index}|0", seller="same-seller", delivered=1000.0)
        for index in range(10)
    ]
    assert comparable_references(
        [candidate, *duplicated_vendor],
        minimum_comparables=5,
    ) == {}

    distinct = [
        _listing(item_id=f"v1|{index}|0", seller=f"seller-{index}", delivered=1000.0)
        for index in range(2, 7)
    ]
    references = comparable_references(
        [candidate, *distinct],
        minimum_comparables=5,
    )
    assert references[candidate.item_id].price == 1000.0
    assert references[candidate.item_id].sample_size == 5


def test_69_percent_big_ticket_history_drop_qualifies() -> None:
    settings = EbayWatcherSettings(
        require_remote_database=False,
        minimum_baseline_age_seconds=240,
    )
    decision = qualify_ebay_deal(
        listing=_listing(delivered=299.0),
        rule=_rule(),
        history=_history(),
        comparable=None,
        settings=settings,
        now=datetime(2026, 8, 2, 21, 0, tzinfo=timezone.utc),
    )
    assert decision.should_publish is True
    assert decision.event_type == "price_drop"
    assert decision.discount_percent == 70.1
    assert decision.reference_source.endswith("baseline")


def test_sought_after_item_can_use_lower_reference_floor() -> None:
    settings = EbayWatcherSettings(
        require_remote_database=False,
        minimum_baseline_age_seconds=0,
        big_ticket_min_reference_price=200.0,
    )
    decision = qualify_ebay_deal(
        listing=_listing(delivered=24.0),
        rule=_rule(sought=True, min_reference=75.0),
        history=_history(
            baseline=80.0,
            first_seen="2026-08-02T20:59:00+00:00",
        ),
        comparable=None,
        settings=settings,
        now=datetime(2026, 8, 2, 21, 0, tzinfo=timezone.utc),
    )
    assert decision.should_publish is True
    assert decision.discount_percent == 70.0


def test_untrusted_marketing_price_and_unclear_condition_do_not_qualify() -> None:
    settings = EbayWatcherSettings(require_remote_database=False)
    listing = replace(
        _listing(delivered=99.0),
        marketing_original_price=999.0,
        condition_bucket="unknown",
    )
    decision = qualify_ebay_deal(
        listing=listing,
        rule=_rule(),
        history=_history(baseline=None, observations=0, first_seen=""),
        comparable=None,
        settings=settings,
    )
    assert decision.should_publish is False


def test_public_gate_preserves_custom_rule_thresholds() -> None:
    attrs = {
        "ebayStructuredPriceProof": "yes",
        "ebayIndependentConfirmation": "yes",
        "ebayItemId": "v1|123|0",
        "ebayDeliveredPrice": "24.00",
        "ebayConditionBucket": "used_acceptable",
        "ebaySellerFeedbackPercentage": "98.00",
        "ebaySellerFeedbackScore": "50",
        "ebayRuleMinimumSellerFeedbackPercentage": "97.00",
        "ebayRuleMinimumSellerFeedbackScore": "10",
        "ebayRuleMinimumDiscount": "69",
        "ebayRuleMinimumReferencePrice": "75.00",
        "ebaySoughtAfterRule": "yes",
        "trustedReferencePrice": "80.00",
        "trustedReferenceSource": "sniperplug.ebay.exact_listing_history.baseline",
    }
    card = SimpleNamespace(
        retailer="eBay",
        direct_product_url="https://www.ebay.com/itm/123",
        url="https://www.ebay.com/itm/123",
        variant_attributes=attrs,
        selected_offer_id="v1|123|0",
        seller_name="seller",
        api_condition="used_acceptable",
        api_current_price=24.0,
        current_price=24.0,
        api_reference_price=80.0,
        typical_price=80.0,
        api_reference_path="sniperplug.ebay.exact_listing_history.baseline",
    )
    assert is_verified_ebay_public_card(card, min_discount=50) is True


def test_storage_retains_stable_exact_baseline_and_seeds_both_lanes() -> None:
    async def run() -> None:
        conn = await aiosqlite.connect(":memory:")
        conn.row_factory = aiosqlite.Row
        db = FakeDatabase(conn)
        settings = EbayWatcherSettings(
            require_remote_database=False,
            big_ticket_queries=("gaming laptop",),
            sought_after_queries=("Nintendo Switch 2",),
        )
        await ensure_ebay_watcher_tables(db)
        assert await seed_default_watch_rules(db, settings) == 2
        rules = await list_watch_rules(db)
        assert {rule.sought_after for rule in rules} == {False, True}

        now = datetime(2026, 8, 2, 20, 0, tzinfo=timezone.utc)
        rule = _rule()
        listing = _listing(delivered=1000.0)
        first = await store_listing_observation(
            db,
            listing=listing,
            rule=rule,
            next_check_delay=timedelta(minutes=5),
            now=now,
        )
        assert first.is_new is True

        second = await store_listing_observation(
            db,
            listing=listing,
            rule=rule,
            next_check_delay=timedelta(minutes=5),
            now=now + timedelta(minutes=5),
        )
        assert second.prior_baseline_price == 1000.0
        assert second.prior_baseline_observations == 1

        drop = await store_listing_observation(
            db,
            listing=replace(
                listing,
                item_price=299.0,
                delivered_price=299.0,
            ),
            rule=rule,
            next_check_delay=timedelta(minutes=5),
            now=now + timedelta(minutes=10),
        )
        assert drop.prior_baseline_price == 1000.0
        assert drop.prior_baseline_observations == 2

        cursor = await conn.execute(
            f"SELECT baseline_price_cents, baseline_observation_count "
            f"FROM {LISTING_TABLE} WHERE item_id = ?",
            (listing.item_id,),
        )
        row = await cursor.fetchone()
        assert row["baseline_price_cents"] == 100000
        assert row["baseline_observation_count"] == 2
        await conn.close()

    asyncio.run(run())
