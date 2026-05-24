from __future__ import annotations

import logging

import discord
from discord.ext import commands

from sniperplug.config import Settings
from sniperplug.cogs.auto_discovery import AutoDiscoveryCog
from sniperplug.cogs.clearance_bank import ClearanceBankCog
from sniperplug.cogs.deal_scanner import DealScannerCog
from sniperplug.cogs.home_depot_search import HomeDepotSearchCog
from sniperplug.cogs.local_inventory import LocalInventoryCog
from sniperplug.cogs.public_alerts import PublicAlertsCog
from sniperplug.cogs.sniperplug import SniperPlugCog
from sniperplug.providers.bestbuy import BestBuyProvider
from sniperplug.providers.home_depot import HomeDepotProvider
from sniperplug.providers.registry import provider_registry
from sniperplug.providers.serpapi_home_depot import SerpApiHomeDepotProvider
from sniperplug.providers.walmart import WalmartProvider
from sniperplug.services.resale_hunt import install_resale_hunt_preset
from sniperplug.storage.db import Database


log = logging.getLogger("sniperplug")


class SniperPlugBot(commands.Bot):
    def __init__(self, settings: Settings):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)

        self.settings = settings
        self.db = Database(settings.database_path)

    async def setup_hook(self) -> None:
        await self.db.connect()
        await self.db.init()

        install_resale_hunt_preset()

        provider_registry.register(BestBuyProvider(self.settings.bestbuy_api_key))
        provider_registry.register(WalmartProvider(configured=False))
        provider_registry.register(HomeDepotProvider())
        provider_registry.register(SerpApiHomeDepotProvider())

        await self.add_cog(SniperPlugCog(self))
        await self.add_cog(DealScannerCog(self))
        await self.add_cog(LocalInventoryCog(self))
        await self.add_cog(ClearanceBankCog(self))
        await self.add_cog(HomeDepotSearchCog(self))
        await self.add_cog(AutoDiscoveryCog(self))
        await self.add_cog(PublicAlertsCog(self))

        await self._sync_commands()

    async def _sync_commands(self) -> None:
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

        synced = await self.tree.sync()
        log.info("Synced %s global slash commands", len(synced))

    async def on_ready(self) -> None:
        log.info("SniperPlug online as %s (%s)", self.user, self.user.id if self.user else "unknown")

    async def close(self) -> None:
        await self.db.close()
        await super().close()


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    settings = Settings.from_env()
    bot = SniperPlugBot(settings)

    async with bot:
        await bot.start(settings.discord_token)
