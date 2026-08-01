from __future__ import annotations

import logging
import math
from typing import Any

import discord

from sniperplug.services.deal_feedback import build_deal_feedback_view, build_feedback_target, ensure_deal_feedback_tables
from sniperplug.services.embed_delivery import sanitize_embed
from sniperplug.services.public_alert_config import get_public_alert_config
from sniperplug.services.public_deal_posts import (
    PUBLIC_SCOUT_ALERT_KEY,
    alert_expires_at,
    card_deal_key,
    card_product_key,
    mark_public_deal_posted,
    release_public_deal_reservation,
    reserve_public_deal_post,
    resolve_public_alert_channel,
    safe_find_recent_alert,
    should_suppress_recent_alert,
)
from sniperplug.services.public_posting import normalize_retailer_key
from sniperplug.services.review_card_enrichment import enrich_review_card


DEFAULT_REVIEW_PAGE_SIZE = 3
DEFAULT_REVIEW_MAX_CARDS = 12
MANUAL_REVIEW_SOURCE_LABEL = "staff_shared_review_scout"
MANUAL_REVIEW_POST_PREFIX = "scout:staff_review"
MANUAL_REVIEW_PERMISSION_ERROR = "You need **Manage Server** permission to manually post review leads."
log = logging.getLogger("sniperplug.manual_review_share")


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
        embeds: list[discord.Embed] = []
        for card in self.page_cards():
            try:
                enrich_review_card(card)
                embeds.append(sanitize_embed(card.embed))
            except Exception as exc:
                log.exception("Skipped malformed private review card while rendering page=%s: %s", self.page, clean_error_text(exc))
        return embeds

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
        if not can_manually_post_review(interaction):
            await interaction.response.send_message(MANUAL_REVIEW_PERMISSION_ERROR, ephemeral=True)
            return
        try:
            card = self.view.cards[self.index]
        except Exception:
            await interaction.response.send_message("I could not find that review card anymore.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            _ok, message = await share_review_card(bot=interaction.client, guild_id=interaction.guild_id, card=card)
            await interaction.followup.send(message, ephemeral=True)
        except Exception as exc:
            log.exception("Manual review post button failed guild=%s index=%s", interaction.guild_id, self.index)
            await interaction.followup.send(f"I could not post that review lead: `{clean_error_text(exc)}`", ephemeral=True)


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

        await interaction.response.defer()
        try:
            self.view.page = max(0, min(self.view.page_count - 1, self.view.page + self.direction))
            self.view.refresh_items()
            embeds = self.view.page_embeds()
            if not embeds:
                await interaction.followup.send("That review page contained malformed cards and could not be displayed. The error was logged.", ephemeral=True)
                return
            await interaction.edit_original_response(content=self.view.content(), embeds=embeds, view=self.view)
        except Exception as exc:
            log.exception("Manual review pagination failed guild=%s page=%s direction=%s", interaction.guild_id, getattr(self.view, "page", None), self.direction)
            await interaction.followup.send(f"I could not open that review page: `{clean_error_text(exc)}`", ephemeral=True)


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
        if not can_manually_post_review(interaction):
            await interaction.response.send_message(MANUAL_REVIEW_PERMISSION_ERROR, ephemeral=True)
            return
        try:
            card = self.cards[int(self.values[0])]
        except Exception:
            await interaction.response.send_message("I could not find that review card anymore.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            _ok, message = await share_review_card(bot=interaction.client, guild_id=interaction.guild_id, card=card)
            await interaction.followup.send(message, ephemeral=True)
        except Exception as exc:
            log.exception("Legacy manual review select failed guild=%s", interaction.guild_id)
            await interaction.followup.send(f"I could not post that review lead: `{clean_error_text(exc)}`", ephemeral=True)


def can_manually_post_review(interaction: discord.Interaction) -> bool:
    """Fail closed when Discord does not provide guild permission context."""
    permissions = getattr(getattr(interaction, "user", None), "guild_permissions", None)
    return bool(permissions is not None and getattr(permissions, "manage_guild", False))


async def share_review_card(*, bot: Any, guild_id: int | None, card: Any, fallback_retailer: str = "walmart") -> tuple[bool, str]:
    if guild_id is None:
        return False, "Use this inside a server so I know where to post it."
    db = getattr(bot, "db", None)
    if db is None:
        return False, "Bot database is unavailable."
    await ensure_deal_feedback_tables(db)
    config = await get_public_alert_config(db, guild_id)
    channel_id = config.get("channel_id")
    if not config.get("enabled") or not channel_id:
        return False, "No public deal channel is configured yet. Set it with `/setup_sniperplug_here` first."
    retailer = normalize_retailer_key(getattr(card, "retailer", None)) or normalize_retailer_key(fallback_retailer)
    if retailer not in set(config.get("retailers") or ()):
        return False, f"Public posting is not enabled for `{retailer}` in this server."

    channel, channel_note = await resolve_public_alert_channel(bot, db, guild_id=guild_id, configured_channel_id=channel_id)
    if channel is None:
        return False, channel_note or "Public deal channel could not be found."
    if not hasattr(channel, "send"):
        return False, "Configured public deal channel is not sendable."

    current_price = float_or_none(getattr(card, "current_price", None) or getattr(card, "api_current_price", None))
    product_key = card_product_key(card, retailer=retailer)
    recent_alert = await safe_find_recent_alert(
        db,
        guild_id=guild_id,
        retailer=retailer,
        product_key=product_key,
        current_price=current_price,
        alert_key=PUBLIC_SCOUT_ALERT_KEY,
    )
    if recent_alert and should_suppress_recent_alert(recent_alert, current_price):
        return False, "That lead was already posted recently at the same or better price, so I blocked the duplicate public post."

    deal_key = f"{MANUAL_REVIEW_POST_PREFIX}:{card_deal_key(card, retailer=retailer)}"
    reserved = await reserve_public_deal_post(db, guild_id=guild_id, retailer=retailer, deal_key=deal_key, source_label=MANUAL_REVIEW_SOURCE_LABEL)
    if not reserved:
        return False, "That lead is already being posted or was posted recently, so I blocked the duplicate public post."

    try:
        embed = aligned_public_review_embed(card)
        target = build_feedback_target(card, target_key=product_key, retailer=retailer, source_label=MANUAL_REVIEW_SOURCE_LABEL)
        feedback_view = await build_deal_feedback_view(db, guild_id=guild_id, target=target)
        message = await channel.send(embed=sanitize_embed(embed), view=feedback_view)
    except Exception as exc:
        await release_public_deal_reservation(db, guild_id=guild_id, deal_key=deal_key)
        return False, f"Public post failed: `{clean_error_text(exc)}`"

    await mark_public_deal_posted(db, guild_id=guild_id, deal_key=deal_key)
    try:
        await db.record_alert_dedupe(
            guild_id=guild_id,
            retailer=retailer,
            product_key=product_key,
            alert_key=PUBLIC_SCOUT_ALERT_KEY,
            current_price=current_price,
            channel_id=getattr(channel, "id", channel_id),
            message_id=getattr(message, "id", None),
            threshold_price=current_price,
            expires_at=alert_expires_at(hours=6),
        )
    except Exception:
        pass

    suffix = f" {channel_note}" if channel_note else ""
    return True, "Posted that review lead to the public deal channel with aligned duplicate protection and feedback buttons." + suffix


def aligned_public_review_embed(card: Any) -> discord.Embed:
    enrich_review_card(card)
    source = getattr(card, "embed", None)
    if isinstance(source, discord.Embed):
        embed = source.copy()
    else:
        embed = discord.Embed(title=str(getattr(card, "label", None) or "SniperPlug review lead"), url=str(getattr(card, "url", "") or ""))
    embed.add_field(
        name="📣 Staff-shared review lead",
        value="A staff member manually shared this from SniperPlug private review results. Recheck price, seller, exact variant, reviews, and comps before buying.",
        inline=False,
    )
    embed.set_footer(text="SniperPlug staff-shared review lead • not an automatic verified public markdown post")
    return embed


def float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def clean_error_text(value: Any, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").replace("\n", " ").split())
    return text[:limit].rstrip() + ("…" if len(text) > limit else "")
