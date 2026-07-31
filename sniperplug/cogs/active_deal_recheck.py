from __future__ import annotations

import asyncio
from collections import Counter

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.providers.registry import provider_registry
from sniperplug.services.public_deal_posts import ensure_public_post_tables
from sniperplug.services.walmart_deal_recheck import persist_walmart_recheck, recheck_walmart_observation
from sniperplug.services.walmart_recheck_audit import record_walmart_recheck_attempt


BATCH_RECHECK_MAX_ITEMS = 10
BATCH_RECHECK_CONCURRENCY = 2
BATCH_RECHECK_TIMEOUT_SECONDS = 25
_NON_PERSISTED_STATUSES = {"error", "identity_missing", "provider_unsupported"}


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
        await record_recheck_attempt(
            self.bot.db,
            interaction,
            row,
            result,
            trigger_source="slash_single",
        )
        if result.status not in _NON_PERSISTED_STATUSES:
            await persist_walmart_recheck(self.bot.db, interaction.guild_id, str(row["active_key"]), result)

        await interaction.followup.send(embed=build_recheck_embed(row, result), ephemeral=True)

    @app_commands.command(name="active_deals_recheck", description="Safely recheck several recent Walmart observations with bounded API concurrency.")
    @app_commands.describe(limit="How many recent Walmart observations to recheck. Maximum 10.")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def active_deals_recheck(
        self,
        interaction: discord.Interaction,
        limit: app_commands.Range[int, 1, BATCH_RECHECK_MAX_ITEMS] = 5,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which deal cache to recheck.", ephemeral=True)
            return

        provider = provider_registry.get("walmart")
        if provider is None:
            await interaction.followup.send("The Walmart provider is not registered in this bot process.", ephemeral=True)
            return

        rows = await list_recent_walmart_observations(self.bot.db, interaction.guild_id, int(limit))
        if not rows:
            await interaction.followup.send("There are no recent Walmart observations to recheck. Run `/deals`, `/hunt`, or `/discover` first.", ephemeral=True)
            return

        results = await recheck_walmart_batch(
            provider,
            rows,
            concurrency=BATCH_RECHECK_CONCURRENCY,
            timeout_seconds=BATCH_RECHECK_TIMEOUT_SECONDS,
        )
        for row, result in results:
            await record_recheck_attempt(
                self.bot.db,
                interaction,
                row,
                result,
                trigger_source="slash_batch",
            )
            if result.status not in _NON_PERSISTED_STATUSES and result.status != "timeout":
                await persist_walmart_recheck(self.bot.db, interaction.guild_id, str(row["active_key"]), result)

        await interaction.followup.send(embed=build_batch_recheck_embed(results), ephemeral=True)


async def record_recheck_attempt(db, interaction: discord.Interaction, row: dict, result, *, trigger_source: str) -> None:
    if interaction.guild_id is None:
        return
    user = getattr(interaction, "user", None)
    await record_walmart_recheck_attempt(
        db,
        interaction.guild_id,
        row,
        result,
        trigger_source=trigger_source,
        actor_user_id=getattr(user, "id", None),
        actor_name=str(user) if user is not None else None,
    )


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


async def list_recent_walmart_observations(db, guild_id: int, limit: int) -> list[dict]:
    await ensure_public_post_tables(db)
    conn = db.require_conn()
    safe_limit = max(1, min(int(limit), BATCH_RECHECK_MAX_ITEMS))
    cursor = await conn.execute(
        """
        SELECT active_key, retailer, title, url, current_price, discount, score, source_label, status, first_seen_at, last_seen_at
        FROM guild_active_deal_cache
        WHERE guild_id = ? AND retailer = 'walmart' AND status = 'active'
        ORDER BY last_seen_at DESC
        LIMIT ?
        """,
        (guild_id, safe_limit),
    )
    return [dict(row) for row in await cursor.fetchall()]


async def recheck_walmart_batch(provider, rows: list[dict], *, concurrency: int, timeout_seconds: int):
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    async def run_one(row: dict):
        async with semaphore:
            try:
                result = await asyncio.wait_for(
                    recheck_walmart_observation(provider, row),
                    timeout=max(1, int(timeout_seconds)),
                )
            except asyncio.TimeoutError:
                from sniperplug.services.walmart_deal_recheck import WalmartRecheckResult

                result = WalmartRecheckResult(
                    status="timeout",
                    old_price=_float_or_none(row.get("current_price")),
                    old_discount=_float_or_none(row.get("discount")),
                    message=f"Walmart detail recheck exceeded {timeout_seconds}s. The cached row was left unchanged.",
                )
            return row, result

    return list(await asyncio.gather(*(run_one(row) for row in rows)))


def build_recheck_embed(row: dict, result) -> discord.Embed:
    colors = {
        "unchanged": discord.Color.green(),
        "deal_improved": discord.Color.green(),
        "promotion_verified": discord.Color.green(),
        "price_changed": discord.Color.orange(),
        "deal_weakened": discord.Color.orange(),
        "discount_unproven": discord.Color.red(),
        "discount_gone": discord.Color.red(),
        "unavailable": discord.Color.red(),
        "identity_mismatch": discord.Color.red(),
    }
    labels = {
        "unchanged": "Verified unchanged",
        "deal_improved": "Deal improved",
        "deal_weakened": "Deal weakened",
        "promotion_verified": "Promotion still verified",
        "discount_unproven": "Old discount no longer proven",
        "discount_gone": "Discount gone",
        "price_changed": "Price changed",
        "unavailable": "Unavailable",
        "identity_mismatch": "Identity mismatch blocked",
        "identity_missing": "Missing item identity",
        "provider_unsupported": "Provider unsupported",
        "error": "Recheck error",
        "timeout": "Recheck timeout",
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
    embed.add_field(name="Cached markdown", value=percent(result.old_discount), inline=True)
    embed.add_field(name="Verified markdown", value=percent(result.current_discount), inline=True)
    embed.add_field(name="Reference price", value=money(result.reference_price), inline=True)
    candidate = result.candidate
    if candidate is not None:
        embed.add_field(name="Seller", value=str(getattr(candidate, "seller_name", None) or "Not returned")[:1024], inline=True)
        embed.add_field(name="Variant", value=str(getattr(candidate, "variant_label", None) or "Not returned")[:1024], inline=True)
        embed.add_field(name="Availability", value=str(getattr(candidate, "stock_status", None) or "Not returned")[:1024], inline=True)
    url = str(row.get("url") or "")
    if url.startswith("http"):
        embed.add_field(name="Retailer page", value=f"[Open and verify in Walmart]({url})", inline=False)
    embed.set_footer(text="Fresh markdown percentages require current Walmart reference-price proof. Missing proof clears the old claim instead of guessing.")
    return embed


def build_batch_recheck_embed(results) -> discord.Embed:
    counts = Counter(result.status for _, result in results)
    embed = discord.Embed(
        title="Walmart Batch Recheck",
        description=(
            f"Rechecked **{len(results)}** recent observations with at most **{BATCH_RECHECK_CONCURRENCY}** provider calls running together. "
            "Errors and timeouts leave cached rows unchanged; stale discount claims are cleared."
        ),
        color=discord.Color.orange() if any(counts.get(key) for key in ("price_changed", "deal_weakened")) else discord.Color.green(),
    )
    summary_order = (
        "deal_improved",
        "unchanged",
        "promotion_verified",
        "price_changed",
        "deal_weakened",
        "discount_unproven",
        "discount_gone",
        "unavailable",
        "identity_mismatch",
        "identity_missing",
        "error",
        "timeout",
    )
    summary = " • ".join(f"{status.replace('_', ' ').title()}: **{counts[status]}**" for status in summary_order if counts.get(status))
    embed.add_field(name="Results", value=summary or "No results", inline=False)
    for row, result in results[:BATCH_RECHECK_MAX_ITEMS]:
        price_text = money(result.current_price) if result.current_price is not None else money(result.old_price)
        markdown_text = percent(result.current_discount)
        embed.add_field(
            name=str(row.get("title") or "Unknown Walmart item")[:80],
            value=f"**{result.status.replace('_', ' ').title()}** • {price_text} • Markdown: {markdown_text}\n{result.message[:650]}",
            inline=False,
        )
    embed.set_footer(text="Batch rechecks are owner-triggered, limited to 10 items, and never use SerpApi.")
    return embed


def money(value) -> str:
    if value is None:
        return "N/A"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "N/A"


def percent(value) -> str:
    if value is None:
        return "Not proven"
    try:
        return f"{float(value):.0f}%"
    except (TypeError, ValueError):
        return "Not proven"


def _float_or_none(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
