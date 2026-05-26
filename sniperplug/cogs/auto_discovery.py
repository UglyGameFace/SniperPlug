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
from sniperplug.services.fresh_deal_filter import select_fresh_deal_cards
from sniperplug.services.public_deal_posts import maybe_post_public_deal_cards
from sniperplug.services.verified_discount_hunt import collect_verified_discount_cards, send_card_batches

DISCORD_EMBED_MESSAGE_LIMIT = 6000
SAFE_EMBED_MESSAGE_LIMIT = 5200
AUTO_DISCOVERY_RETAILER = "walmart"


class AutoDiscoveryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="discover",
        description="Manually scan Walmart for verified 50%+ discounts without picking categories.",
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

        auto_scan_settings = await list_retailer_auto_scan_settings(self.bot.db, interaction.guild_id)
        gate_settings = auto_scan_settings.get(AUTO_DISCOVERY_RETAILER, default_auto_scan_config(AUTO_DISCOVERY_RETAILER))
        result = await collect_verified_discount_cards(requested_by=str(interaction.user.id))
        fresh_selection = await select_fresh_deal_cards(
            self.bot.db,
            guild_id=interaction.guild_id,
            cards=result.cards,
            fallback_retailer=AUTO_DISCOVERY_RETAILER,
            limit=max(len(result.cards), 1),
        )
        shown_cards = fresh_selection.fresh
        public_result = await maybe_post_public_deal_cards(
            bot=self.bot,
            guild_id=interaction.guild_id,
            cards=shown_cards,
            source_label="discover:verified_50_plus",
            fallback_retailer=AUTO_DISCOVERY_RETAILER,
        )

        embed = discord.Embed(
            title="🤖 Verified Walmart Discovery",
            description=(
                "Manual `/discover` now uses the same broad verified 50%+ hunt as `/hunt`.\n"
                "No category presets. No relaxed filler. No guessed discount math.\n\n"
                f"Checked: **{result.products_checked} returned products** across **{result.pages_checked} API result pages**\n"
                f"Found: **{len(result.cards)} verified 50%+ card(s)**\n"
                f"Fresh filter: {fresh_selection.summary_line()}"
            ),
            color=discord.Color.red() if shown_cards else discord.Color.dark_gold(),
        )
        embed.add_field(name="Auto-scan setting", value=discover_auto_scan_status(gate_settings), inline=False)
        if public_result.any_activity:
            embed.add_field(
                name="📣 Public posting",
                value=(
                    f"Posted: **{public_result.posted}**\n"
                    f"Duplicate blocked: **{public_result.skipped_duplicate}**\n"
                    f"Not alertable/private review: **{public_result.skipped_not_alertable}**\n"
                    f"Cached active: **{getattr(public_result, 'cached_active', 0)}**"
                ),
                inline=False,
            )
        if result.warnings:
            embed.add_field(name="⚠️ API notes", value="\n".join(f"• {w}" for w in result.warnings[:5]), inline=False)
        embed.set_footer(text="Only API-verified 50%+ markdowns are shown. Same-price repeats are hidden; lower prices can appear again.")

        if not shown_cards:
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        await send_card_batches(interaction, summary=embed, cards=shown_cards)

    @discover.error
    async def discover_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = f"Discovery hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


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


def embed_text_size(embed: discord.Embed) -> int:
    data = embed.to_dict()
    total = 0
    total += len(str(data.get("title") or ""))
    total += len(str(data.get("description") or ""))
    footer = data.get("footer") or {}
    total += len(str(footer.get("text") or ""))
    author = data.get("author") or {}
    total += len(str(author.get("name") or ""))
    for field in data.get("fields") or []:
        total += len(str(field.get("name") or ""))
        total += len(str(field.get("value") or ""))
    return total


def dedupe_cards(cards: list[DealCard]) -> list[DealCard]:
    seen: set[str] = set()
    unique: list[DealCard] = []
    for card in cards:
        key = getattr(card, "public_post_key", None) or getattr(card, "selected_offer_id", None) or getattr(card, "sku", None) or getattr(card, "upc", None) or card.url or card.label
        if key in seen:
            continue
        seen.add(key)
        unique.append(card)
    return unique
