from __future__ import annotations

from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.services.public_deal_posts import ensure_public_post_tables
from sniperplug.services.public_posting import SUPPORTED_RETAILERS, format_retailers, normalize_retailer_key


DEFAULT_STALE_AFTER_HOURS = 24


class ActiveDealsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="active_deals", description="Show deals SniperPlug has recently cached as active.")
    @app_commands.describe(retailer="Optional store filter.", limit="How many cached deals to show. Max 15.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def active_deals(self, interaction: discord.Interaction, retailer: str | None = None, limit: app_commands.Range[int, 1, 15] = 10) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which active deal cache to read.", ephemeral=True)
            return
        key = normalize_retailer_key(retailer) if retailer else None
        if key and key not in SUPPORTED_RETAILERS:
            await interaction.followup.send(f"Unknown retailer `{retailer}`. Supported: {format_retailers(tuple(sorted(SUPPORTED_RETAILERS)))}", ephemeral=True)
            return
        await mark_stale_deals(self.bot.db, interaction.guild_id, stale_after_hours=DEFAULT_STALE_AFTER_HOURS)
        deals = await list_active_deals(self.bot.db, interaction.guild_id, retailer=key, limit=int(limit))
        await interaction.followup.send(embed=build_active_deals_embed(interaction.guild_id, deals, retailer=key, limit=int(limit)), ephemeral=True)

    @app_commands.command(name="active_deals_cleanup", description="Mark old cached deals stale.")
    @app_commands.describe(stale_after_hours="Mark deals stale if not seen again after this many hours.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def active_deals_cleanup(self, interaction: discord.Interaction, stale_after_hours: app_commands.Range[int, 1, 168] = DEFAULT_STALE_AFTER_HOURS) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which cache to clean.", ephemeral=True)
            return
        updated = await mark_stale_deals(self.bot.db, interaction.guild_id, stale_after_hours=int(stale_after_hours))
        await interaction.followup.send(f"Marked **{updated}** cached deal(s) stale if SniperPlug has not seen them again in **{stale_after_hours}h**.", ephemeral=True)


async def list_active_deals(db, guild_id: int, *, retailer: str | None = None, limit: int = 10) -> list[dict]:
    await ensure_public_post_tables(db)
    conn = db.require_conn()
    safe_limit = max(1, min(int(limit), 15))
    if retailer:
        cursor = await conn.execute("SELECT retailer, title, url, current_price, discount, score, source_label, status, first_seen_at, last_seen_at FROM guild_active_deal_cache WHERE guild_id = ? AND retailer = ? AND status = 'active' ORDER BY last_seen_at DESC LIMIT ?", (guild_id, retailer, safe_limit))
    else:
        cursor = await conn.execute("SELECT retailer, title, url, current_price, discount, score, source_label, status, first_seen_at, last_seen_at FROM guild_active_deal_cache WHERE guild_id = ? AND status = 'active' ORDER BY last_seen_at DESC LIMIT ?", (guild_id, safe_limit))
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def active_deal_counts(db, guild_id: int) -> dict[str, int]:
    await ensure_public_post_tables(db)
    conn = db.require_conn()
    cursor = await conn.execute("SELECT retailer, COUNT(*) AS count FROM guild_active_deal_cache WHERE guild_id = ? AND status = 'active' GROUP BY retailer ORDER BY retailer", (guild_id,))
    rows = await cursor.fetchall()
    return {str(row["retailer"]): int(row["count"] or 0) for row in rows}


async def mark_stale_deals(db, guild_id: int, *, stale_after_hours: int = DEFAULT_STALE_AFTER_HOURS) -> int:
    await ensure_public_post_tables(db)
    conn = db.require_conn()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(1, int(stale_after_hours)))
    cursor = await conn.execute("UPDATE guild_active_deal_cache SET status = 'stale' WHERE guild_id = ? AND status = 'active' AND last_seen_at < ?", (guild_id, cutoff.isoformat()))
    await conn.commit()
    return int(getattr(cursor, "rowcount", 0) or 0)


def build_active_deals_embed(guild_id: int, deals: list[dict], *, retailer: str | None, limit: int) -> discord.Embed:
    embed = discord.Embed(title="🟢 Active Deals Cache", description=f"Server: `{guild_id}`\nRetailer: `{retailer or 'all'}`\nShowing up to: **{limit}**\nRecheck before buying because prices can change.", color=discord.Color.green() if deals else discord.Color.dark_gold())
    if not deals:
        embed.add_field(name="No active deals cached yet", value="Run `/discover`, `/hunt`, `/deals`, or wait for enabled auto-scan.", inline=False)
        return embed
    for row in deals[:limit]:
        discount = row.get("discount")
        discount_text = f"{float(discount):.0f}%" if discount is not None else "n/a"
        score = row.get("score") if row.get("score") is not None else "n/a"
        embed.add_field(name=f"{row.get('retailer', 'retailer')} • {trim(str(row.get('title') or 'deal'), 80)}", value=f"Price: **{money(row.get('current_price'))}** • Discount: **{discount_text}** • Score: `{score}`\nSource: `{row.get('source_label') or 'unknown'}`\nLast seen: `{row.get('last_seen_at') or 'unknown'}`\n{row.get('url') or ''}", inline=False)
    embed.set_footer(text="Use /active_deals_cleanup if old deals are hanging around too long.")
    return embed


def money(value) -> str:
    if value is None:
        return "N/A"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "N/A"


def trim(value: str, limit: int) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
