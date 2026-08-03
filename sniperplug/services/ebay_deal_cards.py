from __future__ import annotations

from typing import Any

import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.candidate_pipeline import evaluate_candidate


def build_ebay_deal_card(
    candidate: SourceCandidate,
    *,
    event_key: str = "",
) -> DealCard:
    decision = evaluate_candidate(candidate)
    deal = decision.deal
    current = _float(candidate.api_current_price or candidate.current_price)
    reference = _float(candidate.api_reference_price or candidate.typical_price)
    discount = _float(candidate.api_discount_percent)
    if discount is None and current is not None and reference is not None and reference > current:
        discount = round((reference - current) / reference * 100.0, 2)
    discount = discount or 0.0
    savings = (
        round(reference - current, 2)
        if current is not None and reference is not None
        else None
    )
    attrs = dict(candidate.variant_attributes or {})
    item_price = _float(attrs.get("ebayItemPrice"))
    shipping = _float_or_zero(attrs.get("ebayShippingPrice"))
    reference_source = str(
        candidate.api_reference_path
        or attrs.get("ebayReferenceSource")
        or ""
    )
    source_label = (
        "Exact listing history"
        if "listing_history" in reference_source
        else "Exact-product market median"
    )

    embed = discord.Embed(
        title=candidate.title,
        url=candidate.direct_product_url or candidate.product_url,
        description=(
            "**Verified eBay extreme drop** — the exact listing, seller, condition, "
            "delivered price, and product identity were rechecked before this alert."
        ),
        color=discord.Color.green(),
    )
    if candidate.image_url:
        embed.set_thumbnail(url=candidate.image_url)
    embed.add_field(
        name="💵 Delivered price",
        value=f"**${current:,.2f}**" if current is not None else "Not returned",
        inline=True,
    )
    embed.add_field(
        name=f"🏷️ {source_label}",
        value=f"${reference:,.2f}" if reference is not None else "Not returned",
        inline=True,
    )
    embed.add_field(
        name="📉 Verified drop",
        value=(
            f"**{discount:.0f}% off** • save **${savings:,.2f}**"
            if savings is not None
            else f"**{discount:.0f}% off**"
        ),
        inline=True,
    )
    embed.add_field(
        name="🧾 Price breakdown",
        value=(
            f"Item: **${item_price:,.2f}**\n"
            f"Shipping: **{'FREE' if shipping == 0 else f'${shipping:,.2f}'}**"
            if item_price is not None
            else "Exact item/shipping breakdown unavailable"
        ),
        inline=False,
    )
    feedback_percentage = _float(attrs.get("ebaySellerFeedbackPercentage"))
    feedback_score = _int(attrs.get("ebaySellerFeedbackScore"))
    seller_bits = [f"Seller: **{candidate.seller_name or 'Not returned'}**"]
    if feedback_percentage is not None:
        seller_bits.append(f"Positive feedback: **{feedback_percentage:.1f}%**")
    if feedback_score is not None:
        seller_bits.append(f"Feedback score: **{feedback_score:,}**")
    embed.add_field(
        name="👤 Seller proof",
        value="\n".join(seller_bits),
        inline=False,
    )
    identity_lines = [
        f"eBay item: `{attrs.get('ebayItemId') or candidate.product_id or 'unknown'}`",
        f"Condition: **{candidate.condition or candidate.api_condition or 'unknown'}**",
    ]
    if candidate.upc:
        identity_lines.append(f"GTIN/UPC: `{candidate.upc}`")
    if candidate.sku:
        identity_lines.append(f"MPN: `{candidate.sku}`")
    if candidate.model:
        identity_lines.append(f"Model: `{candidate.model}`")
    embed.add_field(
        name="🔎 Exact identity",
        value="\n".join(identity_lines),
        inline=False,
    )
    comparable_count = _int(attrs.get("ebayComparableCount")) or 0
    proof_lines = [
        "Current price: `Browse item price + exact shipping cost`",
        "Confirmation: a second exact-item Browse request matched price, condition, and seller",
    ]
    if "listing_history" in reference_source:
        proof_lines.append(
            "Reference: SniperPlug's durable prior observations of this same eBay item"
        )
    else:
        proof_lines.append(
            f"Reference: median delivered price from **{comparable_count}** other exact-product listings in the same condition"
        )
    proof_lines.append(
        "Seller marketing/original price is never accepted as public proof by itself."
    )
    embed.add_field(
        name="✅ Verification",
        value="\n".join(proof_lines)[:1024],
        inline=False,
    )
    embed.set_footer(
        text="eBay prices, shipping, and availability can change. Open the exact listing before buying."
    )

    card = DealCard(
        embed=embed,
        url=deal.product_url,
        label=_button_label(deal.title),
        score=max(100, int(decision.anomaly.score)),
        discount=discount,
    )
    card.retailer = "eBay"
    card.current_price = current
    card.typical_price = reference
    card.should_alert = True
    card.deal_lane = candidate.deal_lane or "verified_ebay_price_drop"
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
    card.variant_attributes = attrs
    card.sku = candidate.sku
    card.upc = candidate.upc
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


def _float_or_zero(value: Any) -> float:
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _button_label(value: str) -> str:
    text = " ".join(str(value or "eBay deal").split())
    return text[:77] + "..." if len(text) > 80 else text
