from __future__ import annotations

from sniperplug.cogs import deal_scanner
from sniperplug.models.candidate import SourceCandidate
from sniperplug.models.deal import NormalizedDeal
from sniperplug.providers.base import ProviderScanResult
from sniperplug.services.candidate_pipeline import evaluate_candidate
from sniperplug.services.price_proof import verified_deal_value
from sniperplug.services.routing import route_label
from sniperplug.services.safe_links import product_link_choices


MAX_CARD_FIELD_CHARS = 900
MAX_DETAILS_FIELD_CHARS = 850


def build_walmart_cards(result: ProviderScanResult, min_discount: int, alerts_only: bool) -> list[deal_scanner.DealCard]:
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
            embed=build_deal_card_embed(candidate, deal, decision, proof, choices),
            url=deal.product_url,
            label=deal_scanner.short_button_label(deal.title),
            score=decision.anomaly.score,
            discount=discount_for_sort,
            link_choices=choices,
        )
        card.retailer = deal.retailer
        card.should_alert = decision.should_alert and discount is not None
        card.current_price = deal.current_price
        card.selected_offer_id = deal.selected_offer_id
        card.sku = deal.sku
        card.upc = deal.upc
        cards.append(card)
    return cards


def build_deal_card_embed(candidate: SourceCandidate, deal: NormalizedDeal, decision, proof, link_choices=()):
    discount = proof.discount_percent or 0.0
    score = decision.anomaly.score
    title_prefix = f"{discount:.0f}% OFF" if proof.discount_percent is not None else "VALUE WATCH"
    embed = deal_scanner.discord.Embed(
        title=f"{deal_scanner.heat_emoji(discount, deal.current_price)} {title_prefix} • {deal_scanner.trim_title(deal.title, 68)}",
        url=deal.product_url,
        color=deal_scanner.embed_color(discount, score),
    )
    if deal.image_url:
        embed.set_thumbnail(url=deal.image_url)

    embed.add_field(name="💰 Price", value=price_block(deal, proof), inline=False)

    compact_lines: list[str] = []
    compact_lines.append(f"Score: **{deal_scanner.friendly_score_level(decision.anomaly.level)}** `{score}/250`")
    compact_lines.append(f"Route: **{route_label(decision.route.route)}**")
    compact_lines.append(f"Would alert: **{'Yes' if decision.should_alert and proof.discount_percent is not None else 'No'}**")
    stock = compact_stock_line(candidate, deal)
    if stock:
        compact_lines.append(stock)
    option = compact_option_line(deal)
    if option:
        compact_lines.append(option)
    seller = compact_seller_line(candidate, deal)
    if seller:
        compact_lines.append(seller)
    embed.add_field(name="📊 Proof", value=truncate("\n".join(compact_lines), MAX_CARD_FIELD_CHARS), inline=False)

    value_lines = list(proof.effective_value_notes)
    if value_lines:
        embed.add_field(name="💵 Coupon / Cash", value=truncate("\n".join(f"• {line}" for line in value_lines), 350), inline=False)

    details = api_detail_lines(candidate, deal)
    if details:
        embed.add_field(name="🧾 Walmart API details", value=truncate("\n".join(details), MAX_DETAILS_FIELD_CHARS), inline=False)

    reason_lines = proof_lines_for(candidate, decision, proof)
    if reason_lines:
        embed.add_field(name="🔎 Why", value=truncate("\n".join(reason_lines[:3]), 500), inline=False)

    link_block = deal_scanner.product_link_block(link_choices, fallback_url=deal.product_url)
    if link_block:
        embed.add_field(name="🔗 Links", value=link_block, inline=False)

    footer_bits = [f"SKU: {deal.sku or 'n/a'}"]
    if deal.upc:
        footer_bits.append(f"UPC: {deal.upc}")
    footer_bits.append("trusted price proof required")
    embed.set_footer(text=truncate(" • ".join(footer_bits), 180))
    return embed


