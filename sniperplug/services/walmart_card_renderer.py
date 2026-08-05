from __future__ import annotations

from collections.abc import Iterable

import discord

from sniperplug.cogs import deal_scanner
from sniperplug.models.candidate import SourceCandidate
from sniperplug.models.deal import NormalizedDeal
from sniperplug.providers.base import ProviderScanResult
from sniperplug.services.candidate_pipeline import evaluate_candidate
from sniperplug.services.price_proof import verified_deal_value
from sniperplug.services.safe_links import product_link_choices
from sniperplug.services.variant_identity import derived_variant_identity


MAX_FIELD_VALUE = 1024
SAFE_FIELD_VALUE = 950


def build_walmart_cards(result: ProviderScanResult, min_discount: int, alerts_only: bool) -> list[deal_scanner.DealCard]:
    """Build Walmart cards using only API-returned fields and labeled API math.

    The card surface must not present guesses as facts. Heuristics may decide
    sorting/filtering, but visible text is restricted to:
    - direct fields normalized from the Walmart API payload
    - API-derived math from those fields, labeled as derived
    - explicit notes that a field was not returned/trusted
    """
    cards: list[deal_scanner.DealCard] = []
    for candidate in result.candidates:
        decision = evaluate_candidate(candidate)
        deal = decision.deal
        proof = verified_deal_value(deal)
        discount = proof.discount_percent
        has_coupon_or_cash = bool(proof.effective_value_notes)

        if discount is None:
            if not has_coupon_or_cash or min_discount > 25:
                continue
            discount_for_sort = 0.0
        else:
            if discount < min_discount:
                continue
            discount_for_sort = discount

        if alerts_only and not decision.should_alert:
            continue

        choices = product_link_choices(
            retailer=deal.retailer,
            product_url=deal.product_url,
            title=deal.title,
            product_id=candidate.product_id,
            sku=deal.sku,
            asin=deal.asin,
        )
        card = deal_scanner.DealCard(
            embed=build_deal_card_embed(candidate, deal, proof, choices),
            url=deal.product_url,
            label=deal_scanner.short_button_label(deal.title),
            score=decision.anomaly.score,
            discount=discount_for_sort,
            link_choices=choices,
        )
        card.retailer = deal.retailer
        # Value-only Walmart cards are valid when the API proves a coupon or
        # Walmart Cash value even without a trusted was-price markdown.
        card.should_alert = decision.should_alert and (discount is not None or has_coupon_or_cash)
        card.current_price = deal.current_price
        card.item_price = deal.item_price
        card.shipping_cost = deal.shipping_cost
        card.delivered_price = deal.delivered_price
        card.shipping_status = deal.shipping_status
        card.selected_offer_id = deal.selected_offer_id or derived_variant_identity(
            variant_label=deal.variant_label,
            variant_attributes=deal.variant_attributes,
            pack_size=deal.pack_size,
            color=deal.color,
            platform=deal.platform,
            model=deal.model,
            seller_name=deal.seller_name,
            fulfillment_type=deal.fulfillment_type,
            condition=deal.condition,
        )
        card.sku = deal.sku
        card.upc = deal.upc
        card.variant_label = deal.variant_label
        card.variant_attributes = dict(deal.variant_attributes or {})
        card.pack_size = deal.pack_size
        card.color = deal.color
        card.platform = deal.platform
        card.model = deal.model
        card.seller_name = deal.seller_name
        card.fulfillment_type = deal.fulfillment_type
        card.condition = deal.condition
        cards.append(card)
    return cards


