from __future__ import annotations

from typing import Any

import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.candidate_pipeline import evaluate_candidate


def build_target_deal_card(candidate: SourceCandidate, *, event_key: str = "") -> DealCard:
    decision = evaluate_candidate(candidate)
    deal = decision.deal
    current = _float(candidate.api_current_price or candidate.current_price)
    reference = _float(candidate.api_reference_price or candidate.typical_price)
    discount = _float(candidate.api_discount_percent)
    if discount is None and current is not None and reference is not None and reference > current:
        discount = round((reference - current) / reference * 100.0, 2)
    discount = discount or 0.0
    savings = round(reference - current, 2) if current is not None and reference is not None else None
    attrs = dict(candidate.variant_attributes or {})
    price_error = str(attrs.get("targetPriceErrorLane") or "").lower() == "yes"

    embed = discord.Embed(
        title=candidate.title,
        url=candidate.direct_product_url or candidate.product_url,
        description=(
            "**Exact Target structured-offer match** — TCIN, seller, price, and local fulfillment were independently confirmed."
        ),
        color=discord.Color.red(),
    )
    if candidate.image_url:
        embed.set_thumbnail(url=candidate.image_url)
    embed.add_field(
        name="💵 Target price",
        value=f"**${current:,.2f}**" if current is not None else "Not returned",
        inline=True,
    )
    reference_label = str(attrs.get("referencePriceLabel") or "Verified reference")
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
        name="🎯 Exact identity",
        value=(
            f"TCIN: `{candidate.product_id or 'unknown'}`\n"
            f"Store: `{attrs.get('targetStoreId') or 'unknown'}` • ZIP: `{attrs.get('targetZip') or 'unknown'}`\n"
            f"Seller: **{candidate.seller_name or 'Target'}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="📦 Fulfillment",
        value=(
            f"Shipping: **{_availability(attrs.get('targetShippingAvailable'))}**\n"
            f"Pickup/Drive Up: **{_availability(attrs.get('targetPickupAvailable'))}**\n"
            f"Add to cart: **{'confirmed' if candidate.can_add_to_cart is True else 'unavailable' if candidate.can_add_to_cart is False else 'not returned'}**"
        ),
        inline=False,
    )
    promotion = str(attrs.get("targetPromotionText") or "").strip()
    if promotion:
        embed.add_field(name="⭕ Target promotion", value=promotion[:1024], inline=False)
    if price_error:
        embed.add_field(
            name="🚨 Price-error fast lane",
            value=(
                f"Reference value: **${reference:,.2f}+** policy qualified\n"
                f"Verified markdown: **{discount:.0f}%**"
                if reference is not None
                else f"Verified markdown: **{discount:.0f}%**"
            ),
            inline=False,
        )
    embed.add_field(
        name="✅ Verification",
        value=(
            "Current price: `Target RedSky product.price.current_retail`\n"
            "Reference: Target regular price when returned; otherwise a previous exact Target observation.\n"
            "The alert is blocked unless a second exact TCIN and fulfillment request agrees."
        ),
        inline=False,
    )
    embed.set_footer(
        text="Target prices and local availability can change. Open the exact product before buying."
    )

    card = DealCard(
        embed=embed,
        url=deal.product_url,
        label=_button_label(deal.title),
        score=max(100, int(decision.anomaly.score)),
        discount=discount,
    )
    card.retailer = "Target"
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
    card.can_add_to_cart = candidate.can_add_to_cart
    card.stock_status = candidate.stock_status
    card.direct_product_url = candidate.direct_product_url or candidate.product_url
    card.variant_attributes = attrs
    card.sku = candidate.sku
    card.selected_offer_id = candidate.selected_offer_id
    card.candidate = candidate
    card.deal = deal
    if event_key:
        card.public_post_key = event_key
    return card


def _availability(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "available" if text == "yes" else "unavailable" if text == "no" else "not returned"


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _button_label(value: str) -> str:
    text = " ".join(str(value or "Target deal").split())
    return text[:77] + "..." if len(text) > 80 else text