def price_block(deal: NormalizedDeal, proof) -> str:
    if deal.current_price is None:
        return "Current price unavailable"
    attrs = deal.variant_attributes or {}
    lines = [f"Current: **{money(deal.current_price)}**"]
    if proof.discount_percent is not None and deal.typical_price:
        source = attrs.get("trustedReferenceSource")
        source_text = f" `{source}`" if source else ""
        lines.append(f"Was/typical: ~~{money(deal.typical_price)}~~{source_text}")
        lines.append(f"Verified save: **{money(proof.savings_amount)} ({proof.discount_percent:.0f}%)**")
        lines.append("Proof: **trusted Walmart markdown reference**")
    else:
        context_price = float_or_none(attrs.get("referenceContextPrice"))
        context_source = attrs.get("referenceContextSource")
        if context_price and context_price > deal.current_price:
            source_text = f" `{context_source}`" if context_source else ""
            lines.append(f"Reference shown: **{money(context_price)}**{source_text}")
            lines.append("Was/typical: **not counted for % off**")
        else:
            lines.append("Was/typical: **not returned/trusted**")
        lines.append("Proof: **no trusted markdown proof**")
    if deal.coupon_savings:
        lines.append(f"Coupon: **{money(deal.coupon_savings)}**")
    cash = float_or_none(attrs.get("walmartCashSavings"))
    if cash:
        lines.append(f"Walmart Cash: **{money(cash)} value**")
    return truncate("\n".join(lines), 650)


def compact_stock_line(candidate: SourceCandidate, deal: NormalizedDeal) -> str | None:
    parts: list[str] = []
    if candidate.stock_status:
        parts.append(candidate.stock_status[:40])
    if candidate.can_add_to_cart is True:
        parts.append("add-to-cart seen")
    elif candidate.can_add_to_cart is False:
        parts.append("cart not confirmed")
    if deal.variant_attributes.get("availableOnline") == "yes":
        parts.append("online available")
    return "Stock: " + ", ".join(parts[:3]) if parts else None


def compact_option_line(deal: NormalizedDeal) -> str | None:
    if deal.variant_label:
        return f"Option: {truncate(deal.variant_label, 80)}"
    attrs = deal.variant_attributes
    for key in ("packSize", "size", "unitSize", "color", "platform"):
        value = attrs.get(key)
        if value:
            return f"{key}: {truncate(value, 80)}"
    return None


def compact_seller_line(candidate: SourceCandidate, deal: NormalizedDeal) -> str | None:
    seller = candidate.seller_name or deal.seller_name or deal.variant_attributes.get("seller")
    condition = candidate.condition or deal.condition or deal.variant_attributes.get("condition")
    bits = []
    if seller:
        bits.append(f"Seller: {truncate(seller, 60)}")
    if condition:
        bits.append(f"Condition: {truncate(condition, 60)}")
    return " • ".join(bits) if bits else None