def build_deal_card_embed(candidate: SourceCandidate, deal: NormalizedDeal, proof, link_choices=()) -> discord.Embed:
    discount = proof.discount_percent or 0.0
    title_prefix = f"{discount:.0f}% API-derived markdown" if proof.discount_percent is not None else "Walmart API result"
    embed = discord.Embed(
        title=f"{deal_scanner.heat_emoji(discount, deal.current_price)} {title_prefix} • {deal_scanner.trim_title(deal.title, 68)}",
        url=deal.product_url,
        color=deal_scanner.embed_color(discount, 0),
    )
    if deal.image_url:
        embed.set_thumbnail(url=deal.image_url)

    append_field_chunks(embed, "💰 Price from Walmart API", price_lines(candidate, deal, proof))
    append_field_chunks(embed, "🧾 Product identity from API", identity_lines(candidate, deal))
    append_field_chunks(embed, "🏷️ Offer / seller from API", offer_lines(candidate, deal))
    append_field_chunks(embed, "📦 Fulfillment / stock from API", fulfillment_lines(candidate, deal))
    append_field_chunks(embed, "🎯 Variant / option from API", variant_lines(candidate, deal))
    append_field_chunks(embed, "💵 Coupon / Walmart Cash from API", value_lines(deal, proof))
    append_field_chunks(embed, "🧮 API evidence used", evidence_lines(candidate, deal, proof))

    link_block = deal_scanner.product_link_block(link_choices, fallback_url=deal.product_url)
    if link_block:
        embed.add_field(name="🔗 Links", value=link_block, inline=False)

    footer_bits = ["No guessed card values"]
    if deal.sku:
        footer_bits.append(f"SKU: {deal.sku}")
    if deal.upc:
        footer_bits.append(f"UPC: {deal.upc}")
    embed.set_footer(text=truncate(" • ".join(footer_bits), 180))
    return embed


def price_lines(candidate: SourceCandidate, deal: NormalizedDeal, proof) -> list[str]:
    attrs = deal.variant_attributes or {}
    lines: list[str] = []
    item_price = (
        deal.item_price
        if deal.item_price is not None
        else float_or_none(attrs.get("itemPrice"))
    )
    shipping_cost = deal.shipping_cost
    if shipping_cost is None:
        shipping_cost = float_or_none(attrs.get("shippingCost"))
    delivered = (
        deal.delivered_price
        if deal.delivered_price is not None
        else float_or_none(attrs.get("deliveredPrice"))
    )
    shipping_status = str(deal.shipping_status or attrs.get("shippingStatus") or "").strip().lower()

    if item_price is not None:
        item_source = attrs.get("selectedOfferItemPriceSource")
        source_text = f" `{item_source}`" if item_source else ""
        lines.append(f"• Selected-offer item price: **{money(item_price)}**{source_text}")
    if shipping_status == "free":
        source = deal.shipping_source or attrs.get("shippingSource") or attrs.get("selectedOfferShippingSource")
        source_text = f" `{source}`" if source else ""
        lines.append(f"• Shipping: **FREE**{source_text}")
    elif shipping_cost is not None:
        source = deal.shipping_source or attrs.get("shippingSource") or attrs.get("selectedOfferShippingSource")
        source_text = f" `{source}`" if source else ""
        lines.append(f"• Shipping: **{money(shipping_cost)}**{source_text}")
    elif shipping_status:
        lines.append(f"• Shipping: **{shipping_status.replace('_', ' ')}**")

    if deal.current_price is None:
        lines.append("• Payable price: **blocked — selected-offer shipping was not verified**")
    else:
        current_source = attrs.get("currentPriceSource") or api_signal(candidate.signals, "Walmart current price source")
        source_text = f" `{current_source}`" if current_source else ""
        label = "Delivered total used for deal math" if delivered is not None else "Current"
        lines.append(f"• {label}: **{money(deal.current_price)}**{source_text}")

    alternate_min = float_or_none(attrs.get("alternateSellerMinPrice"))
    if alternate_min is not None:
        alternate_source = attrs.get("alternateSellerMinPriceSource")
        source_text = f" `{alternate_source}`" if alternate_source else ""
        lines.append(f"• Other-seller minimum: **{money(alternate_min)}**{source_text} — context only, not this selected offer")

    if proof.discount_percent is not None and deal.typical_price:
        source = attrs.get("trustedReferenceSource") or api_signal(candidate.signals, "Walmart reference price source")
        source_text = f" `{source}`" if source else ""
        lines.append(f"• Was/typical: ~~{money(deal.typical_price)}~~{source_text}")
        lines.append(f"• Delivered-price savings: **{money(proof.savings_amount)} ({proof.discount_percent:.0f}%)**")
        lines.append("• Discount math status: **trusted Walmart reference vs selected-offer payable total**")
    else:
        context_price = float_or_none(attrs.get("referenceContextPrice"))
        context_source = attrs.get("referenceContextSource")
        if context_price and deal.current_price is not None and context_price > deal.current_price:
            source_text = f" `{context_source}`" if context_source else ""
            lines.append(f"• Reference shown: **{money(context_price)}**{source_text}")
            lines.append("• Discount math status: **reference shown but not counted for % off**")
        else:
            lines.append("• Was/typical: **not returned or not trusted by Walmart API**")
            lines.append("• Discount math status: **no trusted Walmart reference**")

    if deal.coupon_savings:
        lines.append(f"• Coupon API value: **{money(deal.coupon_savings)}**")
    cash = float_or_none(attrs.get("walmartCashSavings"))
    if cash:
        lines.append(f"• Walmart Cash API value: **{money(cash)}**")
    return lines


