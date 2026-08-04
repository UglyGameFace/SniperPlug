from __future__ import annotations

import asyncio
import platform
import sys
import time

from discord.ext import tasks

from sniperplug.cogs import auto_scan_runner as legacy
from sniperplug.cogs import resilient_auto_scan_runner as resilient
from sniperplug.providers.registry import provider_registry
from sniperplug.services.autoscan_live_guild_reconciliation import (
    list_live_public_alert_guilds,
    reconcile_live_public_alert_setups,
)
from sniperplug.services.autoscan_observed_price_memory import (
    collect_verified_discount_cards_with_observed_memory,
)
from sniperplug.services.verified_retailer_event_fanout import (
    fanout_verified_retailer_events,
)
from sniperplug.services.walmart_fresh_work_policy import (
    catalog_backpressure_reason,
)
from sniperplug.services.walmart_global_catalog_autoscan import (
    DEFAULT_ROUTES_PER_BATCH,
    claim_next_catalog_routes,
    complete_catalog_claim,
    load_global_catalog_state,
    release_catalog_claim,
)
from sniperplug.services.walmart_global_deal_fanout_bulk import (
    fanout_recent_exact_walmart_deals,
)
from sniperplug.services.walmart_exact_queue_health import (
    load_walmart_exact_queue_health,
)
from sniperplug.services.walmart_exact_queue_bulk_runtime import (
    process_actionable_walmart_exact_queue_batch,
)


GLOBAL_RECONCILIATION_MINUTES = 30
GLOBAL_DISCOVERY_INITIAL_DELAY_SECONDS = 45
GLOBAL_DISCOVERY_INTERVAL_SECONDS = 60
GLOBAL_DISCOVERY_BUSY_RETRY_SECONDS = 15
GLOBAL_DISCOVERY_MIN_DISCOUNT = 10
GLOBAL_BACKPRESSURE_LOG_INTERVAL_SECONDS = 5 * 60
GLOBAL_FANOUT_EVENT_LIMIT = 20