def api_detail_lines(candidate: SourceCandidate, deal: NormalizedDeal) -> list[str]:
    attrs = deal.variant_attributes or {}
    lines: list[str] = []

    ids = []
    if deal.sku:
        ids.append(f"SKU `{deal.sku}`")
    if deal.upc:
        ids.append(f"UPC `{deal.upc}`")
    if deal.selected_offer_id:
        ids.append(f"Offer `{deal.selected_offer_id}`")
    if ids:
        lines.append("• " + " • ".join(ids[:3]))

    seller_bits = []
    seller = candidate.seller_name or deal.seller_name or attrs.get("seller")
    if seller:
        seller_bits.append(f"Seller **{seller}**")
    walmart_seller = attrs.get("walmartSeller")
    if walmart_seller:
        seller_bits.append(f"Walmart seller: **{walmart_seller}**")
    fulfillment = candidate.fulfillment_type or deal.fulfillment_type or attrs.get("fulfillment")
    if fulfillment:
        seller_bits.append(f"Fulfillment **{fulfillment}**")
    condition = candidate.condition or deal.condition or attrs.get("condition")
    if condition:
        seller_bits.append(f"Condition **{condition}**")
    if seller_bits:
        lines.append("• " + " • ".join(seller_bits[:4]))

    flags = []
    for key, label in (("rollback", "Rollback"), ("clearance", "Clearance"), ("specialBuy", "Special Buy"), ("marketplace", "Marketplace"), ("bundle", "Bundle"), ("availableOnline", "Online"), ("shipToStore", "Ship-to-store"), ("freeShipToStore", "Free ship-to-store"), ("twoThreeDayShipping", "2-3 day shipping")):
        value = attrs.get(key)
        if value:
            flags.append(f"{label}: **{value}**")
    if flags:
        lines.append("• " + " • ".join(flags[:5]))

    product_bits = []
    for key, label in (("brand", "Brand"), ("modelNumber", "Model"), ("rating", "Rating"), ("reviews", "Reviews"), ("offerType", "Offer type"), ("maxOrderQty", "Max qty"), ("category", "Category")):
        value = attrs.get(key)
        if value:
            product_bits.append(f"{label}: **{truncate(value, 40)}**")
    if product_bits:
        lines.append("• " + " • ".join(product_bits[:4]))

    option_bits = []
    if deal.variant_label:
        option_bits.append(f"Selected option: **{truncate(deal.variant_label, 60)}**")
    for key, label in (("packSize", "Pack"), ("size", "Size"), ("unitSize", "Unit"), ("color", "Color"), ("platform", "Platform")):
        value = attrs.get(key)
        if value:
            option_bits.append(f"{label}: **{truncate(value, 40)}**")
    if option_bits:
        lines.append("• " + " • ".join(option_bits[:4]))

    proof_bits = []
    reference_trusted = attrs.get("referencePriceTrusted")
    if reference_trusted:
        proof_bits.append(f"Reference trusted: **{reference_trusted}**")
    trusted_price = float_or_none(attrs.get("trustedReferencePrice"))
    trusted_source = attrs.get("trustedReferenceSource")
    if trusted_price:
        proof_bits.append(f"Trusted was/typical: **{money(trusted_price)}** `{trusted_source or 'unknown'}`")
    context_price = float_or_none(attrs.get("referenceContextPrice"))
    context_source = attrs.get("referenceContextSource")
    if context_price:
        proof_bits.append(f"Reference shown/not counted: **{money(context_price)}** `{context_source or 'unknown'}`")
    if attrs.get("msrp"):
        proof_bits.append(f"MSRP shown but not counted: **{attrs['msrp']}**")
    if attrs.get("couponSavings"):
        proof_bits.append(f"Coupon API value: **{money(float(attrs['couponSavings']))}**")
    if attrs.get("walmartCashSavings"):
        proof_bits.append(f"Walmart Cash API value: **{money(float(attrs['walmartCashSavings']))}**")
    if proof_bits:
        lines.append("• " + " • ".join(proof_bits[:4]))

    return lines[:6]


def proof_lines_for(candidate: SourceCandidate, decision, proof) -> list[str]:
    lines: list[str] = []
    if proof.price_proof_level == "trusted_reference_price":
        lines.append("• trusted Walmart reference used for % off")
    else:
        lines.append("• no trusted was/strike/reference price; % off suppressed")
    for reason in decision.anomaly.reasons[:1]:
        lines.append(f"• {reason}")
    important_signals = [signal for signal in candidate.signals if signal.startswith("Walmart current price source") or signal.startswith("Walmart reference price source") or signal.startswith("Walmart reference shown") or signal.startswith("ignored low-confidence") or signal.startswith("selected option") or signal.startswith("Walmart coupon") or signal.startswith("Walmart Cash") or signal in {"rollback", "clearance", "special buy", "marketplace seller", "bundle"}]
    lines.extend(f"• {truncate(signal, 120)}" for signal in important_signals[:2])
    return deal_scanner.dedupe_lines(lines) or ["• Product link and current price returned by Walmart API"]


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
