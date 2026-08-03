from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.services.public_alert_config import (
    get_public_alert_config,
    set_public_alert_config,
)
from sniperplug.services.public_posting import normalize_retailer_key
from sniperplug.services.target_locations import (
    TargetLocationContext,
    clear_target_location,
    get_guild_target_location,
    get_user_target_location,
    save_guild_target_location,
    save_user_target_location,
)
from sniperplug.target_watcher.client import TargetRedSkyClient
from sniperplug.target_watcher.config import TargetWatcherSettings
from sniperplug.target_watcher.stores import TargetStore


class TargetLocationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

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
        stores, error = await self._find_stores(zip_code)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        view = TargetStoreSelectView(
            db=self.bot.db,
            requester_id=int(interaction.user.id),
            scope_type="guild",
            scope_id=int(interaction.guild_id),
            requested_zip=clean_zip(zip_code),
            stores=stores,
        )
        current = await get_guild_target_location(
            self.bot.db,
            int(interaction.guild_id),
        )
        embed = store_picker_embed(
            stores,
            requested_zip=clean_zip(zip_code),
            scope_label="this server",
            current=current,
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

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
        stores, error = await self._find_stores(zip_code)
        if error:
            await interaction.followup.send(error, ephemeral=True)
            return
        view = TargetStoreSelectView(
            db=self.bot.db,
            requester_id=int(interaction.user.id),
            scope_type="user",
            scope_id=int(interaction.user.id),
            requested_zip=clean_zip(zip_code),
            stores=stores,
        )
        current = await get_user_target_location(
            self.bot.db,
            int(interaction.user.id),
        )
        embed = store_picker_embed(
            stores,
            requested_zip=clean_zip(zip_code),
            scope_label="your personal Target DMs",
            current=current,
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

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

    async def _find_stores(
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
                "Target location setup is temporarily unavailable because the owner has not configured the Target access secret.",
            )
        settings = TargetWatcherSettings(
            redsky_api_key=api_key,
            require_remote_database=False,
        )
        try:
            async with TargetRedSkyClient(settings) as client:
                return await client.find_nearby_stores(cleaned, limit=10), ""
        except Exception as error:
            return (
                (),
                "Target did not return a usable nearby-store list for that ZIP. "
                f"Nothing was saved. `{type(error).__name__}: {compact(error)}`",
            )

    @target_location.error
    @target_location_clear.error
    async def target_location_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        message = (
            "You need **Manage Server** permission to configure the server's Target location."
            if isinstance(error, app_commands.MissingPermissions)
            else f"Target location setup failed safely: `{type(error).__name__}`"
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


class TargetStoreSelectView(discord.ui.View):
    def __init__(
        self,
        *,
        db: Any,
        requester_id: int,
        scope_type: str,
        scope_id: int,
        requested_zip: str,
        stores: tuple[TargetStore, ...],
    ):
        super().__init__(timeout=180)
        self.db = db
        self.requester_id = requester_id
        self.scope_type = scope_type
        self.scope_id = scope_id
        self.requested_zip = requested_zip
        self.stores = {store.store_id: store for store in stores}
        options = [
            discord.SelectOption(
                label=truncate(store.label, 100),
                description=truncate(store.description, 100),
                value=store.store_id,
            )
            for store in stores[:25]
        ]
        self.store_select = discord.ui.Select(
            placeholder="Choose the exact Target store",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.store_select.callback = self._selected
        self.add_item(self.store_select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.requester_id:
            return True
        await interaction.response.send_message(
            "Only the person who opened this Target picker can use it.",
            ephemeral=True,
        )
        return False

    async def _selected(self, interaction: discord.Interaction) -> None:
        store_id = self.store_select.values[0]
        store = self.stores.get(store_id)
        if store is None:
            await interaction.response.send_message(
                "That Target option expired. Run the location command again.",
                ephemeral=True,
            )
            return
        kwargs = {
            "zip_code": self.requested_zip,
            "store_id": store.store_id,
            "store_name": store.name,
            "address_line": store.address_line,
            "city": store.city,
            "state": store.state,
            "postal_code": store.postal_code,
            "latitude": store.latitude,
            "longitude": store.longitude,
        }
        if self.scope_type == "guild":
            location = await save_guild_target_location(
                self.db,
                guild_id=self.scope_id,
                **kwargs,
            )
            await add_target_retailer(self.db, self.scope_id)
            scope_message = (
                "Local Target alerts are now enabled for this server when public alerts are on."
            )
        else:
            location = await save_user_target_location(
                self.db,
                user_id=self.scope_id,
                **kwargs,
            )
            scope_message = (
                "Your local Target DM filter now uses this exact store."
            )
        self.store_select.disabled = True
        embed = saved_location_embed(location, scope_message=scope_message)
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()


async def add_target_retailer(db: Any, guild_id: int) -> None:
    config = await get_public_alert_config(db, guild_id)
    retailers = list(config.get("retailers") or ())
    if "target" not in {
        normalize_retailer_key(retailer) for retailer in retailers
    }:
        retailers.append("target")
    await set_public_alert_config(
        db,
        guild_id=guild_id,
        enabled=bool(config.get("enabled")),
        retailers=tuple(retailers),
        channel_id=config.get("channel_id"),
    )


async def remove_target_retailer(db: Any, guild_id: int) -> None:
    config = await get_public_alert_config(db, guild_id)
    retailers = tuple(
        retailer
        for retailer in config.get("retailers") or ()
        if normalize_retailer_key(retailer) != "target"
    )
    await set_public_alert_config(
        db,
        guild_id=guild_id,
        enabled=bool(config.get("enabled")),
        retailers=retailers,
        channel_id=config.get("channel_id"),
    )


def store_picker_embed(
    stores: tuple[TargetStore, ...],
    *,
    requested_zip: str,
    scope_label: str,
    current: TargetLocationContext | None,
) -> discord.Embed:
    embed = discord.Embed(
        title="🎯 Choose a Target store",
        description=(
            f"Target returned **{len(stores)}** nearby stores for ZIP `{requested_zip}`. "
            f"Choose the exact store used for {scope_label}. Nothing is saved until you select one."
        ),
        color=discord.Color.red(),
    )
    if current is not None and current.enabled:
        embed.add_field(
            name="Current selection",
            value=format_location(current),
            inline=False,
        )
    embed.add_field(
        name="Why this matters",
        value=(
            "Target price and pickup availability can differ by store. SniperPlug only sends local alerts whose verified store and ZIP match the saved selection."
        ),
        inline=False,
    )
    return embed


def saved_location_embed(
    location: TargetLocationContext,
    *,
    scope_message: str,
) -> discord.Embed:
    embed = discord.Embed(
        title="✅ Target location saved",
        description=scope_message,
        color=discord.Color.green(),
    )
    embed.add_field(name="Store", value=location.store_name, inline=False)
    embed.add_field(
        name="Location",
        value=format_location(location),
        inline=False,
    )
    embed.add_field(
        name="Location-safe delivery",
        value=(
            "Target events from other stores and ZIP codes are blocked. Servers choosing this same store share one watcher location scan."
        ),
        inline=False,
    )
    return embed


def format_location(location: TargetLocationContext) -> str:
    address = ", ".join(
        piece
        for piece in (
            location.address_line,
            location.city,
            location.state,
            location.postal_code,
        )
        if piece
    )
    return (
        f"**{location.store_name}**\n"
        f"{address or 'Address not returned'}\n"
        f"Store `{location.store_id}` • alert ZIP `{location.zip_code}`"
    )


def clean_zip(value: str) -> str:
    return "".join(character for character in str(value or "") if character.isdigit())[:5]


def truncate(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip() + "…"


def compact(error: Exception) -> str:
    return truncate(str(error) or "unknown error", 300)
