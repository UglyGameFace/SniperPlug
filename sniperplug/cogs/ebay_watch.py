from __future__ import annotations

from hashlib import sha256
import re

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.ebay_watcher.config import EbayWatcherSettings
from sniperplug.ebay_watcher.models import EbayWatchRule
from sniperplug.ebay_watcher.storage import (
    delete_watch_rule,
    list_watch_rules,
    save_watch_rule,
    seed_default_watch_rules,
    set_watch_rule_enabled,
)
from sniperplug.services.ebay_watcher_health import load_ebay_watcher_health


ACTION_CHOICES = [
    app_commands.Choice(name="Add or update a watch", value="add"),
    app_commands.Choice(name="Watcher status", value="status"),
    app_commands.Choice(name="List active watches", value="list"),
    app_commands.Choice(name="Pause a watch", value="pause"),
    app_commands.Choice(name="Resume a watch", value="resume"),
    app_commands.Choice(name="Remove a watch", value="remove"),
    app_commands.Choice(name="Restore default high-demand watches", value="seed"),
]


class EbayWatchCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="ebay_watch",
        description="Manage the owner's global big-ticket and high-demand eBay watcher.",
    )
    @app_commands.describe(
        action="Add, inspect, pause, resume, remove, or restore watcher rules.",
        query="Keyword search, such as Nintendo Switch 2 or RTX 5090.",
        label="Short name shown in watcher status and alert proof.",
        rule_id="Rule ID shown by the List action.",
        sought_after="Allow this high-demand item below the normal big-ticket floor.",
        min_discount="Verified drop required before an alert.",
        min_reference_price="Lowest trusted prior/market value allowed.",
        conditions="Comma list: new, open_box, certified_refurbished, used_good, etc.",
        interval_minutes="How often eBay discovery reruns this watch.",
        seller="Optional exact eBay seller filter.",
        gtin="Optional exact UPC/EAN/ISBN product identity.",
        epid="Optional exact eBay product ID.",
        category_id="Optional eBay category ID.",
    )
    @app_commands.choices(action=ACTION_CHOICES)
    async def ebay_watch(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        query: app_commands.Range[str, 1, 300] | None = None,
        label: app_commands.Range[str, 1, 120] | None = None,
        rule_id: app_commands.Range[str, 1, 100] | None = None,
        sought_after: bool | None = None,
        min_discount: app_commands.Range[int, 1, 95] | None = None,
        min_reference_price: app_commands.Range[float, 10.0, 100000.0] | None = None,
        conditions: app_commands.Range[str, 1, 400] | None = None,
        interval_minutes: app_commands.Range[int, 1, 1440] | None = None,
        seller: app_commands.Range[str, 1, 100] | None = None,
        gtin: app_commands.Range[str, 1, 50] | None = None,
        epid: app_commands.Range[str, 1, 50] | None = None,
        category_id: app_commands.Range[str, 1, 30] | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        if not await self.bot.is_owner(interaction.user):
            await interaction.followup.send(
                "This controls SniperPlug's shared eBay API budget, so only the bot owner can change it.",
                ephemeral=True,
            )
            return

        selected = str(action.value)
        settings = EbayWatcherSettings.from_env()

        if selected == "status":
            health = await load_ebay_watcher_health(self.bot.db)
            await interaction.followup.send(
                embed=build_ebay_watcher_health_embed(health),
                ephemeral=True,
            )
            return

        if selected == "list":
            rules = await list_watch_rules(self.bot.db)
            await interaction.followup.send(
                embed=build_ebay_watch_rules_embed(rules),
                ephemeral=True,
            )
            return

        if selected == "seed":
            inserted = await seed_default_watch_rules(self.bot.db, settings)
            await interaction.followup.send(
                f"✅ Restored the default big-ticket and high-demand eBay watches. Added **{inserted}** missing rule(s).",
                ephemeral=True,
            )
            return

        clean_rule_id = str(rule_id or "").strip()
        if selected in {"pause", "resume", "remove"}:
            if not clean_rule_id:
                await interaction.followup.send(
                    "Choose the **List active watches** action first, then provide its rule ID.",
                    ephemeral=True,
                )
                return
            if selected == "remove":
                changed = await delete_watch_rule(self.bot.db, clean_rule_id)
                message = (
                    f"🗑️ Removed eBay watch `{clean_rule_id}`."
                    if changed
                    else f"No eBay watch exists with ID `{clean_rule_id}`."
                )
            else:
                enabled = selected == "resume"
                changed = await set_watch_rule_enabled(
                    self.bot.db,
                    clean_rule_id,
                    enabled,
                )
                message = (
                    f"{'▶️ Resumed' if enabled else '⏸️ Paused'} eBay watch `{clean_rule_id}`."
                    if changed
                    else f"No eBay watch exists with ID `{clean_rule_id}`."
                )
            await interaction.followup.send(message, ephemeral=True)
            return

        clean_query = " ".join(str(query or "").split())
        clean_seller = " ".join(str(seller or "").split())
        clean_gtin = re.sub(r"[^A-Za-z0-9]", "", str(gtin or ""))
        clean_epid = re.sub(r"[^A-Za-z0-9]", "", str(epid or ""))
        clean_category = re.sub(r"[^0-9]", "", str(category_id or ""))
        if not any((clean_query, clean_seller, clean_gtin, clean_epid, clean_category)):
            await interaction.followup.send(
                "Add at least one search identity: query, seller, GTIN, ePID, or category ID.",
                ephemeral=True,
            )
            return

        demand = bool(sought_after)
        floor = (
            float(min_reference_price)
            if min_reference_price is not None
            else (
                settings.sought_after_min_reference_price
                if demand
                else settings.big_ticket_min_reference_price
            )
        )
        allowed = (
            normalize_condition_list(conditions)
            if conditions is not None
            else settings.allowed_conditions
        )
        identity = "|".join(
            (
                clean_query.lower(),
                clean_seller.lower(),
                clean_gtin.lower(),
                clean_epid.lower(),
                clean_category,
            )
        )
        generated_id = (
            clean_rule_id
            or f"ebay-custom:{sha256(identity.encode('utf-8')).hexdigest()[:16]}"
        )
        rule = EbayWatchRule(
            rule_id=generated_id,
            label=" ".join(
                str(label or clean_query or clean_gtin or clean_epid or clean_seller).split()
            )[:120],
            query=clean_query,
            category_id=clean_category,
            gtin=clean_gtin,
            epid=clean_epid,
            seller=clean_seller,
            sought_after=demand,
            enabled=True,
            priority=90 if demand else 60,
            min_discount_percent=(
                int(min_discount)
                if min_discount is not None
                else settings.default_min_discount_percent
            ),
            min_reference_price=floor,
            allowed_conditions=allowed,
            min_seller_feedback_percentage=settings.minimum_seller_feedback_percentage,
            min_seller_feedback_score=settings.minimum_seller_feedback_score,
            search_limit=settings.search_limit,
            scan_interval_seconds=(
                int(interval_minutes) * 60
                if interval_minutes is not None
                else settings.default_rule_interval_seconds
            ),
        )
        await save_watch_rule(self.bot.db, rule)
        await interaction.followup.send(
            embed=build_saved_rule_embed(rule),
            ephemeral=True,
        )

    @ebay_watch.error
    async def ebay_watch_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ) -> None:
        message = (
            "eBay watcher settings failed safely; no partial rule was intentionally saved. "
            f"Error: `{type(error).__name__}`"
        )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)



