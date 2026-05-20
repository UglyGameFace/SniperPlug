from __future__ import annotations

import logging

import discord
from discord.ext import commands

from sniperplug.config import Settings
from sniperplug.cogs.sniperplug import SniperPlugCog
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

        await self.add_cog(SniperPlugCog(self))

        if self.settings.dev_guild_id:
            guild = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %s guild slash commands to %s", len(synced), self.settings.dev_guild_id)
        else:
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
