from __future__ import annotations

from typing import Any

import discord

from sniperplug.services.deal_feedback import build_deal_feedback_view, build_feedback_target, ensure_deal_feedback_tables
from sniperplug.services.public_alert_config import get_public_alert_config
from sniperplug.services.public_deal_posts import card_product_key
from sniperplug.services.public_posting import normalize_retailer_key


class ManualReviewShareView(discord.ui.View):
    def __init__(self, cards: list[Any]):
        super().__init__(timeout=300)
        self.cards = cards[:5]
        if self.cards:
            self.add_item(ManualShareSelect(self.cards))


class ManualShareSelect(discord.ui.Select):
    def __init__(self, cards: list[Any]):
        self.cards = cards
        options = [
            discord.SelectOption(label=f"Post {index + 1}: {getattr(card, 'label', 'deal')[:70]}", value=str(index))
            for index, card in enumerate(cards[:5])
        ]
        super().__init__(placeholder="Staff: manually post one lead to public", min_values=1, max_values=1, options=options, row=3)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            card = self.cards[int(self.values[0])]
        except Exception:
            await interaction.response.send_message("I could not find that review card anymore.", ephemeral=True)
            return
        ok, message = await share_review_card(bot=interaction.client, guild_id=interaction.guild_id, card=card)
        await interaction.response.send_message(message, ephemeral=True)


async def share_review_card(*, bot: Any, guild_id: int | None, card: Any, fallback_retailer: str = "walmart") -> tuple[bool, str]:
    if guild_id is None:
        return False, "Use this inside a server so I know where to post it."
    db = getattr(bot, "db", None)
    if db is None:
        return False, "Bot database is unavailable."
    await ensure_deal_feedback_tables(db)
    config = await get_public_alert_config(db, guild_id)
    channel_id = config.get("channel_id")
    if not channel_id:
        return False, "No public deal channel is configured yet. Set it with `/setup_sniperplug_here` first."
    retailer = normalize_retailer_key(getattr(card, "retailer", None)) or normalize_retailer_key(fallback_retailer)
    if retailer not in set(config.get("retailers") or ()): 
        return False, f"Public posting is not enabled for `{retailer}` in this server."
    channel = bot.get_channel(channel_id)
    if channel is None:
        channel = await bot.fetch_channel(channel_id)
    if not hasattr(channel, "send"):
        return False, "Configured public deal channel is not sendable."
    embed = card.embed.copy()
    embed.add_field(
        name="📣 Staff-shared review lead",
        value="Manually shared from a private SniperPlug review card. Recheck price, seller, exact variant, and comps before buying.",
        inline=False,
    )
    product_key = card_product_key(card, retailer=retailer)
    target = build_feedback_target(card, target_key=product_key, retailer=retailer, source_label="staff_shared_review")
    feedback_view = await build_deal_feedback_view(db, guild_id=guild_id, target=target)
    await channel.send(embed=embed, view=feedback_view)
    return True, "Posted that review lead to the public deal channel with persistent feedback buttons."