def build_ebay_watcher_health_embed(health) -> discord.Embed:
    embed = discord.Embed(
        title="🩺 eBay watcher status",
        description=health.summary_line(),
        color=discord.Color.green() if health.ok else discord.Color.orange(),
    )
    embed.add_field(
        name="Last successful cycle",
        value=(
            f"`{health.last_successful_cycle_at}`"
            if health.last_successful_cycle_at
            else "No successful cycle recorded yet"
        ),
        inline=False,
    )
    if health.last_error:
        embed.add_field(
            name="Last error",
            value=str(health.last_error)[:1024],
            inline=False,
        )
    embed.add_field(
        name="What is protected",
        value=(
            "Default rules watch both **big-ticket categories/keywords** and "
            "**high-demand exact products**. Every public alert still needs an "
            "exact delivered price, allowed condition, trusted seller, a second "
            "exact-item confirmation, and the watch rule's verified discount."
        ),
        inline=False,
    )
    return embed

def normalize_condition_list(value: str) -> tuple[str, ...]:
    result = []
    for piece in str(value or "").replace(";", ",").split(","):
        condition = re.sub(
            r"_+",
            "_",
            re.sub(r"[^a-z0-9]+", "_", piece.strip().lower()),
        ).strip("_")
        if condition and condition not in result:
            result.append(condition)
    return tuple(result)


def build_ebay_watch_rules_embed(rules: list[EbayWatchRule]) -> discord.Embed:
    embed = discord.Embed(
        title="🎯 eBay watcher rules",
        description=(
            "These rules drive one shared 24/7 worker. Public alerts still require exact "
            "condition/seller/delivered-price proof and each server's own retailer, category, "
            "and discount settings."
        ),
        color=discord.Color.blurple(),
    )
    if not rules:
        embed.add_field(
            name="No watches",
            value="Use `/ebay_watch` with **Add or update a watch** or restore the defaults.",
            inline=False,
        )
        return embed
    for rule in rules[:20]:
        identity = rule.query or rule.gtin or rule.epid or rule.seller or rule.category_id
        embed.add_field(
            name=f"{'▶️' if rule.enabled else '⏸️'} {rule.label[:80]}",
            value=(
                f"ID: `{rule.rule_id}`\n"
                f"Search: `{identity[:180]}`\n"
                f"Type: **{'high-demand' if rule.sought_after else 'big-ticket'}** • "
                f"drop **{rule.min_discount_percent}%+** • reference **${rule.min_reference_price:,.2f}+**\n"
                f"Every **{max(1, rule.scan_interval_seconds // 60)} min** • "
                f"conditions: `{', '.join(rule.allowed_conditions) or 'none'}`"
            )[:1024],
            inline=False,
        )
    if len(rules) > 20:
        embed.set_footer(text=f"Showing 20 of {len(rules)} rules.")
    return embed


def build_saved_rule_embed(rule: EbayWatchRule) -> discord.Embed:
    embed = discord.Embed(
        title="✅ eBay watch saved",
        description=(
            f"**{rule.label}** is now part of the shared always-on watcher. "
            "The first scans build durable history; exact comparable matches can qualify immediately."
        ),
        color=discord.Color.green(),
    )
    embed.add_field(name="Rule ID", value=f"`{rule.rule_id}`", inline=False)
    embed.add_field(
        name="Alert policy",
        value=(
            f"Verified discount: **{rule.min_discount_percent}%+**\n"
            f"Trusted reference floor: **${rule.min_reference_price:,.2f}**\n"
            f"Lane: **{'high-demand' if rule.sought_after else 'big-ticket'}**\n"
            f"Scan interval: **{max(1, rule.scan_interval_seconds // 60)} minutes**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Condition policy",
        value=f"`{', '.join(rule.allowed_conditions) or 'none'}`",
        inline=False,
    )
    return embed
