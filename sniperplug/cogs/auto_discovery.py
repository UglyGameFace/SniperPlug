from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs.deal_scanner import (
    HUNT_PRESETS,
    DealCard,
    PresetResultView,
    provider_health_error_message,
    run_preset_hunt,
)
from sniperplug.cogs.public_alerts import auto_scan_allowed, record_auto_scan_run
from sniperplug.services.public_deal_posts import maybe_post_public_deal_cards

DISCORD_EMBED_MESSAGE_LIMIT = 6000
SAFE_EMBED_MESSAGE_LIMIT = 5200
AUTO_DISCOVERY_RETAILER = "walmart"


class AutoDiscoveryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="discover",
        description="Let SniperPlug automatically hunt across categories without making you search.",
    )
    async def discover(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        if interaction.guild_id is None:
            await interaction.followup.send("Use `/discover` in a server so SniperPlug can honor that server's auto-scan settings.", ephemeral=True)
            return

        health_error = await provider_health_error_message()
        if health_error:
            await interaction.followup.send(health_error, ephemeral=True)
            return

        allowed, gate_reason, gate_settings = await auto_scan_allowed(
            self.bot.db,
            interaction.guild_id,
            AUTO_DISCOVERY_RETAILER,
            scan_key="discover:all_presets",
        )
        if not allowed:
            embed = discord.Embed(
                title="🛑 Auto Discovery blocked by credit safety",
                description=(
                    f"SniperPlug did **not** call `{AUTO_DISCOVERY_RETAILER}`.\n\n"
                    f"Reason: {gate_reason}\n\n"
                    "Manual store-specific commands still work. Turn on auto-scan only for stores you intentionally want pulled automatically."
                ),
                color=discord.Color.dark_gold(),
            )
            embed.add_field(
                name="Current setting",
                value=(
                    f"Enabled: **{'yes' if gate_settings.get('enabled') else 'no'}**\n"
                    f"Interval: **every {gate_settings.get('interval_hours')}h**\n"
                    f"Daily limit: **{gate_settings.get('daily_limit')}/day**"
                ),
                inline=False,
            )
            embed.add_field(
                name="Enable example",
                value="`/retailer_autoscan retailer:walmart enabled:true interval_hours:4 daily_limit:25`",
                inline=False,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        all_cards: list[DealCard] = []
        total_pages_checked = 0
        total_products_checked = 0
        warnings: list[str] = [gate_reason]
        category_notes: list[str] = []

        for preset in HUNT_PRESETS.values():
            cards, pages_checked, products_checked, preset_warnings, shown_discount = await run_preset_hunt(
                preset=preset,
                requested_by=str(interaction.user.id),
            )
            total_pages_checked += pages_checked
            total_products_checked += products_checked
            warnings.extend(w for w in preset_warnings if w not in warnings)
            if cards:
                category_notes.append(
                    f"{preset.emoji} **{preset.label}**: {len(cards)} match(es), showing {shown_discount}%+ best available"
                )
                all_cards.extend(cards[:3])
            else:
                category_notes.append(f"{preset.emoji} **{preset.label}**: no useful matches right now")

        await record_auto_scan_run(
            self.bot.db,
            interaction.guild_id,
            AUTO_DISCOVERY_RETAILER,
            scan_key="discover:all_presets",
        )

        all_cards = dedupe_cards(all_cards)
        all_cards.sort(key=lambda card: (card.discount, card.score), reverse=True)
        shown_cards = all_cards[:5]
        public_result = await maybe_post_public_deal_cards(
            bot=self.bot,
            guild_id=interaction.guild_id,
            cards=shown_cards,
            source_label="discover",
        )

        embed = discord.Embed(
            title="🤖 SniperPlug Auto Discovery",
            description=(
                "I searched the enabled automatic deal source for you. No product names, pages, or filters needed.\n\n"
                f"Checked: **{total_products_checked} products** across **{total_pages_checked} smart searches**\n"
                f"Found: **{len(all_cards)} candidate(s)**"
            ),
            color=discord.Color.orange() if all_cards else discord.Color.dark_gold(),
        )
        embed.add_field(
            name="Category results",
            value="\n".join(category_notes[:8]) or "No category results yet.",
            inline=False,
        )
        embed.add_field(
            name="Auto-scan budget",
            value=(
                f"Retailer: `{AUTO_DISCOVERY_RETAILER}`\n"
                f"Interval: **every {gate_settings.get('interval_hours')}h**\n"
                f"Daily limit: **{gate_settings.get('daily_limit')}/day**"
            ),
            inline=False,
        )
        if public_result.any_activity:
            embed.add_field(
                name="📣 Public posting",
                value=(
                    f"Posted: **{public_result.posted}**\n"
                    f"Duplicate blocked: **{public_result.skipped_duplicate}**\n"
                    f"Not alertable/private review: **{public_result.skipped_not_alertable}**"
                ),
                inline=False,
            )
        if warnings:
            embed.add_field(name="⚠️ Notes", value="\n".join(f"• {w}" for w in warnings[:3]), inline=False)
        embed.set_footer(text="Auto Discovery does not guess discounts. Weak proof stays review-only instead of fake public alerts.")

        if not shown_cards:
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        await send_discovery_results(interaction, summary=embed, cards=shown_cards)

    @discover.error
    async def discover_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = f"Auto discovery hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


async def send_discovery_results(interaction: discord.Interaction, *, summary: discord.Embed, cards: list[DealCard]) -> None:
    """Send Auto Discovery results without tripping Discord's 6000-char embed payload cap.

    Walmart proof cards can be rich. Discord counts the combined embed text in a
    single message, so summary + five rich cards can exceed 6000 even when each
    individual card is valid. Send the summary first, then cards in safe chunks.
    """
    await interaction.followup.send(embed=summary, ephemeral=True)

    batches = batch_cards_for_embed_limit(cards, limit=SAFE_EMBED_MESSAGE_LIMIT)
    for batch in batches:
        await interaction.followup.send(
            embeds=[card.embed for card in batch],
            view=PresetResultView(batch),
            ephemeral=True,
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
        key = getattr(card, "public_post_key", None) or card.url or card.label
        if key in seen:
            continue
        seen.add(key)
        unique.append(card)
    return unique
