from __future__ import annotations

from typing import Any

import discord

from sniperplug.services.public_alert_config import (
    get_public_alert_config,
    set_public_alert_config,
)
from sniperplug.services.public_posting import normalize_retailer_key
from sniperplug.services.target_locations import (
    TargetLocationContext,
    prune_orphan_target_product_rows,
    save_guild_target_location,
    save_user_target_location,
)
from sniperplug.target_watcher.stores import TargetStore


class TargetStoreSelectView(discord.ui.View):
    """Shared store picker used by the canonical Target setup commands."""

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
            scope_message = "Your local Target DM filter now uses this exact store."
        await prune_orphan_target_product_rows(self.db)
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
