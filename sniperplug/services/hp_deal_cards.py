from __future__ import annotations

from typing import Any

import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.candidate_pipeline import evaluate_candidate


def build_hp_deal_card(candidate: SourceCandidate, *, event_key: str = "") -> DealCard:
    decision = evaluate_candidate(candidate)
    deal = decision.deal
    current = _float(candidate.api_current_price or candidate.current_price)
    reference = _float(candidate.api_reference_price or candidate.typical_price)
    discount = _float(candidate.api_discount_percent)
    if discount is None and current is not None and reference is not None and reference > current:
        discount = round((reference - current) / reference * 100.0, 2)
    discount = discount or 0.0
    savings = round(reference - current, 2) if current is not None and reference is not None else None

    embed = discord.Embed(
        title=candidate.title,
        url=candidate.direct_product_url or candidate.product_url,
        description=(
            "**Exact HP.com structured-price match** — catalog entry and part number were checked before this alert."
        ),
        color=discord.Color.green(),
    )
    if candidate.image_url:
        embed.set_thumbnail(url=candidate.image_url)
    embed.add_field(
        name="💵 HP.com price",
        value=f"**${current:,.2f}**" if current is not None else "Not returned",
        inline=True,
    )
    reference_label = str(candidate.variant_attributes.get("referencePriceLabel") or "Verified reference")
    embed.add_field(
        name=f"🏷️ {reference_label}",
        value=f"${reference:,.2f}" if reference is not None else "Not returned",
        inline=True,
    )
    embed.add_field(
        name="📉 Savings",
        value=(
            f"**{discount:.0f}% off** • save **${savings:,.2f}**"
            if savings is not None
            else f"**{discount:.0f}% off**"
        ),
        inline=True,
    )
    embed.add_field(
        name="🔎 Exact identity",
        value=(
            f"SKU: `{candidate.sku or 'unknown'}`\n"
            f"Catalog entry: `{candidate.product_id or 'unknown'}`\n"
            f"Seller: **{candidate.seller_name or 'HP.com'}**"
        ),
        inline=False,
    )
    stock = candidate.stock_status or "Not returned"
    cart = (
        "Confirmed" if candidate.can_add_to_cart is True else
        "Unavailable" if candidate.can_add_to_cart is False else
        "Not returned"
    )
    embed.add_field(
        name="📦 Availability",
        value=f"Stock: **{stock}**\nAdd to cart: **{cart}**",
        inline=False,
    )
    promotion = str(candidate.variant_attributes.get("hpPromotionText") or "").strip()
    if promotion:
        embed.add_field(name="🎟️ HP promotion", value=promotion[:1024], inline=False)
    embed.add_field(
        name="✅ Verification",
        value=(
            "Price source: `HPServices priceData.price`\n"
            "Reference: HP MSRP when `lPrice` is supplied; otherwise a previous exact HP.com observation.\n"
            "A visible-page `$0.00` placeholder is never accepted as a deal price."
        ),
        inline=False,
    )
    embed.set_footer(text="Prices and availability can change. Open the exact HP.com product before buying.")

    card = DealCard(
        embed=embed,
        url=deal.product_url,
        label=_button_label(deal.title),
        score=max(100, int(decision.anomaly.score)),
        discount=discount,
    )
    card.retailer = "HP"
    card.current_price = current
    card.typical_price = reference
    card.should_alert = True
    card.deal_lane = candidate.deal_lane or "verified_markdown"
    card.api_current_price = current
    card.api_reference_price = reference
    card.api_discount_percent = discount
    card.api_condition = candidate.api_condition or candidate.condition
    card.api_condition_path = candidate.api_condition_path
    card.api_reference_path = candidate.api_reference_path
    card.api_price_path = candidate.api_price_path
    card.seller_name = candidate.seller_name
    card.fulfillment_type = candidate.fulfillment_type
    card.direct_product_url = candidate.direct_product_url or candidate.product_url
    card.variant_attributes = dict(candidate.variant_attributes or {})
    card.sku = candidate.sku
    card.selected_offer_id = candidate.selected_offer_id
    card.candidate = candidate
    card.deal = deal
    if event_key:
        card.public_post_key = event_key
    return card


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _button_label(value: str) -> str:
    text = " ".join(str(value or "HP deal").split())
    return text[:77] + "..." if len(text) > 80 else text
