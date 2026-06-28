from __future__ import annotations

import discord

from sniperplug.cogs import auto_scan_runner as legacy
from sniperplug.cogs import deal_scanner
from sniperplug.providers.base import ProviderScanResult
from sniperplug.services.autoscan_route_policy import public_autoscan_hunt_presets
from sniperplug.services.deal_ranking import rank_review_cards
from sniperplug.services.embed_delivery import sanitize_embed
from sniperplug.services.manual_review_share import ManualReviewShareView
from sniperplug.services.walmart_review_candidates import build_review_candidate_cards


PRIVATE_AUTOSCAN_REVIEW_QUERY_LIMIT = 3
PRIVATE_AUTOSCAN_REVIEW_MAX_RESULTS = 12
PRIVATE_AUTOSCAN_REVIEW_CARD_LIMIT = 3


class AutoScanRunnerCog(legacy.AutoScanRunnerCog):
    """Native autoscan command surface with private review leads.

    Public auto-posting stays strict. Manual `/autoscan_now` now also shows the
    best private review leads with staff buttons, so useful finds are visible
    even when they are not safe enough for automatic public posting.
    """

    async def _send_autoscan_report(self, interaction: discord.Interaction, report: legacy.AutoScanReport, *, label: str = "Auto-scan test result") -> None:
        await super()._send_autoscan_report(interaction, report, label=label)
        if not report.allowed or report.public_result.posted:
            return
        cards = await self._private_review_cards_for_report(report)
        if not cards:
            return
        await self._send_private_review_cards(interaction, cards, report=report)

    async def _private_review_cards_for_report(self, report: legacy.AutoScanReport) -> list[legacy.DealCard]:
        presets = public_autoscan_hunt_presets()
        preset = presets.get(report.category_key) or presets.get("deal_week") or presets.get("all") or next(iter(presets.values()), None)
        if preset is None:
            return []

        all_candidates = []
        warnings: list[str] = []
        for query in tuple(preset.queries)[:PRIVATE_AUTOSCAN_REVIEW_QUERY_LIMIT]:
            try:
                result = await deal_scanner.run_walmart_scan(
                    query,
                    1,
                    PRIVATE_AUTOSCAN_REVIEW_MAX_RESULTS,
                    None,
                    None,
                    "autoscan",
                )
            except Exception as exc:
                warnings.append(legacy.clean_log_text(exc))
                continue
            all_candidates.extend(result.candidates)
            warnings.extend(w for w in result.warnings if w not in warnings)

        if not all_candidates:
            return []
        deduped = deal_scanner.dedupe_candidates(all_candidates)
        aggregate = ProviderScanResult(
            provider_key="walmart",
            candidates=tuple(deduped),
            warnings=tuple(warnings),
            page=1,
            page_size=len(deduped),
            start_index=1,
            has_next_page=False,
        )
        review = build_review_candidate_cards(list(aggregate.candidates), limit=PRIVATE_AUTOSCAN_REVIEW_CARD_LIMIT)
        cards = rank_review_cards(review.cards)[:PRIVATE_AUTOSCAN_REVIEW_CARD_LIMIT]
        for index, card in enumerate(cards, start=1):
            annotate_private_review_card(card, index=index)
        return cards

    async def _send_private_review_cards(self, interaction: discord.Interaction, cards: list[legacy.DealCard], *, report: legacy.AutoScanReport) -> None:
        content = (
            "🟨 **Private autoscan review leads**\n"
            "Nothing passed the automatic public proof gate, but SniperPlug did find leads worth checking. "
            "Use **Post 1 / Post 2 / Post 3** only after you verify price, seller, exact variant, and comps."
        )
        try:
            await interaction.followup.send(
                content=content,
                embeds=[sanitize_embed(card.embed) for card in cards[:PRIVATE_AUTOSCAN_REVIEW_CARD_LIMIT]],
                view=ManualReviewShareView(cards),
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
            f"Lead #{index}. This did **not** pass automatic public posting proof. "
            "A staff member can manually post it with the button below after checking price, seller, exact variant, reviews, and comps."
        ),
        inline=False,
    )
