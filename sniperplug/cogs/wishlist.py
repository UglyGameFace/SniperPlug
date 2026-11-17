"""Standalone ``/wishlist`` command group.

A wishlist is the plain-words version of personal deal alerts: a user names
the exact items they want (``iphone 11``, ``dyson airwrap``) instead of
picking from the 31 curated product profiles or setting broad category
filters. Four small commands: add, remove, list, clear.

Deliberately a standalone group, NOT a ``/walmart`` subcommand. The
``/walmart`` group routes through a ``_DELEGATES`` map that must name a cog
``bot.py`` actually loads, and that map is mid-fix on another branch; wiring
a new subcommand through it would couple this feature to that fix and to
the exact-set ``/walmart`` surface test. A wishlist is also
retailer-agnostic (it matches Walmart, HP and Target verified deals), so it
does not belong under a Walmart-only group.

Adding an item turns personal DM alerts on with the usual Smart defaults
when they are off — a wishlist that never DMs anyone is useless — but only
after proving the DM can be delivered, the same probe-then-save pattern
``/walmart my-alerts`` uses. If DMs are closed, the item is still saved and
the reply says plainly that alerts stay off until DMs are allowed.
"""

from __future__ import annotations

from dataclasses import replace

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.services.dm_deal_alerts import (
    DmDealAlertPreference,
    get_dm_deal_alert_preference,
    save_dm_deal_alert_preference,
)
from sniperplug.services.user_wishlist import (
    MAX_WISHLIST_ITEMS_PER_USER,
    WISHLIST_SCAN_NOTE,
    add_user_wishlist_item,
    clear_user_wishlist,
    list_user_wishlist,
    remove_user_wishlist_item,
)

GROUP_DESCRIPTION = (
    "Name items you want — get a DM only if the broad scan finds one "
    "as a verified deal."
)


def _wishlist_lines(items: tuple[str, ...]) -> str:
    if not items:
        return "Your wishlist is empty."
    return "\n".join(f"{index}. {item}" for index, item in enumerate(items, 1))


def _dm_status_line(preference: DmDealAlertPreference) -> str:
    if preference.enabled:
        return "Personal deal DMs are **on** — wishlist matches will reach your DMs."
    return (
        "Personal deal DMs are **off** — wishlist matches cannot reach you until "
        "they are on. Run `/walmart my-alerts action:Enable or update with typed options`."
    )


