from __future__ import annotations

from typing import Any, Literal

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.services.deal_feedback import ensure_deal_feedback_tables


RESET_SCOPES = ("all", "products", "brands", "events")


class DealFeedbackAdminCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="feedback_learning_status", description="Show what SniperPlug learned from deal feedback buttons.")
    @app_commands.describe(limit="How many boosted/penalized products and brands to show.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def feedback_learning_status(self, interaction: discord.Interaction, limit: app_commands.Range[int, 3, 10] = 5) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which learning data to show.", ephemeral=True)
            return
        embed = await build_feedback_learning_status_embed(self.bot.db, interaction.guild_id, limit=int(limit))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @feedback_learning_status.error
    async def feedback_learning_status_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = "You need **Manage Server** permission to view feedback learning." if isinstance(error, app_commands.MissingPermissions) else f"Feedback learning status hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="feedback_learning_reset", description="Reset SniperPlug feedback learning for this server.")
    @app_commands.describe(
        scope="What to clear: all, products, brands, or raw feedback events.",
        confirm="Must be true so this cannot be clicked by accident.",
    )
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="Everything", value="all"),
            app_commands.Choice(name="Product learning only", value="products"),
            app_commands.Choice(name="Brand learning only", value="brands"),
            app_commands.Choice(name="Raw feedback click history only", value="events"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def feedback_learning_reset(
        self,
        interaction: discord.Interaction,
        scope: app_commands.Choice[str],
        confirm: bool = False,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which learning data to reset.", ephemeral=True)
            return
        scope_value = normalize_reset_scope(scope.value)
        if not confirm:
            await interaction.followup.send(
                f"Reset blocked. Re-run with `confirm:true` to clear **{scope_value}** feedback learning. Example: `/feedback_learning_reset scope:{scope_value} confirm:true`",
                ephemeral=True,
            )
            return
        result = await reset_feedback_learning(self.bot.db, interaction.guild_id, scope=scope_value)
        embed = discord.Embed(
            title="🧹 Feedback Learning Reset",
            description=f"Cleared **{scope_value}** feedback learning for this server.",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Deleted rows", value="\n".join(f"`{name}`: **{count}**" for name, count in result.items()), inline=False)
        embed.set_footer(text="Existing public buttons can still collect new feedback after this reset.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @feedback_learning_reset.error
    async def feedback_learning_reset_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = "You need **Manage Server** permission to reset feedback learning." if isinstance(error, app_commands.MissingPermissions) else f"Feedback learning reset hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def build_feedback_learning_status_embed(db, guild_id: int, *, limit: int = 5) -> discord.Embed:
    await ensure_deal_feedback_tables(db)
    total_events = await count_feedback_events(db, guild_id)
    top_products = await feedback_rows(db, guild_id, table="guild_deal_feedback_summary", positive=True, limit=limit)
    bad_products = await feedback_rows(db, guild_id, table="guild_deal_feedback_summary", positive=False, limit=limit)
    top_brands = await feedback_rows(db, guild_id, table="guild_deal_brand_feedback_summary", positive=True, limit=limit)
    bad_brands = await feedback_rows(db, guild_id, table="guild_deal_brand_feedback_summary", positive=False, limit=limit)

    embed = discord.Embed(
        title="🧠 Feedback Learning Status",
        description="Shows what SniperPlug is currently boosting or penalizing from feedback buttons.",
        color=discord.Color.blue(),
    )
    embed.add_field(name="Feedback events", value=f"Total saved clicks: **{total_events}**", inline=False)
    embed.add_field(name="📈 Boosted products", value=format_product_rows(top_products) or "No boosted products yet.", inline=False)
    embed.add_field(name="📉 Penalized products", value=format_product_rows(bad_products) or "No penalized products yet.", inline=False)
    embed.add_field(name="🏷️ Boosted brands", value=format_brand_rows(top_brands) or "No boosted brands yet.", inline=False)
    embed.add_field(name="🚫 Penalized brands", value=format_brand_rows(bad_brands) or "No penalized brands yet.", inline=False)
    embed.set_footer(text="Feedback affects auto-scan ranking, but threshold, confidence, duplicate, and proof gates still protect public posts. Use /feedback_learning_reset if learning goes sideways.")
    return embed


async def reset_feedback_learning(db, guild_id: int, *, scope: str) -> dict[str, int]:
    await ensure_deal_feedback_tables(db)
    conn = db.require_conn()
    scope_value = normalize_reset_scope(scope)
    deleted: dict[str, int] = {}

    async def delete_from(table: str) -> None:
        cursor = await conn.execute(f"DELETE FROM {table} WHERE guild_id = ?", (guild_id,))
        deleted[table] = max(0, int(getattr(cursor, "rowcount", 0) or 0))

    if scope_value in {"all", "products"}:
        await delete_from("guild_deal_feedback_summary")
    if scope_value in {"all", "brands"}:
        await delete_from("guild_deal_brand_feedback_summary")
    if scope_value in {"all", "events"}:
        await delete_from("guild_deal_feedback_events")
    if scope_value == "all":
        # Keep active button targets only when not doing a full wipe. Full reset
        # intentionally removes old target tokens so stale public posts stop
        # feeding the old learning batch after a hard reset.
        await delete_from("guild_deal_feedback_targets")

    await conn.commit()
    return deleted


def normalize_reset_scope(scope: str) -> Literal["all", "products", "brands", "events"]:
    value = str(scope or "all").strip().lower()
    if value not in RESET_SCOPES:
        return "all"
    return value  # type: ignore[return-value]


async def count_feedback_events(db, guild_id: int) -> int:
    conn = db.require_conn()
    cursor = await conn.execute("SELECT COUNT(*) AS count FROM guild_deal_feedback_events WHERE guild_id = ?", (guild_id,))
    row = await cursor.fetchone()
    return int(row["count"] if row and row["count"] is not None else 0)


async def feedback_rows(db, guild_id: int, *, table: str, positive: bool, limit: int) -> list[dict[str, Any]]:
    if table not in {"guild_deal_feedback_summary", "guild_deal_brand_feedback_summary"}:
        return []
    conn = db.require_conn()
    op = ">" if positive else "<"
    order = "DESC" if positive else "ASC"
    cursor = await conn.execute(
        f"""
        SELECT * FROM {table}
        WHERE guild_id = ? AND total_score {op} 0
        ORDER BY total_score {order}, last_action_at DESC
        LIMIT ?
        """,
        (guild_id, max(1, min(int(limit), 10))),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


def format_product_rows(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in rows:
        title = clean_inline(str(row.get("title") or "deal"), max_len=58)
        score = int(row.get("total_score") or 0)
        good = int(row.get("good_count") or 0)
        bad = int(row.get("bad_count") or 0) + int(row.get("bad_brand_count") or 0) + int(row.get("weak_count") or 0)
        flip = int(row.get("flip_count") or 0)
        lines.append(f"`{score:+}` **{title}** — 👍 {good} • 💰 {flip} • 👎 {bad}")
    return "\n".join(lines[:10])


def format_brand_rows(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in rows:
        brand = clean_inline(str(row.get("brand_hint") or "unknown"), max_len=32)
        retailer = clean_inline(str(row.get("retailer") or "store"), max_len=18)
        score = int(row.get("total_score") or 0)
        good = int(row.get("good_count") or 0)
        bad = int(row.get("bad_count") or 0) + int(row.get("bad_brand_count") or 0) + int(row.get("weak_count") or 0)
        flip = int(row.get("flip_count") or 0)
        lines.append(f"`{score:+}` **{brand}** `{retailer}` — 👍 {good} • 💰 {flip} • 👎 {bad}")
    return "\n".join(lines[:10])


def clean_inline(value: str, *, max_len: int) -> str:
    text = " ".join(str(value or "").replace("`", "'").split())
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"
