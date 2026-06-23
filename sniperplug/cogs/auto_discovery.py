from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.deal_scanner import DealCard, provider_health_error_message
from sniperplug.cogs.public_alerts import (
    default_auto_scan_config,
    format_daily_limit,
    format_interval,
    list_retailer_auto_scan_settings,
)
from sniperplug.services.deal_finder_engine import find_walmart_discovery_deals
from sniperplug.services.fresh_deal_filter import select_fresh_deal_cards
from sniperplug.services.public_deal_posts import PublicPostResult, maybe_post_public_deal_cards
from sniperplug.services.scan_locks import ScanLockKey, scan_operation_locks
from sniperplug.services.verified_discount_hunt import send_card_batches

DISCORD_EMBED_MESSAGE_LIMIT = 6000
SAFE_EMBED_MESSAGE_LIMIT = 5200
AUTO_DISCOVERY_RETAILER = "walmart"


class AutoDiscoveryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="discover",
        description="Manually scan Walmart for verified discounts without picking categories.",
    )
    async def discover(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if interaction.guild_id is None:
            await interaction.followup.send("Use `/discover` in a server so SniperPlug can use that server's public-posting and duplicate settings.", ephemeral=True)
            return

        health_error = await provider_health_error_message()
        if health_error:
            await interaction.followup.send(health_error, ephemeral=True)
            return

        lock_key = ScanLockKey(
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            action="manual_discovery",
            preset="all_verified_discounts",
        )
        if not await scan_operation_locks.acquire(lock_key):
            await interaction.followup.send("That discovery scan is already running. I blocked the duplicate tap so SniperPlug does not double-post or freeze the menu.", ephemeral=True)
            return

        try:
            await interaction.followup.send("⏳ Running Walmart discovery now. Duplicate taps are locked until this finishes.", ephemeral=True)
            auto_scan_settings = await list_retailer_auto_scan_settings(self.bot.db, interaction.guild_id)
            gate_settings = auto_scan_settings.get(AUTO_DISCOVERY_RETAILER, default_auto_scan_config(AUTO_DISCOVERY_RETAILER))
            result = await find_walmart_discovery_deals(
                requested_by=str(interaction.user.id),
                db=self.bot.db,
                guild_id=interaction.guild_id,
                use_price_memory=True,
            )
            fresh_selection = await select_fresh_deal_cards(
                self.bot.db,
                guild_id=interaction.guild_id,
                cards=result.cards,
                fallback_retailer=AUTO_DISCOVERY_RETAILER,
                limit=max(len(result.cards), 1),
                hide_active_cache_repeats=False,
            )
            shown_cards = fresh_selection.fresh
            public_result = await maybe_post_public_deal_cards(
                bot=self.bot,
                guild_id=interaction.guild_id,
                cards=shown_cards,
                source_label=f"discover:verified_{result.min_discount}_plus",
                fallback_retailer=AUTO_DISCOVERY_RETAILER,
            )

            review_count = len(result.review_candidates.cards) if result.review_candidates else 0
            embed = discord.Embed(
                title="🤖 Verified Walmart Discovery",
                description=(
                    "Manual `/discover` now uses the same server-aware discovery path as auto-scan.\n"
                    "No category presets. No relaxed filler. No guessed discount math.\n\n"
                    f"Threshold: **{result.min_discount}%+ verified markdown**\n"
                    f"Checked: **{result.products_checked} returned products** across **{result.pages_checked} API result pages**\n"
                    f"Verified total: **{result.total_verified_cards}** • Fresh shown: **{len(shown_cards)}**\n"
                    f"Review/flip leads: **{review_count}**\n"
                    f"Fresh filter: {fresh_selection.summary_line()}"
                ),
                color=discord.Color.red() if shown_cards else discord.Color.dark_gold(),
            )
            embed.add_field(name="Auto-scan setting", value=discover_auto_scan_status(gate_settings), inline=False)
            if result.price_memory is not None:
                embed.add_field(name="🧠 Price memory", value=result.price_memory.summary_line(), inline=False)
            if result.review_candidates is not None:
                embed.add_field(name="🟨 Review / flip audit", value=result.review_candidates.summary_line(), inline=False)
            if public_result.any_activity:
                embed.add_field(name="📣 Public posting", value=public_posting_summary(public_result), inline=False)
            if result.warnings:
                embed.add_field(name="⚠️ API notes", value="\n".join(f"• {w}" for w in result.warnings[:5]), inline=False)
            embed.set_footer(text="Verified cards can public-post. Review/flip leads are private and require manual checkout/comp checks.")

            if not shown_cards:
                await interaction.followup.send(embed=embed, ephemeral=True)
                if result.review_candidates and result.review_candidates.cards:
                    await send_card_batches(interaction, summary=discord.Embed(title="🟨 Review / flip leads", description="Private leads only — not public-posted as verified deals.", color=discord.Color.gold()), cards=[], review_cards=result.review_candidates.cards[:5])
                return

            await send_card_batches(interaction, summary=embed, cards=shown_cards, review_cards=result.review_candidates.cards[:5] if result.review_candidates else [])
        finally:
            await scan_operation_locks.release(lock_key)

    @discover.error
    async def discover_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = f"Discovery hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def public_posting_summary(result: PublicPostResult) -> str:
    lines = [
        f"Posted: **{result.posted}**",
        f"Duplicate blocked: **{result.skipped_duplicate}**",
        f"Not alertable/private review: **{result.skipped_not_alertable}**",
        f"Wrong retailer blocked: **{getattr(result, 'skipped_wrong_retailer', 0)}**",
        f"Disabled/config blocked: **{getattr(result, 'skipped_disabled', 0)}**",
        f"Cached active: **{getattr(result, 'cached_active', 0)}**",
    ]
    if result.errors:
        lines.append("Errors:\n" + "\n".join(f"• {error}" for error in result.errors[:4]))
    return "\n".join(lines)


def embed_text_size(embed: discord.Embed) -> int:
    total = 0
    if embed.title:
        total += len(str(embed.title))
    if embed.description:
        total += len(str(embed.description))
    for field in embed.fields:
        total += len(str(field.name)) + len(str(field.value))
    footer = getattr(embed, "footer", None)
    footer_text = getattr(footer, "text", None)
    if footer_text:
        total += len(str(footer_text))
    author = getattr(embed, "author", None)
    author_name = getattr(author, "name", None)
    if author_name:
        total += len(str(author_name))
    return total


def batch_cards_for_embed_limit(cards: list[DealCard], *, limit: int = SAFE_EMBED_MESSAGE_LIMIT) -> list[list[DealCard]]:
    batches: list[list[DealCard]] = []
    current: list[DealCard] = []
    current_size = 0
    for card in cards:
        size = embed_text_size(card.embed)
        if current and current_size + size > limit:
            batches.append(current)
            current = []
            current_size = 0
        current.append(card)
        current_size += size
    if current:
        batches.append(current)
    return batches


def manual_discover_note(settings: dict) -> str:
    if not settings.get("enabled"):
        return f"Manual `/discover` override: `{AUTO_DISCOVERY_RETAILER}` auto-scan is off, but this manual command is allowed."
    return f"Manual `/discover` run. `{AUTO_DISCOVERY_RETAILER}` auto-scan settings only gate scheduled/background pulls."


def discover_auto_scan_status(settings: dict) -> str:
    interval_hours = int(settings.get("interval_hours") if settings.get("interval_hours") is not None else 6)
    daily_limit = int(settings.get("daily_limit") if settings.get("daily_limit") is not None else 25)
    return (
        f"Retailer: `{AUTO_DISCOVERY_RETAILER}`\n"
        f"Auto enabled: **{'yes' if settings.get('enabled') else 'no'}**\n"
        f"Interval: **{format_interval(interval_hours)}**\n"
        f"Daily limit: **{format_daily_limit(daily_limit)}**\n"
        "Manual `/discover`: **allowed**"
    )