def price_block(deal: NormalizedDeal, proof) -> str:
    """Backward-compatible rendered price block used by older tests."""
    attrs = deal.variant_attributes or {}
    fake_candidate = SourceCandidate(
        source_key="walmart",
        retailer=deal.retailer or "Walmart",
        title=deal.title,
        product_url=deal.product_url,
        current_price=deal.current_price,
        typical_price=deal.typical_price,
        item_price=deal.item_price,
        shipping_cost=deal.shipping_cost,
        delivered_price=deal.delivered_price,
        shipping_status=deal.shipping_status,
        shipping_source=deal.shipping_source,
        variant_attributes=attrs,
    )
    return "\n".join(price_lines(fake_candidate, deal, proof))


def api_detail_lines(candidate: SourceCandidate, deal: NormalizedDeal) -> list[str]:
    """Backward-compatible compact Walmart API details used by older tests."""
    attrs = deal.variant_attributes or {}
    lines: list[str] = []
    if deal.sku:
        lines.append(f"SKU `{deal.sku}`")
    if deal.upc:
        lines.append(f"UPC `{deal.upc}`")
    if candidate.seller_name or deal.seller_name or attrs.get("seller"):
        lines.append(f"Seller **{candidate.seller_name or deal.seller_name or attrs.get('seller')}**")
    if attrs.get("rollback") is not None:
        lines.append(f"Rollback: **{attrs.get('rollback')}**")
    if attrs.get("referencePriceTrusted") is not None:
        lines.append(f"Reference trusted: **{attrs.get('referencePriceTrusted')}**")
    trusted_price = float_or_none(attrs.get("trustedReferencePrice")) or deal.typical_price
    trusted_source = attrs.get("trustedReferenceSource")
    if trusted_price:
        source_text = f" `{trusted_source}`" if trusted_source else ""
        lines.append(f"Trusted was/typical: **{money(trusted_price)}**{source_text}")
    coupon = float_or_none(attrs.get("couponSavings"))
    if coupon:
        lines.append(f"Coupon API value: **{money(coupon)}**")
    cash = float_or_none(attrs.get("walmartCashSavings"))
    if cash:
        lines.append(f"Walmart Cash API value: **{money(cash)}**")
    lines.extend(identity_lines(candidate, deal))
    lines.extend(offer_lines(candidate, deal))
    lines.extend(fulfillment_lines(candidate, deal))
    lines.extend(variant_lines(candidate, deal))
    return deal_scanner.dedupe_lines(lines)


def api_evidence_lines(candidate: SourceCandidate, deal: NormalizedDeal, proof) -> list[str]:
    """Backward-compatible alias for the native evidence line builder."""
    return evidence_lines(candidate, deal, proof)


