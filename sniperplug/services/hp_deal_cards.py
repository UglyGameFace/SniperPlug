from __future__ import annotations

import os
from typing import Any

import discord

from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.candidate_pipeline import evaluate_candidate


# Components V2 pilot (Discord, April 2025): when this flag is on, HP deal
# cards also carry a LayoutView rendering (Container/Section/TextDisplay)
# of the exact same data as the classic embed, and the HP public poster
# sends that instead of the embed. Default OFF: production rendering is
# unchanged until the flag is deliberately flipped.
COMPONENTS_V2_CARDS_ENV = "SNIPERPLUG_COMPONENTS_V2_CARDS"


def components_v2_cards_enabled() -> bool:
    return (os.getenv(COMPONENTS_V2_CARDS_ENV) or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


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
    availability_lines = []
    if candidate.stock_status:
        availability_lines.append(f"Stock: **{candidate.stock_status}**")
    if candidate.can_add_to_cart is True:
        availability_lines.append("Add to cart: **Confirmed**")
    if availability_lines:
        embed.add_field(
            name="📦 Availability",
            value="\n".join(availability_lines),
            inline=False,
        )
    promotion = str(candidate.variant_attributes.get("hpPromotionText") or "").strip()
    if promotion:
        embed.add_field(name="🎟️ HP promotion", value=promotion[:1024], inline=False)
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
    # Components V2 pilot: None unless SNIPERPLUG_COMPONENTS_V2_CARDS is on,
    # so every existing send path keeps using card.embed untouched.
    card.layout_view = (
        _hp_card_layout(
            title=candidate.title,
            url=candidate.direct_product_url or candidate.product_url,
            label=card.label,
            image_url=candidate.image_url,
            current=current,
            reference=reference,
            reference_label=reference_label,
            discount=discount,
            savings=savings,
            availability_lines=availability_lines,
            promotion=promotion,
        )
        if components_v2_cards_enabled()
        else None
    )
    return card


def _hp_card_layout(
    *,
    title: str,
    url: str,
    label: str,
    image_url: str | None,
    current: float | None,
    reference: float | None,
    reference_label: str,
    discount: float,
    savings: float | None,
    availability_lines: list[str],
    promotion: str,
) -> discord.ui.LayoutView:
    """Components V2 rendering of the HP card, same data as the embed."""

    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_colour=discord.Color.green())
    # A Section holds at most 3 text components, so price + reference
    # share one TextDisplay (newline-separated, mirroring the embed fields).
    price_line = (
        f"💵 HP.com price: **${current:,.2f}**" if current is not None else "💵 HP.com price: Not returned"
    )
    reference_line = (
        f"🏷️ {reference_label}: ${reference:,.2f}" if reference is not None else f"🏷️ {reference_label}: Not returned"
    )
    texts = [
        discord.ui.TextDisplay(f"## {title}"),
        discord.ui.TextDisplay(f"{price_line}\n{reference_line}"),
        discord.ui.TextDisplay(
            f"📉 Savings: **{discount:.0f}% off** • save **${savings:,.2f}**"
            if savings is not None
            else f"📉 Savings: **{discount:.0f}% off**"
        ),
    ]
    if image_url:
        container.add_item(
            discord.ui.Section(*texts, accessory=discord.ui.Thumbnail(image_url))
        )
    else:
        for text in texts:
            container.add_item(text)
    if availability_lines or promotion:
        container.add_item(discord.ui.Separator())
        if availability_lines:
            container.add_item(
                discord.ui.TextDisplay("📦 Availability\n" + "\n".join(availability_lines))
            )
        if promotion:
            container.add_item(discord.ui.TextDisplay(f"🎟️ HP promotion\n{promotion[:1024]}"))
    container.add_item(discord.ui.Separator())
    container.add_item(
        discord.ui.TextDisplay(
            "-# Prices and availability can change. Open the exact HP.com product before buying."
        )
    )
    row = discord.ui.ActionRow()
    row.add_item(discord.ui.Button(style=discord.ButtonStyle.link, label=label, url=url))
    container.add_item(row)
    view.add_item(container)
    return view


def _float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _button_label(value: str) -> str:
    text = " ".join(str(value or "HP deal").split())
    return text[:77] + "..." if len(text) > 80 else text
