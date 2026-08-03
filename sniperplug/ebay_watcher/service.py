from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import logging
from typing import Any

from sniperplug.ebay_watcher.client import EbayBrowseClient
from sniperplug.ebay_watcher.config import EbayWatcherSettings
from sniperplug.ebay_watcher.models import (
    ComparableReference,
    EbayCycleResult,
    EbayDealDecision,
    EbayListing,
    EbayWatchRule,
    ListingHistory,
)
from sniperplug.ebay_watcher.parser import (
    comparable_references,
    parse_ebay_item,
    parse_ebay_items_response,
    parse_ebay_search_response,
)
from sniperplug.ebay_watcher.storage import (
    claim_due_tracked_listings,
    claim_due_watch_rules,
    complete_watch_rule,
    ebay_watcher_counts,
    ensure_ebay_watcher_tables,
    fail_tracked_listings,
    fail_watch_rule,
    get_watch_rule,
    mark_listing_event,
    seed_default_watch_rules,
    set_health_value,
    store_listing_observation,
)
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.verified_retailer_events import (
    ensure_verified_retailer_event_table,
    publish_verified_retailer_event,
)


log = logging.getLogger("sniperplug.ebay_watcher")


class EbayWatcherService:
    def __init__(self, db: Any, settings: EbayWatcherSettings):
        self.db = db
        self.settings = settings
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await ensure_ebay_watcher_tables(self.db)
        await ensure_verified_retailer_event_table(self.db)
        inserted = await seed_default_watch_rules(self.db, self.settings)
        await set_health_value(self.db, "service_status", "starting")
        await set_health_value(
            self.db,
            "policy",
            (
                f"discount_floor={self.settings.default_min_discount_percent},"
                f"big_ticket_floor={self.settings.big_ticket_min_reference_price:.2f},"
                f"sought_floor={self.settings.sought_after_min_reference_price:.2f},"
                f"minimum_comparables={self.settings.minimum_comparables},"
                f"seeded_rules={inserted}"
            ),
        )
        self._initialized = True

    async def run_forever(self) -> None:
        await self.initialize()
        async with EbayBrowseClient(self.settings) as client:
            while True:
                started = datetime.now(timezone.utc)
                try:
                    result = await self.run_cycle(client)
                    await self._record_success(result, client.calls_made)
                except asyncio.CancelledError:
                    raise
                except Exception as error:  # noqa: BLE001 - keep worker alive.
                    log.exception("eBay watcher cycle failed")
                    await set_health_value(self.db, "service_status", "degraded")
                    await set_health_value(
                        self.db,
                        "last_cycle_error",
                        f"{type(error).__name__}: {error}",
                    )
                if self.settings.run_once:
                    return
                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                await asyncio.sleep(max(0.0, self.settings.loop_seconds - elapsed))

    async def run_cycle(self, client: EbayBrowseClient) -> EbayCycleResult:
        await self.initialize()
        result = EbayCycleResult()

        tracked = await claim_due_tracked_listings(
            self.db,
            limit=self.settings.tracked_batch_size,
            lease_seconds=max(120, self.settings.loop_seconds * 4),
        )
        if tracked:
            result = await self._refresh_tracked(client, tracked, result)

        rules = await claim_due_watch_rules(
            self.db,
            limit=self.settings.rule_batch_size,
            lease_seconds=max(120, self.settings.loop_seconds * 4),
        )
        result = result.add(rules_claimed=len(rules))
        for rule in rules:
            try:
                result = await self._scan_rule(client, rule, result)
                await complete_watch_rule(self.db, rule)
                result = result.add(rules_succeeded=1)
            except asyncio.CancelledError:
                raise
            except Exception as error:  # noqa: BLE001 - isolate one rule.
                log.exception("eBay watch rule failed rule=%s", rule.rule_id)
                await fail_watch_rule(
                    self.db,
                    rule,
                    error=f"{type(error).__name__}: {error}",
                    retry_seconds=self.settings.failure_retry_seconds,
                )
                result = result.add(rules_failed=1)
        return result

    async def _scan_rule(
        self,
        client: EbayBrowseClient,
        rule: EbayWatchRule,
        result: EbayCycleResult,
    ) -> EbayCycleResult:
        response = await client.search(rule)
        listings = parse_ebay_search_response(response.payload)
        references = comparable_references(
            listings,
            minimum_comparables=self.settings.minimum_comparables,
        )
        result = result.add(searches=1, listings_seen=len(listings))
        for listing in listings:
            result = await self._observe_and_maybe_publish(
                client,
                listing=listing,
                rule=rule,
                comparable=references.get(listing.item_id),
                result=result,
                discovery_source="search",
            )
        return result

    async def _refresh_tracked(
        self,
        client: EbayBrowseClient,
        targets: list[Any],
        result: EbayCycleResult,
    ) -> EbayCycleResult:
        response = await client.get_items([target.item_id for target in targets])
        listings = parse_ebay_items_response(response.payload)
        by_id = {listing.item_id: listing for listing in listings}
        missing: list[str] = []
        for target in targets:
            listing = by_id.get(target.item_id)
            if listing is None:
                missing.append(target.item_id)
                continue
            rule = await get_watch_rule(self.db, target.rule_id)
            if rule is None or not rule.enabled:
                await fail_tracked_listings(
                    self.db,
                    item_ids=[target.item_id],
                    error="watch rule missing or disabled",
                    retry_seconds=self.settings.background_tracked_interval_seconds,
                    deactivate=True,
                )
                continue
            result = await self._observe_and_maybe_publish(
                client,
                listing=listing,
                rule=rule,
                comparable=None,
                result=result,
                discovery_source="tracked",
            )
        if missing:
            await fail_tracked_listings(
                self.db,
                item_ids=missing,
                error="eBay getItems response omitted tracked listing",
                retry_seconds=self.settings.failure_retry_seconds,
            )
        return result.add(tracked_checked=len(targets))

    async def _observe_and_maybe_publish(
        self,
        client: EbayBrowseClient,
        *,
        listing: EbayListing,
        rule: EbayWatchRule,
        comparable: ComparableReference | None,
        result: EbayCycleResult,
        discovery_source: str,
    ) -> EbayCycleResult:
        delay = timedelta(
            seconds=(
                self.settings.default_tracked_interval_seconds
                if rule.sought_after
                else self.settings.background_tracked_interval_seconds
            )
        )
        history = await store_listing_observation(
            self.db,
            listing=listing,
            rule=rule,
            next_check_delay=delay,
        )
        result = result.add(observations=1)
        decision = qualify_ebay_deal(
            listing=listing,
            rule=rule,
            history=history,
            comparable=comparable,
            settings=self.settings,
        )
        if not decision.should_publish:
            return result.add(blocked=1)

        confirmed = await confirm_exact_listing(client, listing)
        result = result.add(confirmations=1)
        # The qualifying price must survive the cache-independent exact item
        # request. A changed or incomplete listing waits for the next cycle.
        if (
            confirmed.delivered_price is None
            or listing.delivered_price is None
            or abs(confirmed.delivered_price - listing.delivered_price) > 0.01
            or confirmed.condition_bucket != listing.condition_bucket
            or confirmed.seller_id != listing.seller_id
        ):
            return result.add(blocked=1)

        candidate = candidate_for_ebay_listing(
            confirmed,
            rule=rule,
            decision=decision,
            discovery_source=discovery_source,
        )
        inserted = await publish_verified_retailer_event(
            self.db,
            event_key=decision.event_key,
            retailer="ebay",
            product_key=confirmed.item_id,
            event_type=decision.event_type,
            candidate=candidate,
            source_verified_at=datetime.now(timezone.utc).isoformat(),
        )
        await mark_listing_event(
            self.db,
            item_id=confirmed.item_id,
            event_key=decision.event_key,
            current_price=float(confirmed.delivered_price),
        )
        return result.add(events=int(inserted))

    async def _record_success(
        self,
        result: EbayCycleResult,
        calls_made: int,
    ) -> None:
        counts = await ebay_watcher_counts(self.db)
        now = datetime.now(timezone.utc).isoformat()
        await set_health_value(self.db, "service_status", "healthy")
        await set_health_value(self.db, "last_successful_cycle_at", now)
        await set_health_value(self.db, "last_cycle_error", "")
        await set_health_value(self.db, "api_calls_since_start", calls_made)
        await set_health_value(
            self.db,
            "last_cycle_summary",
            (
                f"rules={result.rules_claimed}/{result.rules_succeeded}/{result.rules_failed},"
                f"searches={result.searches},seen={result.listings_seen},"
                f"tracked={result.tracked_checked},observations={result.observations},"
                f"confirmations={result.confirmations},events={result.events},"
                f"blocked={result.blocked},"
                f"enabled_rules={counts.get('enabled_rules', 0)},"
                f"tracked_listings={counts.get('tracked_listings', 0)}"
            ),
        )


