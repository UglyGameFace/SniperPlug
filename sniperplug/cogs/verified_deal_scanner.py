from __future__ import annotations

import discord
from discord import app_commands

from sniperplug.cogs.unified_deal_scanner import UnifiedDealScannerCog
from sniperplug.cogs.deal_scanner import WalmartCashOffersButton
from sniperplug.services.verified_discount_hunt import (
    HUNT_PRESETS,
    build_verified_hunt_menu_embed,
    verified_hunt_button_callback,
)


class VerifiedDealScannerCog(UnifiedDealScannerCog):
    """Deal scanner with `/deals`, advanced Walmart scan, and verified `/hunt` wired directly."""

    @app_commands.command(name="hunt", description="Pick a Walmart category and let SniperPlug hunt verified deals.")
    async def hunt(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=build_verified_hunt_menu_embed(),
            view=VerifiedHuntPresetMenuView(),
            ephemeral=True,
        )


class VerifiedHuntPresetMenuView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        for index, preset in enumerate(HUNT_PRESETS.values()):
            self.add_item(VerifiedHuntPresetButton(preset, row=index // 2))
        self.add_item(WalmartCashOffersButton(row=4))


class VerifiedHuntPresetButton(discord.ui.Button):
    def __init__(self, preset, row: int):
        super().__init__(
            label=preset.label,
            emoji=preset.emoji,
            style=discord.ButtonStyle.primary,
            row=row,
            custom_id=f"verified_hunt_preset:{preset.key}",
        )
        self.preset = preset

    async def callback(self, interaction: discord.Interaction) -> None:
        await verified_hunt_button_callback(self, interaction)
