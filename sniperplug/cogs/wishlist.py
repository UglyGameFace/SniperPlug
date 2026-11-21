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

Adding an item turns personal DM alerts on with the user's saved
settings when they are off — a wishlist that never DMs anyone is useless — but only
after proving the DM can be delivered, the same probe-then-save pattern
``/walmart my-alerts`` uses. If DMs are closed, the item is still saved and
the reply says plainly that alerts stay off until DMs are allowed.

An item can carry a target price (``/wishlist add item target_price:150``):
the DM then fires only at or under that price. Re-adding the same item
with a new target updates it in place — that is how a target is changed.
An item can likewise carry a size (``size:10.5``): the DM then fires only
when the deal's proven variant size is that size, and a deal whose size
is not proven does not DM at all.
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
    WishlistEntry,
    add_user_wishlist_item,
    clear_user_wishlist,
    list_user_wishlist_entries,
    remove_user_wishlist_item,
)

GROUP_DESCRIPTION = (
    "Name items you want — get a DM only if the broad scan finds one "
    "as a price-checked deal."
)

MAX_TARGET_PRICE_DOLLARS = 1_000_000


def _money(cents: int) -> str:
    if cents % 100 == 0:
        return f"${cents // 100:,}"
    return f"${cents / 100:,.2f}"


def _target_suffix(entry: WishlistEntry) -> str:
    if entry.target_price_cents is None:
        return ""
    return f" — under {_money(entry.target_price_cents)}"


def _size_suffix(entry: WishlistEntry) -> str:
    if entry.size_text is None:
        return ""
    return f" — size {entry.size_text}"


def _wishlist_lines(items: tuple[WishlistEntry, ...]) -> str:
    if not items:
        return "Your wishlist is empty."
    return "\n".join(
        f"{index}. {entry}{_target_suffix(entry)}{_size_suffix(entry)}"
        for index, entry in enumerate(items, 1)
    )


def _dm_status_line(preference: DmDealAlertPreference) -> str:
    if preference.enabled:
        return "Personal deal DMs are **on** — wishlist matches will reach your DMs."
    return (
        "Personal deal DMs are **off** — wishlist matches can't reach you until "
        "they're on. Open **My alerts** in the menu to turn them on."
    )


class WishlistCog(commands.GroupCog, group_name="wishlist", group_description=GROUP_DESCRIPTION):
    """Per-user wishlist: add / remove / list / clear."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # -- add -----------------------------------------------------------

    @app_commands.command(name="add", description="Add an item to your wishlist. I'll DM you if the bot's broad scan finds it as a price-checked deal.")
    @app_commands.describe(
        item="Plain product name, for example: iphone 11, dyson airwrap, rtx 5070.",
        target_price="Optional: only DM me at or under this price, for example 150.",
        size="Optional: only DM me for this size, for example 10, 10.5, 3Y, XL.",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        item: str,
        target_price: float | None = None,
        size: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        user_id = int(interaction.user.id)
        target_cents: int | None = None
        if target_price is not None:
            if not 0 < target_price <= MAX_TARGET_PRICE_DOLLARS:
                await interaction.followup.send(
                    "That target price doesn't look right — give a normal price "
                    "above $0, like `target_price:150`, or leave it out entirely.",
                    ephemeral=True,
                )
                return
            target_cents = int(round(target_price * 100))
        result = await add_user_wishlist_item(
            self.bot.db,
            user_id,
            item,
            target_price_cents=target_cents,
            size_text=size,
        )
        if result.updated:
            conditions = []
            if result.target_price_cents is not None:
                conditions.append(
                    "the checked price is at or under "
                    f"${result.target_price_cents / 100:,.2f}"
                )
            if result.size_text is not None:
                conditions.append(f"the proven size is {result.size_text}")
            await interaction.followup.send(
                f"🔄 Updated **{result.item}** — I'll only DM you when "
                + " and ".join(conditions)
                + ".",
                ephemeral=True,
            )
            return
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
                if result.target_price_cents is not None:
                    message += (
                        f" Its target is ${result.target_price_cents / 100:,.2f} — "
                        "re-add with a new `target_price` to change it."
                    )
                if result.size_text is not None:
                    message += (
                        f" Its size is {result.size_text} — "
                        "re-add with a new `size` to change it."
                    )
            else:  # cap
                message = (
                    f"Your wishlist is full ({MAX_WISHLIST_ITEMS_PER_USER} items). "
                    "Remove one with `/wishlist remove` before adding another."
                )
            await interaction.followup.send(message, ephemeral=True)
            return

        target_note = ""
        if result.target_price_cents is not None:
            target_note = (
                f" Target set: I'll only DM you at or under "
                f"${result.target_price_cents / 100:,.2f}."
            )
        size_note = ""
        if result.size_text is not None:
            size_note = (
                f" Size set: I'll only DM you for size {result.size_text}."
            )
        preference = await get_dm_deal_alert_preference(self.bot.db, user_id)
        dm_note = ""
        if not preference.enabled:
            enabled, dm_note = await self._enable_dm_alerts(interaction, preference)
            if not enabled:
                await interaction.followup.send(
                    f"✅ Added **{result.item}** to your wishlist "
                    f"({result.count}/{MAX_WISHLIST_ITEMS_PER_USER}).{target_note}{size_note}\n\n{dm_note}",
                    ephemeral=True,
                )
                return
        await interaction.followup.send(
            f"✅ Added **{result.item}** to your wishlist "
            f"({result.count}/{MAX_WISHLIST_ITEMS_PER_USER}).{target_note}{size_note}\n\n"
            "I'll DM you if the bot's broad scan finds it first as a deal "
            "(price checked, at least 20% off). "
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
        """Prove DM delivery, then enable personal alerts.

        Returns (enabled, note). Mirrors the probe-then-save pattern in
        ``DmDealAlertsCog.dm_deals``: never enable a stream that cannot
        reach the user. The copy quotes the preference's own summary
        sentence so it can never drift from the settings actually saved.
        """

        enabled_preference = replace(
            preference, enabled=True, failure_count=0, last_error=""
        )
        embed = discord.Embed(
            title="✅ SniperPlug wishlist alerts are on",
            description=(
                "You added an item to your wishlist, so your personal deal "
                f"DMs are now on. {enabled_preference.summary_sentence()} "
                "A wishlist match only happens when the bot's broad scan "
                "finds your item first.\n\n"
                "Change them anytime in /menu → My alerts."
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
                "so your personal deal DMs are still off — your wishlist item is saved. "
                "Try again from **My alerts** in the menu in a little while."
            )
        await save_dm_deal_alert_preference(self.bot.db, enabled_preference)
        return True, (
            "Your personal deal DMs were off, so I turned them on — "
            f"{enabled_preference.summary_sentence()}"
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
        items = await list_user_wishlist_entries(self.bot.db, user_id)
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
                "Add with /wishlist add • Matches need a checked price and at least 20% off, "
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
