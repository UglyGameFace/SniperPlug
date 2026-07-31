from __future__ import annotations

import asyncio
import logging
import os
import platform
import sys

import discord
from discord.ext import commands

from sniperplug.config import Settings
from sniperplug.cogs.active_deal_history import ActiveDealHistoryCog
from sniperplug.cogs.active_deal_recheck import ActiveDealRecheckCog
from sniperplug.cogs.active_deals import ActiveDealsCog
from sniperplug.cogs.auto_discovery import AutoDiscoveryCog
from sniperplug.cogs.native_auto_scan_runner import AutoScanRunnerCog
from sniperplug.cogs.resilient_auto_scan_runner import AutoScanRunnerCog as ResilientAutoScanRunnerCog
from sniperplug.cogs.clearance_bank import ClearanceBankCog
from sniperplug.cogs.deal_feedback_admin import DealFeedbackAdminCog
from sniperplug.cogs.home_depot_local import HomeDepotLocalCog
from sniperplug.cogs.home_depot_search import HomeDepotSearchCog
from sniperplug.cogs.local_inventory import LocalInventoryCog
from sniperplug.cogs.open_box_deals import OpenBoxDealsCog
from sniperplug.cogs.public_alerts import PublicAlertsCog, register_persistent_public_panel_views
from sniperplug.cogs.settings_dashboard import SettingsDashboardCog
from sniperplug.cogs.sniperplug import SniperPlugCog
from sniperplug.cogs.storage_admin import StorageAdminCog
from sniperplug.cogs.verizon_shine import VerizonShineCog
from sniperplug.cogs.verified_deal_scanner import VerifiedDealScannerCog
from sniperplug.cogs.workflow import WorkflowCog
from sniperplug.providers.bestbuy import BestBuyProvider
from sniperplug.providers.cached_walmart import CachedWalmartProvider
from sniperplug.providers.home_depot import HomeDepotProvider
from sniperplug.providers.registry import provider_registry
from sniperplug.providers.serpapi_home_depot import SerpApiHomeDepotProvider, configure_home_depot_search_cache
from sniperplug.providers.walmart import WalmartAffiliateConfig, WalmartProvider
from sniperplug.services.active_deal_history import ensure_active_deal_history
from sniperplug.services.bounded_feedback_views import register_bounded_persistent_feedback_views
from sniperplug.services.error_logging import (
    configure_runtime_logging,
    ensure_error_logging_table,
    install_asyncio_exception_handler,
    install_discord_error_handlers,
    install_global_exception_hooks,
)
from sniperplug.services.home_depot_product_lookup import configure_home_depot_product_detail_cache
from sniperplug.services.storage_maintenance import run_storage_maintenance
from sniperplug.services.walmart_recheck_audit import ensure_walmart_recheck_audit
from sniperplug.storage.db import Database


log = logging.getLogger("sniperplug")


class SniperPlugBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        intents.message_content = env_enabled("ENABLE_MESSAGE_CONTENT_INTENT", default=False)
        super().__init__(command_prefix=commands.when_mentioned_or("!"), intents=intents)
        self.settings = settings
        self.db = Database(settings.database_path)
        log.info("Discord intents configured: message_content=%s slash_first=true", intents.message_content)

    async def setup_hook(self) -> None:
        log.info(
            "Runtime identity python=%s implementation=%s platform=%s executable=%s pid=%s",
            platform.python_version(),
            platform.python_implementation(),
            sys.platform,
            sys.executable,
            os.getpid(),
        )
        await self.db.connect()
        log.info("Database connected backend=%s", getattr(self.db, "backend", "unknown"))
        await self.db.init()
        await ensure_error_logging_table(self.db)
        await ensure_active_deal_history(self.db)
        await ensure_walmart_recheck_audit(self.db)
        install_discord_error_handlers(self)
        install_asyncio_exception_handler(asyncio.get_running_loop(), lambda: self.db)
        log.info("Error logging installed: discord=true asyncio=true db_table=error_events")
        log.info("Active deal lifecycle history installed: retention_days=30 max_rows_per_guild=1000")
        log.info("Walmart recheck audit installed: retention_days=30 max_rows_per_guild=2000")
        log.info("Database schema ready backend=%s", getattr(self.db, "backend", "unknown"))
        configure_home_depot_product_detail_cache(self.db)
        configure_home_depot_search_cache(self.db)
        log.info("Home Depot search/detail caches connected backend=%s", getattr(self.db, "backend", "unknown"))

        maintenance = await run_storage_maintenance(self.db)
        log.info("Storage maintenance completed: %s", maintenance.log_fields())

        feedback_views = await register_bounded_persistent_feedback_views(self)
        public_panel_views = await register_persistent_public_panel_views(self)
        log.info("Persistent deal feedback views registered: %s (bounded cap=250)", feedback_views)
        log.info("Persistent public panel views registered: %s", public_panel_views)

        provider_registry.clear()
        provider_registry.register(BestBuyProvider(self.settings.bestbuy_api_key))
        walmart_provider = WalmartProvider(walmart_affiliate_config(self.settings))
        provider_registry.register(CachedWalmartProvider(self.db, walmart_provider))
        provider_registry.register(HomeDepotProvider())
        provider_registry.register(SerpApiHomeDepotProvider())
        log.info(
            "Walmart provider registered: enabled=%s configured=%s publisher_id=%s",
            walmart_provider.config.enabled,
            walmart_provider.config.configured,
            bool(walmart_provider.config.publisher_id),
        )

        await self.add_cog(SniperPlugCog(self))
        await self.add_cog(WorkflowCog(self))
        await self.add_cog(VerifiedDealScannerCog(self))
        await self.add_cog(OpenBoxDealsCog(self))
        await self.add_cog(LocalInventoryCog(self))
        await self.add_cog(ClearanceBankCog(self))
        await self.add_cog(HomeDepotSearchCog(self))
        await self.add_cog(HomeDepotLocalCog(self))
        await self.add_cog(AutoDiscoveryCog(self))
        await self.add_cog(PublicAlertsCog(self))
        await self.add_cog(ActiveDealsCog(self))
        await self.add_cog(ActiveDealRecheckCog(self))
        await self.add_cog(ActiveDealHistoryCog(self))
        await self.add_cog(SettingsDashboardCog(self))
        await self.add_cog(DealFeedbackAdminCog(self))
        await self.add_cog(StorageAdminCog(self))
        await self.add_cog(VerizonShineCog(self))
        await self.add_cog(ResilientAutoScanRunnerCog(self))
        log.info("Runtime services ready: provider_count=%s", len(provider_registry.providers))

        await self._sync_commands()

    async def _sync_commands(self) -> None:
        if env_enabled("CLEAR_STALE_GLOBAL_COMMANDS_ON_BOOT"):
            self.tree.clear_commands(guild=None)
            synced = await self.tree.sync()
            log.warning("Cleared stale global slash commands. Synced %s global commands. Turn CLEAR_STALE_GLOBAL_COMMANDS_ON_BOOT off now.", len(synced))
            return
        if not self.settings.sync_commands_on_boot:
            log.info("Skipped slash command sync on boot. Set SYNC_COMMANDS_ON_BOOT=true only when command definitions changed.")
            return
        if self.settings.sync_global_commands:
            synced = await self.tree.sync()
            log.info("Synced %s global slash commands", len(synced))
            return
        if self.settings.dev_guild_ids:
            for guild_id in self.settings.dev_guild_ids:
                guild = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info("Synced %s guild slash commands to %s", len(synced), guild_id)
            return
        log.info("No DEV_GUILD_IDS configured and global sync is off; skipped slash command sync.")

    async def on_ready(self) -> None:
        log.info("SniperPlug online as %s (%s)", self.user, self.user.id if self.user else "unknown")


def walmart_affiliate_config(settings: Settings) -> WalmartAffiliateConfig:
    """Build the Walmart runtime config from the already-validated Settings object."""
    return WalmartAffiliateConfig(
        consumer_id=settings.walmart_consumer_id,
        key_version=settings.walmart_key_version or "1",
        private_key_b64=settings.walmart_private_key_b64,
        publisher_id=settings.walmart_publisher_id,
        enabled=settings.walmart_provider_enabled,
    )


def env_enabled(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def run() -> None:
    settings = Settings.from_env()
    configure_runtime_logging()
    install_global_exception_hooks()
    if not settings.discord_token:
        raise RuntimeError("DISCORD_TOKEN is required")
    bot = SniperPlugBot(settings)
    bot.run(settings.discord_token)
