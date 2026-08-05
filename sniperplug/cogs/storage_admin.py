from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.services.autoscan_history import ensure_autoscan_history_table
from sniperplug.services.deal_category_preferences import get_category_preferences
from sniperplug.services.deal_feedback import ensure_deal_feedback_tables
from sniperplug.services.deal_threshold_settings import get_starting_deal_percent
from sniperplug.services.public_deal_posts import ensure_public_post_tables
from sniperplug.services.storage_maintenance import run_storage_maintenance
from sniperplug.services.walmart_delivery_health import load_walmart_delivery_health


class StorageAdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="storage_health",
        description="Show SniperPlug storage and live delivery counts for this server.",
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def storage_health(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send(
                "Use this in a server so I know which storage rows to count.",
                ephemeral=True,
            )
            return
        embed = await build_storage_health_embed(
            self.bot.db,
            int(interaction.guild_id),
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @storage_health.error
    async def storage_health_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        message = (
            "You need **Manage Server** permission to view storage health."
            if isinstance(error, app_commands.MissingPermissions)
            else f"Storage health hit an error: `{error}`"
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(
        name="storage_maintenance_now",
        description="Run SniperPlug storage cleanup now for stale cache/history rows.",
    )
    @app_commands.describe(confirm="Must be true so cleanup cannot be run by accident.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def storage_maintenance_now(
        self,
        interaction: discord.Interaction,
        confirm: bool = False,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not confirm:
            await interaction.followup.send(
                "Cleanup blocked. Re-run with `confirm:true` to run storage maintenance now.",
                ephemeral=True,
            )
            return
        before = await storage_counts(self.bot.db, interaction.guild_id)
        result = await run_storage_maintenance(self.bot.db)
        after = await storage_counts(self.bot.db, interaction.guild_id)
        embed = discord.Embed(
            title="🧹 Storage Maintenance Complete",
            description=(
                "Cleaned stale operational rows. Product/brand learning summaries "
                "and active vote ledgers are preserved."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(
            name="Cleaned",
            value=format_cleanup_result(result.log_fields()),
            inline=False,
        )
        if interaction.guild_id is not None:
            embed.add_field(
                name="Server rows before",
                value=format_count_rows(before),
                inline=False,
            )
            embed.add_field(
                name="Server rows after",
                value=format_count_rows(after),
                inline=False,
            )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @storage_maintenance_now.error
    async def storage_maintenance_now_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        message = (
            "You need **Manage Server** permission to run storage maintenance."
            if isinstance(error, app_commands.MissingPermissions)
            else f"Storage maintenance hit an error: `{error}`"
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def build_storage_health_embed(db, guild_id: int) -> discord.Embed:
    guild_id = int(guild_id)
    counts = await storage_counts(db, guild_id)
    threshold = await get_starting_deal_percent(db, guild_id)
    category_preferences = await get_category_preferences(db, guild_id)
    walmart_health = await load_walmart_delivery_health(
        db,
        guild_id=guild_id,
        threshold=int(threshold),
        category_preferences=category_preferences,
    )
    embed = discord.Embed(
        title="🗄️ SniperPlug Storage Health",
        description=(
            "Server-specific cache/history/feedback rows plus the live global "
            "Walmart fanout. Zero legacy rows do not prove that discovery stopped. "
            "Use `/storage_maintenance_now confirm:true` only for stale cleanup."
        ),
        color=(
            discord.Color.orange()
            if walmart_health.has_delivery_problem
            else discord.Color.blue()
        ),
    )
    embed.add_field(
        name="Live Walmart pipeline",
        value=walmart_health.storage_line(threshold=int(threshold)),
        inline=False,
    )
    embed.add_field(
        name="Feedback",
        value=format_feedback_counts(counts),
        inline=False,
    )
    embed.add_field(
        name="Deals/cache",
        value=format_deal_counts(counts),
        inline=False,
    )
    embed.add_field(
        name="Legacy per-server scan history",
        value=(
            f"Saved reports: **{counts.get('autoscan_reports', 0)}**\n"
            "Global Walmart delivery is reported above; it does not depend on the "
            "old per-server discovery report table."
        ),
        inline=False,
    )
    embed.set_footer(
        text=(
            "High counts are not automatically bad. This command is visibility, "
            "not proof that a deal should have posted."
        )
    )
    return embed


async def storage_counts(db, guild_id: int | None) -> dict[str, int]:
    await ensure_deal_feedback_tables(db)
    await ensure_public_post_tables(db)
    await ensure_autoscan_history_table(db)
    if guild_id is None:
        return {}
    conn = db.require_conn()
    counts = {
        "feedback_targets": await count_rows(
            conn,
            "guild_deal_feedback_targets",
            guild_id,
        ),
        "feedback_events": await count_rows(
            conn,
            "guild_deal_feedback_events",
            guild_id,
        ),
        "feedback_votes": await count_rows(
            conn,
            "guild_deal_feedback_user_votes",
            guild_id,
        ),
        "feedback_products": await count_rows(
            conn,
            "guild_deal_feedback_summary",
            guild_id,
        ),
        "feedback_brands": await count_rows(
            conn,
            "guild_deal_brand_feedback_summary",
            guild_id,
        ),
        "active_deals": await count_rows(
            conn,
            "guild_active_deal_cache",
            guild_id,
            extra="status = 'active'",
        ),
        "expired_deals": await count_rows(
            conn,
            "guild_active_deal_cache",
            guild_id,
            extra="status = 'expired'",
        ),
        "public_posts_posted": await count_rows(
            conn,
            "guild_public_deal_posts",
            guild_id,
            extra="status = 'posted'",
        ),
        "public_posts_reserved": await count_rows(
            conn,
            "guild_public_deal_posts",
            guild_id,
            extra="status = 'reserved'",
        ),
        "autoscan_reports": await count_rows(
            conn,
            "guild_auto_scan_report_history",
            guild_id,
        ),
    }
    return counts


async def count_rows(
    conn: Any,
    table: str,
    guild_id: int,
    *,
    extra: str | None = None,
) -> int:
    allowed = {
        "guild_deal_feedback_targets",
        "guild_deal_feedback_events",
        "guild_deal_feedback_user_votes",
        "guild_deal_feedback_summary",
        "guild_deal_brand_feedback_summary",
        "guild_active_deal_cache",
        "guild_public_deal_posts",
        "guild_auto_scan_report_history",
    }
    if table not in allowed:
        return 0
    sql = f"SELECT COUNT(*) AS count FROM {table} WHERE guild_id = ?"
    if extra:
        sql += f" AND {extra}"
    cursor = await conn.execute(sql, (guild_id,))
    row = await cursor.fetchone()
    return int(row["count"] if row and row["count"] is not None else 0)


def format_feedback_counts(counts: dict[str, int]) -> str:
    return (
        f"Restart-safe targets: **{counts.get('feedback_targets', 0)}**\n"
        f"Raw feedback events: **{counts.get('feedback_events', 0)}**\n"
        f"Active unique votes: **{counts.get('feedback_votes', 0)}**\n"
        f"Product summaries: **{counts.get('feedback_products', 0)}**\n"
        f"Brand summaries: **{counts.get('feedback_brands', 0)}**"
    )


def format_deal_counts(counts: dict[str, int]) -> str:
    return (
        f"Active cached deals: **{counts.get('active_deals', 0)}**\n"
        f"Expired cached deals: **{counts.get('expired_deals', 0)}**\n"
        f"Public posts: **{counts.get('public_posts_posted', 0)}**\n"
        f"Reserved post slots: **{counts.get('public_posts_reserved', 0)}**"
    )


def format_cleanup_result(fields: dict[str, int]) -> str:
    return "\n".join(
        f"`{key}`: **{value}**"
        for key, value in fields.items()
    )


def format_count_rows(counts: dict[str, int]) -> str:
    if not counts:
        return "No server-specific counts available."
    return (
        f"feedback targets/events/votes: **{counts.get('feedback_targets', 0)} / "
        f"{counts.get('feedback_events', 0)} / {counts.get('feedback_votes', 0)}**\n"
        f"active/expired cache: **{counts.get('active_deals', 0)} / "
        f"{counts.get('expired_deals', 0)}**\n"
        f"posted/reserved public posts: **{counts.get('public_posts_posted', 0)} / "
        f"{counts.get('public_posts_reserved', 0)}**\n"
        f"legacy autoscan reports: **{counts.get('autoscan_reports', 0)}**"
    )