class AutoScanRunnerCog(resilient.AutoScanRunnerCog):
    """Global retailer discovery plus per-destination exact-deal fanout.

    Walmart discovery and verification remain inside this bot. Standalone
    retailer watchers, beginning with HP Store, publish exact candidates through
    the shared durable event outbox. This bot alone owns Discord delivery,
    thresholds, category preferences, duplicate guards, and DM subscriptions.
    """

    def __init__(self, bot):
        super().__init__(bot)
        self._walmart_catalog_discovery_task: asyncio.Task | None = None
        self._last_backpressure_log_monotonic = 0.0

    async def cog_load(self) -> None:
        # Do not start the inherited per-guild scheduled route loop. Its manual
        # command methods and exact safety gates remain inherited and active.
        self.global_reconciliation_loop.start()
        self._event_loop_watchdog_task = asyncio.create_task(
            self._event_loop_watchdog(),
            name="sniperplug-event-loop-watchdog",
        )
        self._walmart_verification_queue_task = asyncio.create_task(
            self._walmart_exact_verification_worker(),
            name="sniperplug-walmart-exact-verification-queue",
        )
        self._walmart_catalog_discovery_task = asyncio.create_task(
            self._walmart_global_catalog_worker(),
            name="sniperplug-walmart-global-catalog-autoscan",
        )
        self._runtime_phase = "idle"
        legacy.log.info(
            "Autoscan global architecture active python=%s platform=%s "
            "per_guild_discovery=false global_catalog_routes_per_batch=%s "
            "global_catalog_interval_s=%s exact_queue_batch=%s "
            "exact_queue_interval_s=%s global_exact_fanout=true personal_dm_alerts=true "
            "external_verified_event_fanout=true terminal_identity_quarantine=true "
            "exact_parse_off_event_loop=true bulk_exact_persistence=true "
            "fresh_work_priority=true bulk_fanout=true",
            platform.python_version(),
            sys.platform,
            DEFAULT_ROUTES_PER_BATCH,
            GLOBAL_DISCOVERY_INTERVAL_SECONDS,
            resilient.WALMART_QUEUE_BATCH_SIZE,
            resilient.WALMART_QUEUE_INTERVAL_SECONDS,
        )

    async def cog_unload(self) -> None:
        self.global_reconciliation_loop.cancel()
        tasks_to_cancel = (
            self._event_loop_watchdog_task,
            self._walmart_verification_queue_task,
            self._walmart_catalog_discovery_task,
        )
        self._event_loop_watchdog_task = None
        self._walmart_verification_queue_task = None
        self._walmart_catalog_discovery_task = None
        self._runtime_phase = "unloading"
        for task in tasks_to_cancel:
            if task is not None:
                task.cancel()

    @tasks.loop(minutes=GLOBAL_RECONCILIATION_MINUTES)
    async def global_reconciliation_loop(self) -> None:
        await self.bot.wait_until_ready()

        try:
            self._runtime_phase = "setup_reconciliation"
            repair_summary = await reconcile_live_public_alert_setups(
                self.bot.db,
                self.bot,
            )
            legacy.log.info(
                "Autoscan live-guild setup reconciliation complete "
                "ghost_rows_deleted=%s ghost_rows_quarantined=%s "
                "stale_replica_rows_ignored=%s repaired=%s healthy=%s needs_action=%s",
                repair_summary.get("ghost_rows_deleted", 0),
                repair_summary.get("ghost_rows_quarantined", 0),
                repair_summary.get("ghost_rows_already_quarantined", 0),
                repair_summary.get("repaired", 0),
                repair_summary.get("healthy", 0),
                repair_summary.get("needs_action", 0),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            legacy.log.exception(
                "Autoscan live-guild setup reconciliation failed safely"
            )
        finally:
            self._runtime_phase = "idle"

        try:
            load_result = await list_live_public_alert_guilds(
                self.bot.db,
                self.bot,
            )
            guilds = list(load_result.guilds)
            legacy.log.info(
                "Autoscan eligible fanout guilds count=%s guild_ids=%s",
                len(guilds),
                [int(guild.guild_id) for guild in guilds],
            )
            if load_result.stale_visible_ids:
                legacy.log.debug(
                    "Ignored non-live public-alert rows during fanout enrollment "
                    "guild_ids=%s tombstoned=%s",
                    list(load_result.stale_visible_ids),
                    list(load_result.tombstoned_visible_ids),
                )

            fanout = await fanout_recent_exact_walmart_deals(
                self.bot,
                event_limit=GLOBAL_FANOUT_EVENT_LIMIT,
            )
            if fanout.new_events or fanout.events_processed or fanout.public_posts or fanout.dm_sent:
                legacy.log.info(fanout.summary_line())
            await self._fanout_external_verified_events()
        except asyncio.CancelledError:
            raise
        except Exception:
            legacy.log.exception(
                "Global exact-deal reconciliation/fanout failed safely"
            )

    @global_reconciliation_loop.before_loop
    async def before_global_reconciliation_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _walmart_global_catalog_worker(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(GLOBAL_DISCOVERY_INITIAL_DELAY_SECONDS)
        try:
            while True:
                if self._any_foreground_walmart_scan_busy():
                    await asyncio.sleep(GLOBAL_DISCOVERY_BUSY_RETRY_SECONDS)
                    continue

                health_error = await legacy.provider_health_error_message()
                if health_error:
                    legacy.log.info(
                        "Global Walmart catalog discovery paused: %s",
                        health_error,
                    )
                    await asyncio.sleep(5 * 60)
                    continue

                queue_health = await load_walmart_exact_queue_health(self.bot.db)
                backpressure = catalog_backpressure_reason(queue_health)
                if backpressure:
                    now = time.monotonic()
                    if (
                        now - self._last_backpressure_log_monotonic
                        >= GLOBAL_BACKPRESSURE_LOG_INTERVAL_SECONDS
                    ):
                        legacy.log.info(
                            "Global Walmart catalog discovery paused by %s",
                            backpressure,
                        )
                        self._last_backpressure_log_monotonic = now
                    await asyncio.sleep(GLOBAL_DISCOVERY_INTERVAL_SECONDS)
                    continue

                claim = None
                try:
                    claim = await claim_next_catalog_routes(
                        self.bot.db,
                        route_count=DEFAULT_ROUTES_PER_BATCH,
                    )
                    if claim is None:
                        await asyncio.sleep(GLOBAL_DISCOVERY_BUSY_RETRY_SECONDS)
                        continue

                    self._runtime_phase = (
                        "global_catalog_discovery:"
                        f"routes={claim.start_index + 1}-"
                        f"{claim.start_index + len(claim.queries)}/"
                        f"{claim.total_routes}"
                    )
                    preset = legacy.HuntPreset(
                        key="global_catalog_autoscan",
                        label="Global Walmart Catalog",
                        emoji="🌐",
                        description=(
                            "Durable global route discovery. Search rows only "
                            "discover item IDs; exact proof controls all alerts."
                        ),
                        queries=claim.queries,
                        min_discount=GLOBAL_DISCOVERY_MIN_DISCOUNT,
                    )
                    started = time.monotonic()
                    async with resilient._WALMART_PROVIDER_OPERATION_LOCK:
                        result = await collect_verified_discount_cards_with_observed_memory(
                            requested_by="global_catalog_autoscan",
                            preset=preset,
                            db=self.bot.db,
                            guild_id=None,
                            use_price_memory=False,
                            min_discount=GLOBAL_DISCOVERY_MIN_DISCOUNT,
                        )

                    completed = await complete_catalog_claim(self.bot.db, claim)
                    if not completed:
                        raise RuntimeError(
                            "global catalog claim could not be confirmed complete"
                        )
                    state = await load_global_catalog_state(self.bot.db)
                    elapsed = max(0.0, time.monotonic() - started)
                    legacy.log.info(
                        "Global Walmart catalog batch completed elapsed_s=%.1f %s • "
                        "pages=%s returned_products=%s exact_cards=%s • %s",
                        elapsed,
                        claim.summary_line(),
                        result.pages_checked,
                        result.products_checked,
                        result.total_verified_cards,
                        state.summary_line(total_routes=claim.total_routes),
                    )
                    self._log_global_result_notes(result.warnings)

                    fanout = await fanout_recent_exact_walmart_deals(
                        self.bot,
                        event_limit=GLOBAL_FANOUT_EVENT_LIMIT,
                    )
                    if fanout.new_events or fanout.events_processed or fanout.public_posts or fanout.dm_sent:
                        legacy.log.info(fanout.summary_line())
                    await self._fanout_external_verified_events()
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    if claim is not None:
                        try:
                            await release_catalog_claim(
                                self.bot.db,
                                claim,
                                error=f"{type(error).__name__}: {error}",
                            )
                        except Exception:
                            legacy.log.exception(
                                "Failed to release global catalog claim after error"
                            )
                    legacy.log.exception(
                        "Global Walmart catalog batch failed safely; durable cursor was not advanced"
                    )
                finally:
                    self._runtime_phase = "idle"

                await asyncio.sleep(GLOBAL_DISCOVERY_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            return

    async def _walmart_exact_verification_worker(self) -> None:
        await self.bot.wait_until_ready()
        await asyncio.sleep(resilient.WALMART_QUEUE_INITIAL_DELAY_SECONDS)
        try:
            while True:
                if self._any_foreground_walmart_scan_busy():
                    await asyncio.sleep(resilient.WALMART_QUEUE_BUSY_RETRY_SECONDS)
                    continue

                try:
                    self._runtime_phase = "exact_verification_queue"
                    async with resilient._WALMART_PROVIDER_OPERATION_LOCK:
                        result = await process_actionable_walmart_exact_queue_batch(
                            self.bot.db,
                            provider=provider_registry.get("walmart"),
                            limit=resilient.WALMART_QUEUE_BATCH_SIZE,
                            concurrency=resilient.WALMART_QUEUE_CONCURRENCY,
                            min_discount=GLOBAL_DISCOVERY_MIN_DISCOUNT,
                        )
                    now = time.monotonic()
                    health_due = (
                        now - self._last_queue_health_log_monotonic
                        >= resilient.WALMART_QUEUE_HEALTH_LOG_INTERVAL_SECONDS
                    )
                    if (
                        result.claimed
                        or result.terminal_quarantined
                        or result.terminal_rearmed
                        or health_due
                    ):
                        health = await load_walmart_exact_queue_health(self.bot.db)
                        legacy.log.info(
                            "%s • %s",
                            result.summary_line(),
                            health.summary_line(),
                        )
                        self._last_queue_health_log_monotonic = now

                    fanout = await fanout_recent_exact_walmart_deals(
                        self.bot,
                        event_limit=GLOBAL_FANOUT_EVENT_LIMIT,
                    )
                    if fanout.new_events or fanout.events_processed or fanout.public_posts or fanout.dm_sent:
                        legacy.log.info(fanout.summary_line())
                    await self._fanout_external_verified_events()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    legacy.log.exception(
                        "Walmart exact-detail queue/fanout batch failed safely; queued rows remain retryable"
                    )
                finally:
                    self._runtime_phase = "idle"

                await asyncio.sleep(resilient.WALMART_QUEUE_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            return

    async def _fanout_external_verified_events(self) -> None:
        result = await fanout_verified_retailer_events(
            self.bot,
            event_limit=GLOBAL_FANOUT_EVENT_LIMIT,
        )
        if result.events_claimed or result.public_posts or result.dm_sent:
            legacy.log.info(result.summary_line())

    def _log_global_result_notes(self, warnings) -> None:
        for warning in tuple(warnings or ()):
            detail = legacy.clean_log_text(warning)
            if not detail:
                continue
            lowered = detail.lower()
            if any(
                marker in lowered
                for marker in (
                    "exact-detail queue",
                    "official walmart detail gate",
                    "exact walmart detail price checks",
                )
            ):
                legacy.log.info(
                    "Global Walmart catalog note detail=%s",
                    detail,
                )