def qualify_ebay_deal(
    *,
    listing: EbayListing,
    rule: EbayWatchRule,
    history: ListingHistory,
    comparable: ComparableReference | None,
    settings: EbayWatcherSettings,
    now: datetime | None = None,
) -> EbayDealDecision:
    current = listing.delivered_price
    if current is None or current <= 0 or not listing.shipping_known:
        return EbayDealDecision(False, reason="delivered price is not exact")
    if listing.currency.upper() != "USD":
        return EbayDealDecision(False, reason="non-USD listing")
    if not listing.fixed_price or not listing.active:
        return EbayDealDecision(False, reason="listing is not active fixed-price")
    allowed = set(rule.allowed_conditions or settings.allowed_conditions)
    if listing.condition_bucket not in allowed:
        return EbayDealDecision(False, reason="condition is not allowed")
    if listing.suspicious_reason:
        return EbayDealDecision(False, reason=f"suspicious listing: {listing.suspicious_reason}")
    if not listing.seller_id:
        return EbayDealDecision(False, reason="seller identity missing")
    if (
        listing.seller_feedback_percentage is None
        or listing.seller_feedback_percentage < rule.min_seller_feedback_percentage
    ):
        return EbayDealDecision(False, reason="seller feedback percentage below rule")
    if (
        listing.seller_feedback_score is None
        or listing.seller_feedback_score < rule.min_seller_feedback_score
    ):
        return EbayDealDecision(False, reason="seller feedback score below rule")

    references: list[tuple[float, str, int]] = []
    if (
        history.prior_baseline_price is not None
        and history.prior_baseline_price > current
        and history.prior_baseline_observations
        >= settings.minimum_baseline_observations
        and _baseline_old_enough(
            history.prior_baseline_first_seen_at,
            minimum_age_seconds=settings.minimum_baseline_age_seconds,
            now=now,
        )
    ):
        references.append(
            (
                history.prior_baseline_price,
                "sniperplug.ebay.exact_listing_history.baseline",
                history.prior_baseline_observations,
            )
        )
    if (
        comparable is not None
        and listing.exact_identity
        and comparable.sample_size >= settings.minimum_comparables
        and comparable.price > current
    ):
        references.append(
            (comparable.price, comparable.source, comparable.sample_size)
        )
    if not references:
        return EbayDealDecision(False, reason="no trusted prior or comparable reference")

    # Prefer the exact listing's own durable history. If only market comps exist,
    # use the exact-product/condition median. Never use seller marketingPrice as
    # public proof.
    reference, source, sample_size = references[0]
    discount = (reference - current) / reference * 100.0
    threshold = max(1, int(rule.min_discount_percent))
    if discount + 1e-9 < threshold:
        return EbayDealDecision(False, reason="verified discount below rule")

    big_ticket = reference >= settings.big_ticket_min_reference_price
    sought_after = bool(rule.sought_after)
    required_reference = (
        max(0.01, rule.min_reference_price)
        if sought_after
        else max(rule.min_reference_price, settings.big_ticket_min_reference_price)
    )
    if reference < required_reference or not (big_ticket or sought_after):
        return EbayDealDecision(False, reason="not big-ticket or sought-after")

    if (
        history.last_alert_price is not None
        and current >= history.last_alert_price - 0.001
    ):
        return EbayDealDecision(False, reason="same or worse price already alerted")

    event_type = "price_drop" if "listing_history" in source else "below_market"
    event_key = sha256(
        (
            f"ebay|{listing.item_id}|{event_type}|"
            f"{int(round(current * 100))}|{int(round(reference * 100))}|{source}"
        ).encode("utf-8")
    ).hexdigest()
    if event_key == history.last_event_key:
        return EbayDealDecision(False, reason="duplicate event")
    return EbayDealDecision(
        should_publish=True,
        event_key=event_key,
        event_type=event_type,
        reference_price=round(reference, 2),
        reference_source=source,
        discount_percent=round(discount, 2),
        comparable_count=sample_size if source.endswith("median") else 0,
        reason="verified extreme eBay opportunity",
    )


