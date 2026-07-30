from __future__ import annotations

import asyncio
import logging
import os

import discord
from discord.ext import commands

from sniperplug.config import Settings
from sniperplug.cogs.active_deals import ActiveDealsCog
from sniperplug.cogs.auto_discovery import AutoDiscoveryCog
from sniperplug.cogs.native_auto_scan_runner_v2 import AutoScanRunnerCog
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
from sniperplug.services.deal_feedback import register_persistent_feedback_views
from sniperplug.services.error_logging import (
    configure_runtime_logging,
    ensure_error_logging_table,
    install_asyncio_exception_handler,
    install_discord_error_handlers,
    install_global_exception_hooks,
)
from sniperplug.services.home_depot_product_lookup import configure_home_depot_product_detail_cache
from sniperplug.services.storage_maintenance import run_storage_maintenance
from sniperplug.services.setup_self_heal import repair_all_public_alert_setups
from sniperplug.storage.db import Database


log = logging.getLogger("sniperplug")
MAX_SETUP_SELF_HEAL_ATTEMPTS = 3


class SniperPlugBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        intents.message_content = env_enabled("ENABLE_MESSAGE_CONTENT_INTENT", default=False)
        super().__init__(command_prefix=commands.when_mentioned_or("!"), intents=intents)
        self.settings = settings
        self.db = Database(settings.database_path)
        self._setup_self_heal_done = False
        self._setup_self_heal_attempts = 0
        log.info("Discord intents configured: message_content=%s slash_first=true", intents.message_content)

    async def setup_hook(self) -> None:
        await self.db.connect()
        log.info("Database connected backend=%s", getattr(self.db, "backend", "unknown"))
        await self.db.init()
        await ensure_error_logging_table(self.db)
        install_discord_error_handlers(self)
        install_asyncio_exception_handler(asyncio.get_running_loop(), lambda: self.db)
        log.info("Error logging installed: discord=true asyncio=true db_table=error_events")
        log.info("Database schema ready backend=%s", getattr(self.db, "backend", "unknown"))
        configure_home_depot_product_detail_cache(self.db)
        configure_home_depot_search_cache(self.db)
        log.info("Home Depot search/detail caches connected backend=%s", getattr(self.db, "backend", "unknown"))

        maintenance = await run_storage_maintenance(self.db)
        log.info("Storage maintenance completed: %s", maintenance.log_fields())

        feedback_views = await register_persistent_feedback_views(self)
        public_panel_views = await register_persistent_public_panel_views(self)
        log.info("Persistent deal feedback views registered: %s", feedback_views)
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
        await self.add_cog(SettingsDashboardCog(self))
        await self.add_cog(DealFeedbackAdminCog(self))
        await self.add_cog(StorageAdminCog(self))
        await self.add_cog(VerizonShineCog(self))
        await self.add_cog(AutoScanRunnerCog(self))
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
        if self._setup_self_heal_done or self._setup_self_heal_attempts >= MAX_SETUP_SELF_HEAL_ATTEMPTS:
            return
        self._setup_self_heal_attempts += 1
        try:
            repair_summary = await repair_all_public_alert_setups(self.db, self)
        except Exception:
            log.exception(
                "Setup self-heal failed attempt=%s/%s; it will retry on the next ready event",
                self._setup_self_heal_attempts,
                MAX_SETUP_SELF_HEAL_ATTEMPTS,
            )
            return
        self._setup_self_heal_done = True
        log.info("Setup self-heal complete attempt=%s summary=%s", self._setup_self_heal_attempts, repair_summary)


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
