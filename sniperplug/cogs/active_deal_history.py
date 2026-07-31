from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.services.active_deal_history import list_active_deal_history
from sniperplug.services.public_posting import SUPPORTED_RETAILERS, normalize_retailer_key


class ActiveDealHistoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="active_deal_history", description="Review recent cached deal price, discount, and lifecycle changes.")
    @app_commands.describe(
        retailer="Optional store filter.",
        search="Optional title, item key, or event search.",
        limit="How many history rows to show. Maximum 25.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def active_deal_history(
        self,
        interaction: discord.Interaction,
        retailer: str | None = None,
        search: str | None = None,
        limit: app_commands.Range[int, 1, 25] = 10,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which deal history to read.", ephemeral=True)
            return

        retailer_key = normalize_retailer_key(retailer) if retailer else None
        if retailer_key and retailer_key not in SUPPORTED_RETAILERS:
            await interaction.followup.send(f"Unknown retailer `{retailer}`.", ephemeral=True)
            return

        rows = await list_active_deal_history(
            self.bot.db,
            interaction.guild_id,
            retailer=retailer_key,
            search=search,
            limit=int(limit),
        )
        await interaction.followup.send(embed=build_active_deal_history_embed(rows), ephemeral=True)


def build_active_deal_history_embed(rows: list[dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title="Active Deal Lifecycle History",
        description=(
            "Durable cache changes captured by the database itself. This includes verified rechecks and fresh-scan updates; "
            "the event label describes only what actually changed."
        ),
        color=discord.Color.blurple(),
    )
    if not rows:
        embed.add_field(
            name="No recorded changes yet",
            value="History begins when an existing cached row changes price, markdown, or active/stale state.",
            inline=False,
        )
        return embed

    for row in rows[:25]:
        event = str(row.get("event_type") or "cache_changed").replace("_", " ").title()
        old_price = money(row.get("old_price"))
        new_price = money(row.get("new_price"))
        old_discount = percent(row.get("old_discount"))
        new_discount = percent(row.get("new_discount"))
        old_status = str(row.get("old_status") or "unknown")
        new_status = str(row.get("new_status") or "unknown")
        value = (
            f"Price: **{old_price} → {new_price}**\n"
            f"Markdown: **{old_discount} → {new_discount}**\n"
            f"State: `{old_status}` → `{new_status}` • Source: `{str(row.get('source_label') or 'unknown')[:80]}`\n"
            f"Recorded: `{str(row.get('occurred_at') or 'unknown')}`"
        )
        embed.add_field(
            name=f"{event} • {trim(str(row.get('title') or 'Unknown deal'), 72)}",
            value=value,
            inline=False,
        )

    embed.set_footer(text="History is retained for 30 days and capped at 1,000 rows per server.")
    return embed


def money(value: Any) -> str:
    try:
        return f"${float(value):,.2f}" if value is not None else "N/A"
    except (TypeError, ValueError):
        return "N/A"


def percent(value: Any) -> str:
    try:
        return f"{float(value):.0f}%" if value is not None else "Not proven"
    except (TypeError, ValueError):
        return "Not proven"


def trim(value: str, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
