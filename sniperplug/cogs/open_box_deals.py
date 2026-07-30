from __future__ import annotations

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import DealCard, add_public_posting_field, short_button_label
from sniperplug.models.candidate import SourceCandidate
from sniperplug.providers.base import ProviderScanResult, ProviderStatus
from sniperplug.providers.registry import provider_registry
from sniperplug.services.candidate_pipeline import evaluate_candidate
from sniperplug.services.embed_delivery import send_summary_and_card_batches
from sniperplug.services.open_box_autoscan_routes import OPEN_BOX_AUTOSCAN_QUERIES
from sniperplug.services.public_deal_posts import maybe_post_public_deal_cards
from sniperplug.services.public_deal_quality import (
    LANE_OPEN_BOX_LIKE_NEW,
    LANE_RESTORED_REFURBISHED,
    current_price,
    normalized_condition,
    normalized_lane,
    reference_price,
    select_public_deal_candidates,
    structured_discount,
)
from sniperplug.services.safe_links import product_link_choices

class OpenBoxDealsCog(commands.Cog):
    """Direct Walmart condition-deal scan.

    This command is deliberately separate from Walmart Cash. It only surfaces
    condition markdowns when current/reference/condition proof is present.
    """

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="open_box_deals", description="Scan Walmart open-box, like-new, restored, and refurbished markdowns.")
    @app_commands.describe(
        query="Optional tighter search. Leave blank for bounded open-box route coverage.",
        min_discount="Public threshold to test against.",
        max_routes="How many open-box route searches to run. Bounded to avoid API fanout.",
    )
    async def open_box_deals(
        self,
        interaction: discord.Interaction,
        query: str | None = None,
        min_discount: app_commands.Range[int, 1, 95] = 50,
        max_routes: app_commands.Range[int, 1, 11] = 5,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)

        provider = provider_registry.get("walmart")
        if provider is None:
            await interaction.followup.send("Walmart search is not connected yet.", ephemeral=True)
            return
        health = await provider.healthcheck()
        if health.status != ProviderStatus.READY:
            await interaction.followup.send("Open-box scan is not ready yet. Staff needs to finish the Walmart connection first.", ephemeral=True)
            return

        routes = (query.strip(),) if query and query.strip() else OPEN_BOX_AUTOSCAN_QUERIES[: max(1, int(max_routes))]
        results = await asyncio.gather(
            *(deal_scanner.run_walmart_scan(route, 1, 12, None, None, str(interaction.user.id)) for route in routes),
            return_exceptions=True,
        )

        warnings: list[str] = []
        candidates: list[SourceCandidate] = []
        for route, result in zip(routes, results):
            if isinstance(result, Exception):
                warnings.append(f"{route}: {result}")
                continue
            candidates.extend(result.candidates)
            warnings.extend(w for w in result.warnings if w not in warnings)

        aggregate = ProviderScanResult(
            provider_key="walmart",
            candidates=tuple(deal_scanner.dedupe_candidates(candidates)),
            warnings=tuple(warnings),
            page=1,
            page_size=len(candidates),
            start_index=1,
            has_next_page=True,
        )
        cards = build_open_box_cards(aggregate, min_discount=int(min_discount))
        public_cards = select_public_deal_candidates(cards, source_label="open_box_deals", min_discount=int(min_discount), limit=5)
        public_result = await maybe_post_public_deal_cards(
            bot=self.bot,
            guild_id=interaction.guild_id,
            cards=public_cards,
            source_label="open_box_deals",
            fallback_retailer="walmart",
            min_public_discount=int(min_discount),
        )

        summary = discord.Embed(
            title="📦 Walmart Open Box / Like-New Scan",
            description=(
                f"Routes checked: **{len(routes)}**\n"
                f"Products checked: **{len(candidates)}**\n"
                f"Structured condition deals found: **{len(cards)}**\n"
                f"Public-ready at **{int(min_discount)}%+**: **{len(public_cards)}**"
            ),
            color=discord.Color.orange() if public_cards else discord.Color.dark_gold(),
        )
        summary.add_field(
            name="Routes",
            value=", ".join(f"`{route}`" for route in routes)[:1024],
            inline=False,
        )
        if warnings:
            summary.add_field(name="⚠️ Notes", value="\n".join(f"• {w}" for w in warnings[:4])[:1024], inline=False)
        add_public_posting_field(summary, public_result)
        if not cards:
            summary.add_field(
                name="No public condition deal yet",
                value="I found no item where Walmart/API returned current price, condition, trusted reference price, discount math, and a direct product URL together.",
                inline=False,
            )
        await send_summary_and_card_batches(interaction, summary=summary, cards=cards[:5], ephemeral=True)


def build_open_box_cards(result: ProviderScanResult, *, min_discount: int) -> list[DealCard]:
    cards: list[DealCard] = []
    for candidate in result.candidates:
        lane = normalized_lane(candidate)
        if lane not in {LANE_OPEN_BOX_LIKE_NEW, LANE_RESTORED_REFURBISHED}:
            continue
        condition = normalized_condition(getattr(candidate, "api_condition", None) or getattr(candidate, "condition", None))
        if not condition:
            continue
        discount = structured_discount(candidate) or 0.0
        if discount < int(min_discount):
            continue
        if current_price(candidate) is None or reference_price(candidate) is None:
            continue

        decision = evaluate_candidate(candidate)
        deal = decision.deal
        choices = product_link_choices(
            retailer=deal.retailer,
            product_url=deal.product_url,
            title=deal.title,
            product_id=candidate.product_id,
            sku=deal.sku,
            asin=deal.asin,
        )
        embed = deal_scanner.build_deal_card_embed(candidate, deal, decision, discount, choices)
        variant_attributes = dict(getattr(candidate, "variant_attributes", {}) or {})
        card = DealCard(
            embed=embed,
            url=deal.product_url,
            label=short_button_label(deal.title),
            score=decision.anomaly.score,
            discount=discount,
            link_choices=choices,
            deal_lane=lane,
            api_current_price=current_price(candidate),
            api_reference_price=reference_price(candidate),
            api_discount_percent=discount,
            api_condition=condition,
            api_condition_path=getattr(candidate, "api_condition_path", None) or variant_attributes.get("conditionPath") or "condition",
            api_reference_path=getattr(candidate, "api_reference_path", None) or variant_attributes.get("trustedReferenceSource") or "trustedReferencePrice",
            api_price_path=getattr(candidate, "api_price_path", None) or variant_attributes.get("currentPriceSource") or "currentPrice",
            seller_name=deal.seller_name,
            fulfillment_type=deal.fulfillment_type,
            direct_product_url=getattr(candidate, "direct_product_url", None) or deal.product_url,
            variant_attributes=variant_attributes,
        )
        card.retailer = deal.retailer
        card.should_alert = True
        card.current_price = current_price(candidate)
        card.typical_price = reference_price(candidate)
        card.selected_offer_id = deal.selected_offer_id
        card.sku = deal.sku
        card.upc = deal.upc
        cards.append(card)
    cards.sort(key=lambda card: (float(getattr(card, "api_discount_percent", 0) or 0), int(getattr(card, "score", 0) or 0)), reverse=True)
    return cards