class WishlistCog(commands.GroupCog, group_name="wishlist", group_description=GROUP_DESCRIPTION):
    """Per-user wishlist: add / remove / list / clear."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- add -----------------------------------------------------------

    @app_commands.command(name="add", description="Add an item to your wishlist. I'll DM you if the bot's broad scan finds it as a verified deal.")
    @app_commands.describe(item="Plain product name, for example: iphone 11, dyson airwrap, rtx 5070.")
    async def add(self, interaction: discord.Interaction, item: str) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = int(interaction.user.id)
        result = await add_user_wishlist_item(self.bot.db, user_id, item)
        if not result.added:
            if result.reason == "empty":
                message = "Type the item you want — for example `/wishlist add item:iphone 11`."
            elif result.reason == "too_long":
                message = (
                    f"Keep it to a short product name (8 words max). "
                    f"`{result.item}` is too long to match reliably."
                )
            elif result.reason == "duplicate":
                message = f"**{result.item}** is already on your wishlist."
            else:  # cap
                message = (
                    f"Your wishlist is full ({MAX_WISHLIST_ITEMS_PER_USER} items). "
                    "Remove one with `/wishlist remove` before adding another."
                )
            await interaction.followup.send(message, ephemeral=True)
            return

        preference = await get_dm_deal_alert_preference(self.bot.db, user_id)
        dm_note = ""
        if not preference.enabled:
            enabled, dm_note = await self._enable_dm_alerts(interaction, preference)
            if not enabled:
                await interaction.followup.send(
                    f"✅ Added **{result.item}** to your wishlist "
                    f"({result.count}/{MAX_WISHLIST_ITEMS_PER_USER}).\n\n{dm_note}",
                    ephemeral=True,
                )
                return
        await interaction.followup.send(
            f"✅ Added **{result.item}** to your wishlist "
            f"({result.count}/{MAX_WISHLIST_ITEMS_PER_USER}).\n\n"
            "I'll DM you if the bot's broad scan finds it first as a verified deal "
            "(exact price proof, at least 20% off). "
            f"{WISHLIST_SCAN_NOTE} "
            "See everything with `/wishlist list`."
            + (f"\n\n{dm_note}" if dm_note else ""),
            ephemeral=True,
        )

    @add.error
    async def add_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        await self._send_error(interaction, f"Adding to your wishlist failed: `{type(error).__name__}`")

    async def _enable_dm_alerts(
        self,
        interaction: discord.Interaction,
        preference: DmDealAlertPreference,
    ) -> tuple[bool, str]:
        """Prove DM delivery, then enable personal alerts with Smart defaults.

        Returns (enabled, note). Mirrors the probe-then-save pattern in
        ``DmDealAlertsCog.dm_deals``: never enable a stream that cannot
        reach the user.
        """

        embed = discord.Embed(
            title="✅ SniperPlug wishlist alerts are on",
            description=(
                "You added an item to your wishlist, so your personal deal DMs "
                "are now on with the usual Smart settings "
                "(exact verified deals, at least 20% off, daily cap 25). "
                "You will only hear about wishlist matches and other deals "
                "that pass those filters — and a wishlist match only happens "
                "when the bot's broad scan finds your item first.\n\n"
                "Pause anytime with `/walmart my-alerts action:Pause alerts`."
            ),
            color=discord.Color.green(),
        )
        try:
            await interaction.user.send(embed=embed)
        except discord.Forbidden:
            return False, (
                "⚠️ I could not DM you, so your personal deal DMs are still off — "
                "your wishlist item is saved, but matches cannot reach you until "
                "you allow direct messages from server members for a server we share."
            )
        except discord.HTTPException as error:
            return False, (
                f"⚠️ Discord could not deliver the confirmation DM (`{type(error).__name__}`), "
                "so your personal deal DMs are still off — your wishlist item is saved, "
                "try `/walmart my-alerts action:Enable or update with typed options` again shortly."
            )
        await save_dm_deal_alert_preference(
            self.bot.db,
            replace(preference, enabled=True, failure_count=0, last_error=""),
        )
        return True, (
            "Your personal deal DMs were off, so I turned them on with the usual "
            "Smart settings — pause anytime with `/walmart my-alerts action:Pause alerts`."
        )

    # -- remove --------------------------------------------------------

    @app_commands.command(name="remove", description="Remove an item from your wishlist.")
    @app_commands.describe(item="The wishlist item to remove, for example: iphone 11.")
    async def remove(self, interaction: discord.Interaction, item: str) -> None:
        await interaction.response.defer(ephemeral=True)
        removed, display = await remove_user_wishlist_item(
            self.bot.db, int(interaction.user.id), item
        )
        if removed:
            message = f"🗑️ Removed **{display}** from your wishlist."
        else:
            message = (
                f"**{display}** is not on your wishlist. "
                "Check the exact name with `/wishlist list`."
            )
        await interaction.followup.send(message, ephemeral=True)

    @remove.error
    async def remove_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        await self._send_error(interaction, f"Removing from your wishlist failed: `{type(error).__name__}`")

    # -- list ----------------------------------------------------------

    @app_commands.command(name="list", description="Show your wishlist and whether deal DMs are on.")
    async def list_items(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = int(interaction.user.id)
        items = await list_user_wishlist(self.bot.db, user_id)
        preference = await get_dm_deal_alert_preference(self.bot.db, user_id)
        embed = discord.Embed(
            title="⭐ Your wishlist",
            description=_wishlist_lines(items),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Delivery",
            value=_dm_status_line(preference),
            inline=False,
        )
        embed.set_footer(
            text=(
                f"{len(items)}/{MAX_WISHLIST_ITEMS_PER_USER} items • "
                "Add with /wishlist add • Matches need exact price proof and at least 20% off, "
                "and the item must first be found by the bot's broad scan."
            )
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @list_items.error
    async def list_items_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        await self._send_error(interaction, f"Showing your wishlist failed: `{type(error).__name__}`")

    # -- clear ---------------------------------------------------------

    @app_commands.command(name="clear", description="Remove every item from your wishlist.")
    @app_commands.describe(confirm="Set to true to actually clear your whole wishlist.")
    async def clear(self, interaction: discord.Interaction, confirm: bool = False) -> None:
        await interaction.response.defer(ephemeral=True)
        if not confirm:
            await interaction.followup.send(
                "This removes **every** item from your wishlist. "
                "Run it again with `confirm:true` to clear it for real.",
                ephemeral=True,
            )
            return
        removed = await clear_user_wishlist(self.bot.db, int(interaction.user.id))
        await interaction.followup.send(
            f"🗑️ Cleared your wishlist ({removed} item{'s' if removed != 1 else ''} removed). "
            "Your personal deal DM settings were not changed.",
            ephemeral=True,
        )

    @clear.error
    async def clear_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        await self._send_error(interaction, f"Clearing your wishlist failed: `{type(error).__name__}`")

    @staticmethod
    async def _send_error(interaction: discord.Interaction, message: str) -> None:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
