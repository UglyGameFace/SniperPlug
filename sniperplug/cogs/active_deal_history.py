from __future__ import annotations

from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.services.active_deal_history import list_active_deal_history
from sniperplug.services.public_posting import SUPPORTED_RETAILERS, normalize_retailer_key
from sniperplug.services.walmart_recheck_audit import list_walmart_recheck_audit


class ActiveDealHistoryCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="active_deal_history", description="Review cached deal lifecycle changes or Walmart recheck attempts.")
    @app_commands.describe(
        view="Choose lifecycle changes or the Walmart recheck audit.",
        retailer="Optional store filter for lifecycle history.",
        search="Optional title, item key, status, event, or source search.",
        limit="How many history rows to show. Maximum 25.",
    )
    @app_commands.choices(
        view=[
            app_commands.Choice(name="Lifecycle changes", value="lifecycle"),
            app_commands.Choice(name="Walmart recheck audit", value="rechecks"),
        ]
    )
    @app_commands.checks.has_permissions(manage_guild=True)
    async def active_deal_history(
        self,
        interaction: discord.Interaction,
        view: app_commands.Choice[str] | None = None,
        retailer: str | None = None,
        search: str | None = None,
        limit: app_commands.Range[int, 1, 25] = 10,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None:
            await interaction.followup.send("Use this in a server so I know which deal history to read.", ephemeral=True)
            return

        view_key = view.value if view else "lifecycle"
        if view_key == "rechecks":
            rows = await list_walmart_recheck_audit(
                self.bot.db,
                interaction.guild_id,
                search=search,
                limit=int(limit),
            )
            await interaction.followup.send(embed=build_walmart_recheck_audit_embed(rows), ephemeral=True)
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

    embed.set_footer(text="Lifecycle history is retained for 30 days and capped at 1,000 rows per server.")
    return embed


def build_walmart_recheck_audit_embed(rows: list[dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title="Walmart Recheck Audit",
        description=(
            "Every owner-triggered slash recheck attempt is recorded, including unchanged results, reused anti-spam results, "
            "timeouts, errors, and identity blocks. Cache persistence remains separate."
        ),
        color=discord.Color.dark_teal(),
    )
    if not rows:
        embed.add_field(
            name="No recheck attempts recorded yet",
            value="Run `/active_deal_recheck` or `/active_deals_recheck` to create the first audited attempt.",
            inline=False,
        )
        return embed

    for row in rows[:25]:
        status = str(row.get("result_status") or "unknown").replace("_", " ").title()
        source = str(row.get("trigger_source") or "unknown").replace("_", " ")
        actor = str(row.get("actor_name") or row.get("actor_user_id") or "unknown")
        reused = "yes" if int(row.get("reused") or 0) else "no"
        value = (
            f"Price: **{money(row.get('old_price'))} → {money(row.get('new_price'))}**\n"
            f"Markdown: **{percent(row.get('old_discount'))} → {percent(row.get('new_discount'))}**"
            f" • Reference: **{money(row.get('reference_price'))}**\n"
            f"Source: `{source}` • Actor: `{trim(actor, 80)}` • Reused: **{reused}**\n"
            f"Item ID: `{str(row.get('item_id') or 'unresolved')}` • Cache result: `{str(row.get('cache_status') or 'unchanged')}`\n"
            f"Recorded: `{str(row.get('occurred_at') or 'unknown')}`\n"
            f"{trim(str(row.get('message') or ''), 350)}"
        )
        embed.add_field(
            name=f"{status} • {trim(str(row.get('title') or 'Unknown Walmart item'), 68)}",
            value=value,
            inline=False,
        )

    embed.set_footer(text="Recheck audit is retained for 30 days and capped at 2,000 rows per server.")
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
