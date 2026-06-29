from __future__ import annotations

import contextvars
from typing import Any

import discord

from sniperplug.cogs import auto_scan_runner as legacy
from sniperplug.services.embed_delivery import sanitize_embed
from sniperplug.services.manual_review_share import ManualReviewShareView


PRIVATE_AUTOSCAN_REVIEW_CARD_LIMIT = 12
PRIVATE_AUTOSCAN_REVIEW_PAGE_SIZE = 3

legacy.AUTO_SCAN_DEEP_FOLLOWUP_ENABLED = False
legacy.AUTO_SCAN_DEEP_QUERY_COUNT = 6
legacy.AUTO_SCAN_MANUAL_QUERY_COUNT = 6

_CURRENT_AUTOSCAN_GUILD_ID: contextvars.ContextVar[int | None] = contextvars.ContextVar("sniperplug_current_autoscan_guild_id", default=None)
_CAPTURED_REVIEW_CARDS: dict[int, tuple[legacy.DealCard, ...]] = {}

_ORIGINAL_PREPARE_REVIEW_WATCHLIST_CARDS = getattr(
    legacy,
    "_sniperplug_original_prepare_review_watchlist_cards",
    legacy.prepare_review_watchlist_cards,
)
legacy._sniperplug_original_prepare_review_watchlist_cards = _ORIGINAL_PREPARE_REVIEW_WATCHLIST_CARDS


def _capture_review_watchlist_cards(result: Any, *, limit: int = legacy.AUTO_SCAN_REVIEW_FALLBACK_LIMIT) -> list[legacy.DealCard]:
    cards = list(_ORIGINAL_PREPARE_REVIEW_WATCHLIST_CARDS(result, limit=limit))
    guild_id = _CURRENT_AUTOSCAN_GUILD_ID.get()
    if guild_id is not None and cards:
        _CAPTURED_REVIEW_CARDS[int(guild_id)] = tuple(cards[:PRIVATE_AUTOSCAN_REVIEW_CARD_LIMIT])
    return cards


legacy.prepare_review_watchlist_cards = _capture_review_watchlist_cards


class AutoScanRunnerCog(legacy.AutoScanRunnerCog):
    """Native autoscan command surface with captured private review leads."""

    async def _run_guild_walmart_discovery(self, guild: legacy.AutoScanGuild, *, force: bool = False, query_count_override: int | None = None, report_label: str = "") -> legacy.AutoScanReport:
        token = _CURRENT_AUTOSCAN_GUILD_ID.set(int(guild.guild_id))
        try:
            return await super()._run_guild_walmart_discovery(
                guild,
                force=force,
                query_count_override=query_count_override,
                report_label=report_label,
            )
        finally:
            _CURRENT_AUTOSCAN_GUILD_ID.reset(token)

    async def _send_autoscan_report(self, interaction: discord.Interaction, report: legacy.AutoScanReport, *, label: str = "Auto-scan test result") -> None:
        await super()._send_autoscan_report(interaction, report, label=label)
        if not report.allowed or report.public_result.posted:
            return
        cards = list(_CAPTURED_REVIEW_CARDS.pop(int(report.guild_id), ()))[:PRIVATE_AUTOSCAN_REVIEW_CARD_LIMIT]
        if not cards:
            await self._safe_autoscan_followup(
                interaction,
                "🟨 SniperPlug found private review diagnostics, but no reusable review cards were captured from this pass. Try `/deals` with one of the strongest lead names shown above.",
            )
            return
        for index, card in enumerate(cards, start=1):
            annotate_private_review_card(card, index=index)
        await self._send_private_review_cards(interaction, cards, report=report)

    async def _send_private_review_cards(self, interaction: discord.Interaction, cards: list[legacy.DealCard], *, report: legacy.AutoScanReport) -> None:
        view = ManualReviewShareView(cards, page_size=PRIVATE_AUTOSCAN_REVIEW_PAGE_SIZE, max_cards=PRIVATE_AUTOSCAN_REVIEW_CARD_LIMIT)
        content = view.content(prefix="🟨 **Private autoscan review leads**\nThese are the exact review cards from the fast pass. They did not auto-post because they need staff verification first.")
        try:
            await interaction.followup.send(
                content=content,
                embeds=view.page_embeds(),
                view=view,
                ephemeral=True,
            )
            return
        except (discord.NotFound, discord.HTTPException) as exc:
            if legacy.interaction_token_is_gone(exc):
                if await self._send_autoscan_dm_fallback(interaction, content=content, embed=sanitize_embed(cards[0].embed)):
                    legacy.log.info("Sent autoscan private review lead by DM because Discord expired the interaction token")
                    return
            legacy.log.exception("Failed to send autoscan private review leads")
        except Exception:
            legacy.log.exception("Failed to send autoscan private review leads")


def annotate_private_review_card(card: legacy.DealCard, *, index: int) -> None:
    embed = getattr(card, "embed", None)
    if not isinstance(embed, discord.Embed):
        return
    if any(str(field.name or "") == "🟨 Private autoscan lead" for field in embed.fields):
        return
    embed.add_field(
        name="🟨 Private autoscan lead",
        value=(
            f"Lead #{index}. This is from the same autoscan pass, but it did **not** pass automatic public posting proof. "
            "Use the Post button only after checking price, seller, exact variant, reviews, and comps."
        ),
        inline=False,
    )
