from __future__ import annotations

import math
from typing import Any

import discord

from sniperplug.services.deal_feedback import build_deal_feedback_view, build_feedback_target, ensure_deal_feedback_tables
from sniperplug.services.embed_delivery import sanitize_embed
from sniperplug.services.public_alert_config import get_public_alert_config
from sniperplug.services.public_deal_posts import card_product_key
from sniperplug.services.public_posting import normalize_retailer_key


DEFAULT_REVIEW_PAGE_SIZE = 3
DEFAULT_REVIEW_MAX_CARDS = 12


class ManualReviewShareView(discord.ui.View):
    def __init__(self, cards: list[Any], *, page_size: int = DEFAULT_REVIEW_PAGE_SIZE, max_cards: int = DEFAULT_REVIEW_MAX_CARDS):
        super().__init__(timeout=300)
        self.cards = cards[: max(1, int(max_cards))]
        self.page_size = max(1, min(5, int(page_size)))
        self.page = 0
        self.refresh_items()

    @property
    def page_count(self) -> int:
        return max(1, math.ceil(len(self.cards) / self.page_size))

    def page_cards(self) -> list[Any]:
        start = self.page * self.page_size
        return self.cards[start : start + self.page_size]

    def page_embeds(self) -> list[discord.Embed]:
        return [sanitize_embed(card.embed) for card in self.page_cards()]

    def content(self, *, prefix: str | None = None) -> str:
        header = prefix or "🟨 **Private autoscan review leads**"
        return (
            f"{header}\n"
            f"Page **{self.page + 1}/{self.page_count}** • Showing **{len(self.page_cards())}** of **{len(self.cards)}** lead(s).\n"
            "Use a **Post** button only after checking price, seller, exact variant, reviews, and comps."
        )

    def refresh_items(self) -> None:
        self.clear_items()
        start = self.page * self.page_size
        for offset, _card in enumerate(self.page_cards()):
            absolute_index = start + offset
            self.add_item(ManualShareButton(index=absolute_index, label=f"Post {absolute_index + 1}"))
        if self.page_count > 1:
            self.add_item(ManualReviewPageButton(direction=-1, disabled=self.page <= 0))
            self.add_item(ManualReviewPageButton(direction=1, disabled=self.page >= self.page_count - 1))


class ManualShareButton(discord.ui.Button):
    def __init__(self, *, index: int, label: str):
        super().__init__(label=label, emoji="📣", style=discord.ButtonStyle.success, row=3, custom_id=f"manual_review_share:{index}")
        self.index = index

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(self.view, ManualReviewShareView):
            await interaction.response.send_message("This review menu is no longer active.", ephemeral=True)
            return
        permissions = getattr(getattr(interaction, "user", None), "guild_permissions", None)
        if permissions is not None and not bool(getattr(permissions, "manage_guild", False)):
            await interaction.response.send_message("You need **Manage Server** permission to manually post review leads.", ephemeral=True)
            return
        try:
            card = self.view.cards[self.index]
        except Exception:
            await interaction.response.send_message("I could not find that review card anymore.", ephemeral=True)
            return
        ok, message = await share_review_card(bot=interaction.client, guild_id=interaction.guild_id, card=card)
        await interaction.response.send_message(message, ephemeral=True)


class ManualReviewPageButton(discord.ui.Button):
    def __init__(self, *, direction: int, disabled: bool = False):
        label = "Back" if direction < 0 else "Next"
        emoji = "⬅️" if direction < 0 else "➡️"
        super().__init__(label=label, emoji=emoji, style=discord.ButtonStyle.secondary, row=4, disabled=disabled, custom_id=f"manual_review_page:{direction}")
        self.direction = direction

    async def callback(self, interaction: discord.Interaction) -> None:
        if not isinstance(self.view, ManualReviewShareView):
            await interaction.response.send_message("This review menu is no longer active.", ephemeral=True)
            return
        self.view.page = max(0, min(self.view.page_count - 1, self.view.page + self.direction))
        self.view.refresh_items()
        await interaction.response.edit_message(content=self.view.content(), embeds=self.view.page_embeds(), view=self.view)


class ManualShareSelect(discord.ui.Select):
    """Legacy select kept for compatibility with older imports/tests; new views use paginated buttons."""

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
    await channel.send(embed=sanitize_embed(embed), view=feedback_view)
    return True, "Posted that review lead to the public deal channel with persistent feedback buttons."
