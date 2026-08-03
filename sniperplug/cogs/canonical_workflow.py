from __future__ import annotations

from typing import Iterable

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.public_alerts import set_retailer_auto_scan
from sniperplug.cogs.target_location import (
    TargetStoreSelectView,
    clean_zip,
    remove_target_retailer,
    store_picker_embed,
)
from sniperplug.services.deal_category_preferences import apply_preset
from sniperplug.services.deal_threshold_settings import (
    get_starting_deal_percent,
    set_starting_deal_percent,
)
from sniperplug.services.public_alert_config import (
    get_public_alert_config,
    set_public_alert_config,
)
from sniperplug.services.public_posting import normalize_retailer_key
from sniperplug.services.target_locations import (
    clear_target_location,
    get_guild_target_location,
    get_user_target_location,
)
from sniperplug.target_watcher.client import TargetRedSkyClient
from sniperplug.target_watcher.config import TargetWatcherSettings
from sniperplug.target_watcher.stores import TargetStore


REQUIRED_CHANNEL_PERMS = {
    "view_channel": "View Channel",
    "send_messages": "Send Messages",
    "embed_links": "Embed Links",
    "read_message_history": "Read Message History",
}


class CanonicalWorkflowCog(commands.Cog):
    """Canonical setup plus location-safe Target enrollment."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="setup_sniperplug_here",
        description="Use this channel for exact-verified deal alerts.",
    )
    @app_commands.describe(
        public_alerts="Allow exact-verified retailer deals in this channel.",
        threshold="Minimum exact markdown for this server. Recommended: 30-40.",
        best_categories="Apply broad Deal Week and Walmart Cash category coverage.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def setup_sniperplug_here(
        self,
        interaction: discord.Interaction,
        public_alerts: bool = True,
        threshold: app_commands.Range[int, 0, 95] | None = None,
        best_categories: bool = True,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None or interaction.guild is None:
            await interaction.followup.send(
                "Use this in the server and text channel where SniperPlug should post.",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(
                "Run this inside the text channel where SniperPlug should post verified deals.",
                ephemeral=True,
            )
            return

        missing = missing_channel_permissions(channel, interaction.guild.me)
        if missing:
            await interaction.followup.send(
                missing_permissions_message(channel, missing),
                ephemeral=True,
            )
            return

        guild_id = int(interaction.guild_id)
        target_location = await get_guild_target_location(self.bot.db, guild_id)
        target_ready = bool(target_location and target_location.enabled)
        existing_config = await get_public_alert_config(self.bot.db, guild_id)
        retailers = merge_canonical_retailers(
            existing_config.get("retailers") or (),
            include_target=target_ready,
        )
        await self.bot.db.set_guild_deal_channel(guild_id, int(channel.id))
        await set_public_alert_config(
            self.bot.db,
            guild_id=guild_id,
            enabled=bool(public_alerts),
            retailers=retailers,
            channel_id=int(channel.id),
        )

        await set_retailer_auto_scan(
            self.bot.db,
            guild_id,
            "walmart",
            bool(public_alerts),
            interval_hours=0,
            daily_limit=0,
        )

        if threshold is not None:
            await set_starting_deal_percent(self.bot.db, guild_id, int(threshold))
        saved_threshold = await get_starting_deal_percent(self.bot.db, guild_id)

        if best_categories:
            await apply_preset(self.bot.db, guild_id, "deal_week")
            await apply_preset(self.bot.db, guild_id, "walmart_cash")

        config = await get_public_alert_config(self.bot.db, guild_id)
        embed = discord.Embed(
            title="✅ SniperPlug delivery setup complete",
            description=(
                "This server receives fanout from shared retailer scanners. Target is only enrolled after an admin chooses an exact local store."
            ),
            color=discord.Color.green() if config.get("enabled") else discord.Color.orange(),
        )
        embed.add_field(name="Alert channel", value=channel.mention, inline=True)
        embed.add_field(
            name="Public delivery",
            value="Enabled" if config.get("enabled") else "Paused",
            inline=True,
        )
        embed.add_field(
            name="Server filter",
            value=f"Exact retailer markdown: **{saved_threshold}%+**",
            inline=True,
        )
        embed.add_field(
            name="Retailers",
            value=(
                "**Walmart** global catalog + **HP Store** exact-price watcher + "
                + (
                    f"**Target** at **{target_location.display_name}**"
                    if target_ready and target_location is not None
                    else "**Target paused** — run `/target_location` to choose a store"
                )
            ),
            inline=False,
        )
        embed.add_field(
            name="How Target remains location-safe",
            value=(
                "• The server saves one chosen Target store and ZIP.\n"
                "• Target events from other stores are blocked.\n"
                "• Servers using the same store share one watcher location scan.\n"
                "• No Connecticut or owner location is used as a fallback."
            ),
            inline=False,
        )
        embed.add_field(
            name="Next check",
            value="Run `/autoscan_health` to confirm live delivery and Target location health.",
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="target_location",
        description="Choose the Target store used for this server's local alerts.",
    )
    @app_commands.describe(zip_code="Five-digit ZIP used to find nearby Target stores.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def target_location(
        self,
        interaction: discord.Interaction,
        zip_code: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send(
                "Use this command inside the server you are configuring.",
                ephemeral=True,
            )
            return
        stores, error = await self._find_target_stores(zip_code)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        guild_id = int(interaction.guild_id)
        current = await get_guild_target_location(self.bot.db, guild_id)
        view = TargetStoreSelectView(
            db=self.bot.db,
            requester_id=int(interaction.user.id),
            scope_type="guild",
            scope_id=guild_id,
            requested_zip=clean_zip(zip_code),
            stores=stores,
        )
        await interaction.followup.send(
            embed=store_picker_embed(
                stores,
                requested_zip=clean_zip(zip_code),
                scope_label="this server",
                current=current,
            ),
            view=view,
            ephemeral=True,
        )

    @app_commands.command(
        name="target_location_clear",
        description="Disable local Target alerts for this server.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def target_location_clear(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send(
                "Use this command inside the server you are configuring.",
                ephemeral=True,
            )
            return
        guild_id = int(interaction.guild_id)
        removed = await clear_target_location(
            self.bot.db,
            scope_type="guild",
            scope_id=guild_id,
        )
        await remove_target_retailer(self.bot.db, guild_id)
        await interaction.followup.send(
            (
                "✅ This server's Target location was cleared. Local Target alerts are disabled until an admin runs `/target_location` again."
                if removed
                else "This server did not have an active Target location. Target remains disabled."
            ),
            ephemeral=True,
        )

    @app_commands.command(
        name="target_dm_location",
        description="Choose the Target store used for your personal deal DMs.",
    )
    @app_commands.describe(zip_code="Five-digit ZIP used to find nearby Target stores.")
    async def target_dm_location(
        self,
        interaction: discord.Interaction,
        zip_code: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        stores, error = await self._find_target_stores(zip_code)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        user_id = int(interaction.user.id)
        current = await get_user_target_location(self.bot.db, user_id)
        view = TargetStoreSelectView(
            db=self.bot.db,
            requester_id=user_id,
            scope_type="user",
            scope_id=user_id,
            requested_zip=clean_zip(zip_code),
            stores=stores,
        )
        await interaction.followup.send(
            embed=store_picker_embed(
                stores,
                requested_zip=clean_zip(zip_code),
                scope_label="your personal Target DMs",
                current=current,
            ),
            view=view,
            ephemeral=True,
        )

    @app_commands.command(
        name="target_dm_location_clear",
        description="Disable location-specific Target deal DMs for you.",
    )
    async def target_dm_location_clear(
        self,
        interaction: discord.Interaction,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        removed = await clear_target_location(
            self.bot.db,
            scope_type="user",
            scope_id=int(interaction.user.id),
        )
        await interaction.followup.send(
            (
                "✅ Your personal Target location was cleared. Local Target DMs will not be sent until you choose another store."
                if removed
                else "You did not have an active personal Target location."
            ),
            ephemeral=True,
        )

    async def _find_target_stores(
        self,
        zip_code: str,
    ) -> tuple[tuple[TargetStore, ...], str]:
        cleaned = clean_zip(zip_code)
        if len(cleaned) != 5:
            return (), "Enter a valid five-digit ZIP code."
        api_key = str(
            getattr(self.bot.settings, "target_redsky_api_key", None) or ""
        ).strip()
        if not api_key:
            return (
                (),
                "Target location setup is temporarily unavailable because the owner has not configured `TARGET_REDSKY_API_KEY`.",
            )
        settings = TargetWatcherSettings(
            redsky_api_key=api_key,
            require_remote_database=False,
        )
        try:
            async with TargetRedSkyClient(settings) as client:
                return await client.find_nearby_stores(cleaned, limit=10), ""
        except Exception as error:
            text = " ".join(str(error or type(error).__name__).split())[:300]
            return (
                (),
                "Target did not return a usable nearby-store list for that ZIP. "
                f"Nothing was saved. `{type(error).__name__}: {text}`",
            )

    @setup_sniperplug_here.error
    @target_location.error
    @target_location_clear.error
    async def setup_permissions_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        message = (
            "You need **Manage Server** permission to configure SniperPlug or the server's Target location."
            if isinstance(error, app_commands.MissingPermissions)
            else f"SniperPlug setup failed safely: `{type(error).__name__}`"
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def merge_canonical_retailers(
    existing: Iterable[str],
    *,
    include_target: bool = False,
) -> tuple[str, ...]:
    """Add safe canonical sources without silently enrolling local Target."""

    merged: list[str] = []
    values = [*tuple(existing), "walmart", "hp"]
    if include_target:
        values.append("target")
    for value in values:
        retailer = normalize_retailer_key(value)
        if retailer == "target" and not include_target:
            continue
        if retailer and retailer not in merged:
            merged.append(retailer)
    return tuple(merged)


def missing_channel_permissions(
    channel: discord.TextChannel,
    member: discord.Member | None,
) -> list[str]:
    if member is None:
        return []
    permissions = channel.permissions_for(member)
    return [
        label
        for attribute, label in REQUIRED_CHANNEL_PERMS.items()
        if not getattr(permissions, attribute, False)
    ]


def missing_permissions_message(
    channel: discord.TextChannel,
    missing: list[str],
) -> str:
    return (
        f"SniperPlug cannot post in {channel.mention} yet.\n\n"
        "Missing channel permissions:\n"
        + "\n".join(f"• {permission}" for permission in missing)
        + "\n\nFix those permissions, then run `/setup_sniperplug_here` again in this channel."
    )
