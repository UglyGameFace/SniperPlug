from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.providers.registry import provider_registry
from sniperplug.services.public_deal_posts import ensure_public_post_tables
from sniperplug.services.walmart_deal_recheck import persist_walmart_recheck, recheck_walmart_observation


class ActiveDealRecheckCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="active_deal_recheck", description="Recheck one cached Walmart deal through the official item-detail provider.")
    @app_commands.describe(search="Part of the cached title, Walmart URL, or exact active-cache key.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def active_deal_recheck(self, interaction: discord.Interaction, search: str) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which deal cache to recheck.", ephemeral=True)
            return

        clean_search = " ".join(str(search or "").split())
        if len(clean_search) < 3:
            await interaction.followup.send("Enter at least three characters from the cached title or Walmart URL.", ephemeral=True)
            return

        row, match_count = await find_cached_walmart_observation(self.bot.db, interaction.guild_id, clean_search)
        if row is None:
            await interaction.followup.send("No cached Walmart observation matched that search. Open `/active_deals retailer:walmart` and copy more of the title or URL.", ephemeral=True)
            return
        if match_count > 1:
            await interaction.followup.send(
                f"That search matched **{match_count}** Walmart observations. Add more of the title or paste the exact URL so SniperPlug does not recheck the wrong item.",
                ephemeral=True,
            )
            return

        provider = provider_registry.get("walmart")
        if provider is None:
            await interaction.followup.send("The Walmart provider is not registered in this bot process.", ephemeral=True)
            return

        result = await recheck_walmart_observation(provider, row)
        if result.status not in {"error", "identity_missing", "provider_unsupported"}:
            await persist_walmart_recheck(self.bot.db, interaction.guild_id, str(row["active_key"]), result)

        await interaction.followup.send(embed=build_recheck_embed(row, result), ephemeral=True)


async def find_cached_walmart_observation(db, guild_id: int, search: str) -> tuple[dict | None, int]:
    await ensure_public_post_tables(db)
    conn = db.require_conn()
    pattern = f"%{search.lower()}%"
    cursor = await conn.execute(
        """
        SELECT active_key, retailer, title, url, current_price, discount, score, source_label, status, first_seen_at, last_seen_at
        FROM guild_active_deal_cache
        WHERE guild_id = ?
          AND retailer = 'walmart'
          AND (LOWER(title) LIKE ? OR LOWER(url) LIKE ? OR LOWER(active_key) LIKE ?)
        ORDER BY last_seen_at DESC
        LIMIT 3
        """,
        (guild_id, pattern, pattern, pattern),
    )
    rows = [dict(row) for row in await cursor.fetchall()]
    return (rows[0] if len(rows) == 1 else None, len(rows))


def build_recheck_embed(row: dict, result) -> discord.Embed:
    colors = {
        "unchanged": discord.Color.green(),
        "price_changed": discord.Color.orange(),
        "unavailable": discord.Color.red(),
        "identity_mismatch": discord.Color.red(),
    }
    labels = {
        "unchanged": "Verified unchanged",
        "price_changed": "Price changed",
        "unavailable": "Unavailable",
        "identity_mismatch": "Identity mismatch blocked",
        "identity_missing": "Missing item identity",
        "provider_unsupported": "Provider unsupported",
        "error": "Recheck error",
    }
    embed = discord.Embed(
        title=f"Walmart Recheck • {labels.get(result.status, result.status)}",
        description=result.message,
        color=colors.get(result.status, discord.Color.dark_gold()),
    )
    embed.add_field(name="Cached item", value=str(row.get("title") or "Unknown item")[:1024], inline=False)
    embed.add_field(name="Walmart item ID", value=f"`{result.item_id}`" if result.item_id else "Not safely resolved", inline=True)
    embed.add_field(name="Cached price", value=money(result.old_price), inline=True)
    embed.add_field(name="Current price", value=money(result.current_price), inline=True)
    candidate = result.candidate
    if candidate is not None:
        embed.add_field(name="Seller", value=str(getattr(candidate, "seller_name", None) or "Not returned")[:1024], inline=True)
        embed.add_field(name="Variant", value=str(getattr(candidate, "variant_label", None) or "Not returned")[:1024], inline=True)
        embed.add_field(name="Availability", value=str(getattr(candidate, "stock_status", None) or "Not returned")[:1024], inline=True)
    url = str(row.get("url") or "")
    if url.startswith("http"):
        embed.add_field(name="Retailer page", value=f"[Open and verify in Walmart]({url})", inline=False)
    embed.set_footer(text="This rechecks one exact cached Walmart item. It never substitutes a similar search result or another variant.")
    return embed


def money(value) -> str:
    if value is None:
        return "N/A"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "N/A"
