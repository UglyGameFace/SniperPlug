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


class AutoDiscoveryCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="discover",
        description="Let SniperPlug automatically hunt across categories without making you search.",
    )
    async def discover(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        health_error = await provider_health_error_message()
        if health_error:
            await interaction.followup.send(health_error, ephemeral=True)
            return

        all_cards: list[DealCard] = []
        total_pages_checked = 0
        total_products_checked = 0
        warnings: list[str] = []
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

        all_cards = dedupe_cards(all_cards)
        all_cards.sort(key=lambda card: (card.discount, card.score), reverse=True)

        embed = discord.Embed(
            title="🤖 SniperPlug Auto Discovery",
            description=(
                "I searched the main deal categories for you. No product names, pages, or filters needed.\n\n"
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
        if warnings:
            embed.add_field(name="⚠️ Notes", value="\n".join(f"• {w}" for w in warnings[:3]), inline=False)
        embed.set_footer(text="Auto Discovery does not guess discounts. Weak proof stays review-only instead of fake public alerts.")

        if not all_cards:
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        await interaction.followup.send(
            embeds=[embed] + [card.embed for card in all_cards[:5]],
            view=PresetResultView(all_cards[:5]),
            ephemeral=True,
        )

    @discover.error
    async def discover_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        message = f"Auto discovery hit an error: `{error}`"
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)


def dedupe_cards(cards: list[DealCard]) -> list[DealCard]:
    seen: set[str] = set()
    unique: list[DealCard] = []
    for card in cards:
        key = card.url or card.label
        if key in seen:
            continue
        seen.add(key)
        unique.append(card)
    return unique