def identity_lines(candidate: SourceCandidate, deal: NormalizedDeal) -> list[str]:
    attrs = deal.variant_attributes or {}
    lines = [
        maybe_line("Item ID / SKU", deal.sku),
        maybe_line("UPC", deal.upc),
        maybe_line("Brand", attrs.get("brand")),
        maybe_line("Model", deal.model or attrs.get("model") or attrs.get("modelNumber")),
        maybe_line("Category", attrs.get("category")),
        maybe_line("Category node", attrs.get("categoryNode")),
        maybe_line("Rating", attrs.get("rating")),
        maybe_line("Reviews", attrs.get("reviews")),
        maybe_line("API product id", candidate.product_id),
    ]
    return compact(lines)


def offer_lines(candidate: SourceCandidate, deal: NormalizedDeal) -> list[str]:
    attrs = deal.variant_attributes or {}
    lines = [
        maybe_line("Selected offer ID", deal.selected_offer_id),
        maybe_line("Seller", candidate.seller_name or deal.seller_name or attrs.get("seller")),
        maybe_line("Seller ID", attrs.get("sellerId")),
        maybe_line("Selected-offer node", attrs.get("selectedOfferNodeSource")),
        maybe_line("Walmart seller", attrs.get("walmartSeller")),
        maybe_line("Marketplace", attrs.get("selectedOfferMarketplace") or attrs.get("marketplace")),
        maybe_line("Offer type", attrs.get("offerType")),
        maybe_line("Condition", candidate.condition or deal.condition or attrs.get("condition")),
        maybe_line("Max order qty", attrs.get("maxOrderQty")),
    ]
    return compact(lines)


def fulfillment_lines(candidate: SourceCandidate, deal: NormalizedDeal) -> list[str]:
    attrs = deal.variant_attributes or {}
    add_to_cart = "yes" if candidate.can_add_to_cart is True else "no/unknown" if candidate.can_add_to_cart is False else None
    item_price = (
        deal.item_price
        if deal.item_price is not None
        else float_or_none(attrs.get("itemPrice"))
    )
    shipping_cost = deal.shipping_cost
    if shipping_cost is None:
        shipping_cost = float_or_none(attrs.get("shippingCost"))
    delivered = (
        deal.delivered_price
        if deal.delivered_price is not None
        else float_or_none(attrs.get("deliveredPrice"))
    )
    shipping_status = deal.shipping_status or attrs.get("shippingStatus")
    shipping_display = "free" if shipping_status == "free" else money(shipping_cost) if shipping_cost is not None else shipping_status
    lines = [
        maybe_line("Stock", candidate.stock_status),
        maybe_line("Add-to-cart", add_to_cart),
        maybe_line("Available online", attrs.get("availableOnline")),
        maybe_line("Fulfillment", candidate.fulfillment_type or deal.fulfillment_type or attrs.get("fulfillment")),
        maybe_line("Selected item price", money(item_price) if item_price is not None else None),
        maybe_line("Shipping", shipping_display),
        maybe_line("Delivered total", money(delivered) if delivered is not None else None),
        maybe_line("Shipping proof", deal.shipping_source or attrs.get("shippingSource") or attrs.get("selectedOfferShippingSource")),
        maybe_line("Payable-price status", attrs.get("selectedOfferPublicPriceStatus")),
        maybe_line("Ship to store", attrs.get("shipToStore")),
        maybe_line("Free ship to store", attrs.get("freeShipToStore")),
        maybe_line("2-3 day shipping", attrs.get("twoThreeDayShipping")),
    ]
    return compact(lines)


def variant_lines(candidate: SourceCandidate, deal: NormalizedDeal) -> list[str]:
    attrs = deal.variant_attributes or {}
    lines = [
        maybe_line("Selected option", deal.variant_label),
        maybe_line("Pack", attrs.get("packSize") or deal.pack_size),
        maybe_line("Size", attrs.get("size")),
        maybe_line("Unit", attrs.get("unitSize") or attrs.get("unit")),
        maybe_line("Color", attrs.get("color") or deal.color),
        maybe_line("Platform", attrs.get("platform") or deal.platform),
        maybe_line("Parent title", deal.parent_title if deal.parent_title and deal.parent_title != deal.title else None),
        maybe_line("Option warning", candidate.option_mismatch_warning or deal.option_mismatch_warning),
    ]
    return compact(lines)