async def confirm_exact_listing(
    client: EbayBrowseClient,
    listing: EbayListing,
) -> EbayListing:
    response = await client.get_item(listing.item_id)
    confirmed = parse_ebay_item(response.payload)
    if confirmed.item_id != listing.item_id:
        raise RuntimeError("eBay exact confirmation returned a different item ID")
    if listing.exact_identity and confirmed.fingerprint != listing.fingerprint:
        raise RuntimeError("eBay exact confirmation changed the product fingerprint")
    if not confirmed.product_url:
        confirmed = replace(confirmed, product_url=listing.product_url)
    return confirmed


def candidate_for_ebay_listing(
    listing: EbayListing,
    *,
    rule: EbayWatchRule,
    decision: EbayDealDecision,
    discovery_source: str,
) -> SourceCandidate:
    current = float(listing.delivered_price or 0.0)
    reference = float(decision.reference_price or 0.0)
    item_price = float(listing.item_price)
    shipping = float(listing.shipping_price or 0.0)
    variant_attributes = {
        "ebayStructuredPriceProof": "yes",
        "ebayIndependentConfirmation": "yes",
        "ebayItemId": listing.item_id,
        "ebayLegacyItemId": listing.legacy_item_id,
        "ebayFingerprint": listing.fingerprint,
        "ebayExactIdentity": "yes" if listing.exact_identity else "no",
        "ebayConditionId": listing.condition_id,
        "ebayConditionBucket": listing.condition_bucket,
        "ebayItemPrice": f"{item_price:.2f}",
        "ebayShippingPrice": f"{shipping:.2f}",
        "ebayDeliveredPrice": f"{current:.2f}",
        "ebaySellerFeedbackPercentage": (
            f"{listing.seller_feedback_percentage:.2f}"
            if listing.seller_feedback_percentage is not None
            else ""
        ),
        "ebaySellerFeedbackScore": str(listing.seller_feedback_score or 0),
        "ebayReferenceSource": decision.reference_source,
        "ebayComparableCount": str(decision.comparable_count),
        "ebayWatchRuleId": rule.rule_id,
        "ebayWatchRuleLabel": rule.label,
        "ebaySoughtAfterRule": "yes" if rule.sought_after else "no",
        "ebayRuleMinimumDiscount": str(rule.min_discount_percent),
        "ebayRuleMinimumReferencePrice": f"{rule.min_reference_price:.2f}",
        "ebayRuleMinimumSellerFeedbackPercentage": (
            f"{rule.min_seller_feedback_percentage:.2f}"
        ),
        "ebayRuleMinimumSellerFeedbackScore": str(
            rule.min_seller_feedback_score
        ),
        "ebayDiscoverySource": discovery_source,
        "trustedReferencePrice": f"{reference:.2f}",
        "trustedReferenceSource": decision.reference_source,
    }
    if listing.watch_count is not None:
        variant_attributes["ebayWatchCount"] = str(listing.watch_count)
    if listing.marketing_original_price is not None:
        # Display-only. It is deliberately not used as the trusted reference.
        variant_attributes["ebaySellerMarketingOriginalPrice"] = (
            f"{listing.marketing_original_price:.2f}"
        )
    for key, value in listing.aspects.items():
        if key.lower() in {
            "color",
            "size",
            "storage capacity",
            "platform",
            "edition",
            "number in pack",
        }:
            variant_attributes[f"ebayAspect:{key}"] = value

    signals = [
        "Exact eBay item re-fetched before alert",
        (
            "Exact listing price history verified"
            if decision.event_type == "price_drop"
            else f"Exact-product condition median verified across {decision.comparable_count} other listings"
        ),
    ]
    return SourceCandidate(
        source_key="ebay_browse_api",
        retailer="ebay",
        title=listing.title,
        product_url=listing.product_url,
        direct_product_url=listing.product_url,
        current_price=current,
        typical_price=reference,
        image_url=listing.image_url or None,
        deal_lane=(
            "verified_ebay_price_drop"
            if decision.event_type == "price_drop"
            else "verified_ebay_market_gap"
        ),
        api_current_price=current,
        api_reference_price=reference,
        api_discount_percent=decision.discount_percent,
        api_condition=listing.condition_bucket,
        api_condition_path="Browse.item.conditionId+condition",
        api_reference_path=decision.reference_source,
        api_price_path="Browse.item.price+shippingOptions[].shippingCost",
        product_id=listing.item_id,
        product_id_type="ebay_item_id",
        sku=listing.mpn or None,
        upc=listing.gtin or None,
        selected_offer_id=listing.item_id,
        variant_label=_variant_label(listing),
        variant_attributes=variant_attributes,
        color=_aspect_value(listing, "color"),
        platform=_aspect_value(listing, "platform"),
        model=listing.model or None,
        seller_name=listing.seller_id,
        fulfillment_type="eBay seller shipping",
        condition=listing.condition_bucket,
        stock_status=listing.estimated_availability_status or "AVAILABLE",
        can_add_to_cart=listing.fixed_price and listing.active,
        signals=signals,
    )


def _variant_label(listing: EbayListing) -> str | None:
    parts = [
        value
        for value in (
            listing.brand,
            listing.model,
            _aspect_value(listing, "storage capacity"),
            _aspect_value(listing, "color"),
            _aspect_value(listing, "size"),
        )
        if value
    ]
    return " • ".join(dict.fromkeys(parts)) or None


def _aspect_value(listing: EbayListing, wanted: str) -> str | None:
    target = wanted.strip().lower()
    for key, value in listing.aspects.items():
        if key.strip().lower() == target and str(value).strip():
            return str(value).strip()
    return None


def _baseline_old_enough(
    value: str,
    *,
    minimum_age_seconds: int,
    now: datetime | None,
) -> bool:
    if minimum_age_seconds <= 0:
        return True
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    resolved_now = now or datetime.now(timezone.utc)
    if resolved_now.tzinfo is None:
        resolved_now = resolved_now.replace(tzinfo=timezone.utc)
    return parsed <= resolved_now.astimezone(timezone.utc) - timedelta(
        seconds=minimum_age_seconds
    )
