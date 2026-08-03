from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import logging
from typing import Any

from sniperplug.ebay_watcher.client import EbayBrowseClient
from sniperplug.ebay_watcher.config import EbayWatcherSettings
from sniperplug.ebay_watcher.models import (
    EbayAPIBudgetExceeded,
    EbayCycleResult,
    EbayListing,
    EbayWatchRule,
)
from sniperplug.ebay_watcher.parser import (
    comparable_references,
    parse_ebay_items_response,
    parse_ebay_search_response,
)
from sniperplug.ebay_watcher.service.qualification import (
    _eligible_for_tracking,
    _seconds_until_next_utc_day,
    candidate_for_ebay_listing,
    confirm_exact_listing,
    qualify_ebay_deal,
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
    reserve_api_call,
    seed_default_watch_rules,
    set_health_value,
    store_listing_observation,
)
from sniperplug.services.verified_retailer_events import (
    ensure_verified_retailer_event_table,
    publish_verified_retailer_event,
)


log = logging.getLogger("sniperplug.ebay_watcher")


class EbayWatcherService:
    def __init__(self, db: Any, settings: EbayWatcherSettings):
        self.db = db
        self.settings = settings

    async def initialize(self) -> None:
        await ensure_ebay_watcher_tables(self.db)
        await ensure_verified_retailer_event_table(self.db)
        seeded = await seed_default_watch_rules(self.db, self.settings)
        await set_health_value(self.db, "seeded_rules", seeded)
        await set_health_value(
            self.db,
            "verification_policy",
            (
                f"discount_floor={self.settings.default_min_discount_percent},"
                f"big_ticket_floor={self.settings.big_ticket_min_reference_price:.2f},"
                f"minimum_comparables={self.settings.minimum_comparables},"
                f"history_observations={self.settings.minimum_baseline_observations},"
                f"history_age_s={self.settings.minimum_baseline_age_seconds},"
                f"daily_browse_budget={self.settings.browse_api_daily_budget}"
            ),
        )

    async def run_forever(self) -> None:
        await self.initialize()
        while True:
            try:
                async with EbayBrowseClient(
                    self.settings,
                    reserve_call=self._reserve_api_call,
                ) as client:
                    result = await self.run_cycle(client)
                    await self._record_cycle(client, result, error="")
            except asyncio.CancelledError:
                raise
            except EbayAPIBudgetExceeded as error:
                await set_health_value(self.db, "service_status", "api_budget_exhausted")
                await set_health_value(self.db, "last_cycle_error", str(error))
                if self.settings.run_once:
                    raise
                delay = min(
                    3600,
                    max(self.settings.loop_seconds, _seconds_until_next_utc_day()),
                )
                log.warning("%s; next budget check in %ss", error, delay)
                await asyncio.sleep(delay)
                continue
            except Exception as error:  # noqa: BLE001 - worker must survive transient API/schema failure.
                await set_health_value(self.db, "service_status", "degraded")
                await set_health_value(self.db, "last_cycle_error", _error_text(error))
                log.exception("eBay watcher cycle failed")
                if self.settings.run_once:
                    raise
            if self.settings.run_once:
                return
            await asyncio.sleep(self.settings.loop_seconds)

    async def run_cycle(self, client: EbayBrowseClient) -> EbayCycleResult:
        result = EbayCycleResult()
        result = await self._scan_due_rules(client, result)
        result = await self._refresh_tracked(client, result)
        return result

    async def _reserve_api_call(self, bucket: str) -> None:
        await reserve_api_call(
            self.db,
            bucket=bucket,
            daily_limit=self.settings.browse_api_daily_budget,
        )

    async def _scan_due_rules(
        self,
        client: EbayBrowseClient,
        result: EbayCycleResult,
    ) -> EbayCycleResult:
        rules = await claim_due_watch_rules(
            self.db,
            limit=self.settings.rule_batch_size,
            lease_seconds=max(120, self.settings.loop_seconds * 4),
        )
        result = result.add(rules_claimed=len(rules))
        for rule in rules:
            try:
                response = await client.search(rule)
                listings = parse_ebay_search_response(response.payload)
                references = comparable_references(
                    listings,
                    minimum_comparables=self.settings.minimum_comparables,
                )
                result = result.add(
                    searches=1,
                    listings_seen=len(listings),
                )
                for listing in listings:
                    if not _eligible_for_tracking(
                        listing,
                        rule=rule,
                        comparable=references.get(listing.item_id),
                        settings=self.settings,
                        discovery_source="search",
                    ):
                        result = result.add(blocked=1)
                        continue
                    result = await self._observe_and_maybe_publish(
                        client,
                        listing=listing,
                        rule=rule,
                        comparable=references.get(listing.item_id),
                        result=result,
                    )
                await complete_watch_rule(self.db, rule)
                result = result.add(rules_succeeded=1)
            except asyncio.CancelledError:
                raise
            except EbayAPIBudgetExceeded:
                raise
            except Exception as error:  # noqa: BLE001 - isolate one custom watch.
                await fail_watch_rule(
                    self.db,
                    rule,
                    error=_error_text(error),
                    retry_seconds=self.settings.failure_retry_seconds,
                )
                result = result.add(rules_failed=1)
        return result

    async def _refresh_tracked(
        self,
        client: EbayBrowseClient,
        result: EbayCycleResult,
    ) -> EbayCycleResult:
        targets = await claim_due_tracked_listings(
            self.db,
            limit=self.settings.tracked_batch_size,
            lease_seconds=max(120, self.settings.loop_seconds * 4),
        )
        if not targets:
            return result
        target_ids = [target.item_id for target in targets]
        try:
            response = await client.get_items(target_ids)
            listings = parse_ebay_items_response(response.payload)
        except asyncio.CancelledError:
            raise
        except EbayAPIBudgetExceeded:
            raise
        except Exception as error:  # noqa: BLE001 - return all leased targets to retry.
            await fail_tracked_listings(
                self.db,
                item_ids=target_ids,
                error=_error_text(error),
                retry_seconds=self.settings.failure_retry_seconds,
            )
            return result.add(tracked_checked=len(targets), blocked=len(targets))

        by_id = {listing.item_id: listing for listing in listings}
        missing = [item_id for item_id in target_ids if item_id not in by_id]
        if missing:
            await fail_tracked_listings(
                self.db,
                item_ids=missing,
                error="eBay getItems omitted the tracked item",
                retry_seconds=self.settings.failure_retry_seconds,
                max_failures=3,
            )
        result = result.add(
            tracked_checked=len(targets),
            listings_seen=len(listings),
            blocked=len(missing),
        )
        for target in targets:
            listing = by_id.get(target.item_id)
            if listing is None:
                continue
            rule = await get_watch_rule(self.db, target.rule_id)
            if rule is None or not rule.enabled:
                await fail_tracked_listings(
                    self.db,
                    item_ids=[target.item_id],
                    error="eBay watch rule is missing or disabled",
                    retry_seconds=86400,
                    max_failures=1,
                )
                result = result.add(blocked=1)
                continue
            if not _eligible_for_tracking(
                listing,
                rule=rule,
                comparable=None,
                settings=self.settings,
                discovery_source="tracked",
            ):
                await fail_tracked_listings(
                    self.db,
                    item_ids=[target.item_id],
                    error="Tracked eBay listing no longer passes identity/condition/value gates",
                    retry_seconds=self.settings.background_tracked_interval_seconds,
                    max_failures=3,
                )
                result = result.add(blocked=1)
                continue
            result = await self._observe_and_maybe_publish(
                client,
                listing=listing,
                rule=rule,
                comparable=None,
                result=result,
            )
        return result

    async def _observe_and_maybe_publish(
        self,
        client: EbayBrowseClient,
        *,
        listing: EbayListing,
        rule: EbayWatchRule,
        comparable,
        result: EbayCycleResult,
    ) -> EbayCycleResult:
        tracked_interval = (
            self.settings.default_tracked_interval_seconds
            if rule.sought_after
            else self.settings.background_tracked_interval_seconds
        )
        history = await store_listing_observation(
            self.db,
            listing=listing,
            rule=rule,
            next_check_delay=timedelta(seconds=tracked_interval),
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

        try:
            confirmed = await confirm_exact_listing(client, listing)
        except asyncio.CancelledError:
            raise
        except EbayAPIBudgetExceeded:
            raise
        except Exception as error:  # noqa: BLE001 - fail closed on exact confirmation.
            await fail_tracked_listings(
                self.db,
                item_ids=[listing.item_id],
                error=f"exact confirmation failed: {_error_text(error)}",
                retry_seconds=self.settings.failure_retry_seconds,
            )
            return result.add(blocked=1)

        candidate = candidate_for_ebay_listing(
            listing=confirmed,
            rule=rule,
            decision=decision,
        )
        inserted = await publish_verified_retailer_event(
            self.db,
            event_key=decision.event_key,
            retailer="ebay",
            product_key=confirmed.fingerprint,
            event_type=decision.event_type,
            candidate=candidate,
            source_verified_at=datetime.now(timezone.utc).isoformat(),
        )
        await mark_listing_event(
            self.db,
            item_id=confirmed.item_id,
            event_key=decision.event_key,
            alert_price=float(confirmed.delivered_price or confirmed.item_price),
        )
        return result.add(confirmations=1, events=int(inserted))

    async def _record_cycle(
        self,
        client: EbayBrowseClient,
        result: EbayCycleResult,
        *,
        error: str,
    ) -> None:
        counts = await ebay_watcher_counts(self.db)
        await set_health_value(self.db, "service_status", "healthy" if not error else "degraded")
        await set_health_value(self.db, "last_successful_cycle_at", datetime.now(timezone.utc).isoformat())
        await set_health_value(self.db, "last_cycle_error", error)
        await set_health_value(self.db, "api_calls_since_start", client.calls_made)
        for key, value in result.__dict__.items():
            await set_health_value(self.db, f"cycle_{key}", value)
        for key, value in counts.items():
            await set_health_value(self.db, f"count_{key}", value)


def _error_text(error: Exception) -> str:
    return " ".join(f"{type(error).__name__}: {error}".split())[:800]