def value_lines(deal: NormalizedDeal, proof) -> list[str]:
    attrs = deal.variant_attributes or {}
    lines = [f"• {line}" for line in proof.effective_value_notes]
    coupon = float_or_none(attrs.get("couponSavings"))
    cash = float_or_none(attrs.get("walmartCashSavings"))
    if coupon and not any("coupon" in line.lower() for line in lines):
        lines.append(f"• Coupon API value: **{money(coupon)}**")
    if cash and not any("cash" in line.lower() for line in lines):
        lines.append(f"• Walmart Cash API value: **{money(cash)}**")
    return lines


def evidence_lines(candidate: SourceCandidate, deal: NormalizedDeal, proof) -> list[str]:
    attrs = deal.variant_attributes or {}
    lines: list[str] = []
    for label, key in (
        ("Current price source", "currentPriceSource"),
        ("Selected item-price source", "selectedOfferItemPriceSource"),
        ("Selected shipping source", "selectedOfferShippingSource"),
        ("Selected delivered-price source", "selectedOfferDeliveredPriceSource"),
        ("Trusted reference source", "trustedReferenceSource"),
        ("Reference context source", "referenceContextSource"),
    ):
        value = attrs.get(key)
        if value:
            lines.append(f"• {label}: `{value}`")
    for prefix in (
        "Walmart current price source",
        "Walmart reference price source",
        "Walmart reference shown",
        "ignored low-confidence Walmart reference price",
        "selected option",
        "Walmart coupon detected",
        "Walmart Cash detected",
        "Walmart selected marketplace offer blocked",
        "Walmart alternate seller minimum price blocked",
    ):
        found = api_signal(candidate.signals, prefix, keep_prefix=True)
        if found:
            lines.append(f"• {found}")
    if attrs.get("alternateSellerMinPrice"):
        lines.append("• Alternate seller minimum was retained as context only and did not participate in selected-offer math")
    if attrs.get("msrp"):
        lines.append(f"• MSRP returned: `{attrs['msrp']}` — not counted unless trusted reference rules pass")
    if proof.discount_percent is None and not lines:
        lines.append("• No trusted Walmart markdown reference was returned for discount math")
    return deal_scanner.dedupe_lines(lines)


def append_field_chunks(embed: discord.Embed, name: str, lines: Iterable[str]) -> None:
    clean_lines = [line for line in lines if line]
    if not clean_lines:
        return
    chunks: list[str] = []
    current = ""
    for line in clean_lines:
        safe_line = truncate(line, SAFE_FIELD_VALUE)
        candidate = safe_line if not current else current + "\n" + safe_line
        if len(candidate) > SAFE_FIELD_VALUE:
            if current:
                chunks.append(current)
            current = safe_line
        else:
            current = candidate
    if current:
        chunks.append(current)
    for index, chunk in enumerate(chunks[:4]):
        field_name = name if index == 0 else f"{name} cont. {index + 1}"
        embed.add_field(name=field_name, value=chunk[:MAX_FIELD_VALUE], inline=False)


def maybe_line(label: str, value) -> str | None:
    if value is None or value == "":
        return None
    return f"• {label}: **{truncate(str(value), 120)}**"


def compact(lines: Iterable[str | None]) -> list[str]:
    return [line for line in lines if line]


def api_signal(signals, prefix: str, *, keep_prefix: bool = False) -> str | None:
    for signal in signals or ():
        text = str(signal)
        if text.startswith(prefix):
            return text if keep_prefix else text.split(":", 1)[1].strip() if ":" in text else text
    return None


def strict_discount_percent(current_price: float | None, typical_price: float | None) -> float | None:
    if current_price is None or not typical_price or typical_price <= current_price:
        return None
    return max(0.0, (typical_price - current_price) / typical_price * 100)


def money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"


def float_or_none(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def truncate(value: str, limit: int) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
