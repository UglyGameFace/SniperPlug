from __future__ import annotations

from sniperplug.cogs import deal_scanner
from sniperplug.models.candidate import SourceCandidate
from sniperplug.models.deal import NormalizedDeal
from sniperplug.providers.base import ProviderScanResult
from sniperplug.services.candidate_pipeline import evaluate_candidate
from sniperplug.services.price_proof import verified_deal_value
from sniperplug.services.routing import route_label
from sniperplug.services.safe_links import product_link_choices


def install_walmart_accuracy_patches() -> None:
    """Make Walmart deal cards strict about real discount proof.

    This patch keeps the existing command/UI surface, but changes Walmart card
    selection/rendering so low-confidence MSRP/list-price values do not create
    fake percent-off alerts. Coupons and Walmart Cash are displayed as value
    signals, not silently ignored.
    """
    if getattr(deal_scanner, "_sniperplug_walmart_accuracy_installed", False):
        return
    deal_scanner.build_walmart_cards = build_walmart_cards
    deal_scanner.discount_percent = strict_discount_percent
    deal_scanner._sniperplug_walmart_accuracy_installed = True


def build_walmart_cards(result: ProviderScanResult, min_discount: int, alerts_only: bool) -> list[deal_scanner.DealCard]:
    cards: list[deal_scanner.DealCard] = []
    for candidate in result.candidates:
        decision = evaluate_candidate(candidate)
        deal = decision.deal
        proof = verified_deal_value(deal)
        discount = proof.discount_percent
        has_coupon_or_cash = bool(proof.effective_value_notes)

        # True discount cards require a trusted reference price. Coupon/cash-only
        # value can show only on low-threshold/private review scans so it cannot
        # masquerade as a 70%-90% price glitch.
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
        title=f"{deal_scanner.heat_emoji(discount, deal.current_price)} {title_prefix} • {deal_scanner.trim_title(deal.title, 72)}",
        url=deal.product_url,
        color=deal_scanner.embed_color(discount, score),
    )
    if deal.image_url:
        embed.set_thumbnail(url=deal.image_url)
    embed.add_field(name="💰 Price", value=price_block(deal, proof), inline=False)
    link_block = deal_scanner.product_link_block(link_choices, fallback_url=deal.product_url)
    if link_block:
        embed.add_field(name="🔗 Product links", value=link_block, inline=False)
    embed.add_field(
        name="📊 Sniper Read",
        value=(
            f"**{deal_scanner.friendly_score_level(decision.anomaly.level)}** • `{score}/250`\n"
            f"Route: **{route_label(decision.route.route)}**\n"
            f"Would alert: **{'Yes' if decision.should_alert and proof.discount_percent is not None else 'No'}**"
        ),
        inline=True,
    )
    embed.add_field(name="📦 Stock", value=deal_scanner.stock_block(candidate, deal), inline=True)
    option_lines = deal_scanner.selected_option_lines(deal)
    if option_lines:
        embed.add_field(name="🎯 Selected option", value="\n".join(option_lines), inline=False)
    proof_block = deal_scanner.product_proof_block(deal)
    if proof_block:
        embed.add_field(name="🧾 Product Proof", value=proof_block, inline=False)
    fulfillment_block = deal_scanner.fulfillment_proof_block(candidate, deal)
    if fulfillment_block:
        embed.add_field(name="🚚 Fulfillment", value=fulfillment_block, inline=False)
    flag_block = deal_scanner.walmart_flag_block(deal)
    if flag_block:
        embed.add_field(name="🏷️ Deal Flags", value=flag_block, inline=False)
    if proof.effective_value_notes:
        embed.add_field(name="💵 Coupons / Walmart Cash", value="\n".join(f"• {note}" for note in proof.effective_value_notes), inline=False)
    if deal.option_mismatch_warning:
        embed.add_field(name="⚠️ Variant warning", value=deal.option_mismatch_warning, inline=False)
    embed.add_field(name="🟢 Liveness", value=liveness_block(deal, proof), inline=False)
    proof_lines = proof_lines_for(candidate, decision, proof)
    if proof_lines:
        embed.add_field(name="🔎 Why it showed up", value="\n".join(proof_lines[:5]), inline=False)
    footer_bits = [f"SKU: {deal.sku or 'n/a'}", f"UPC: {deal.upc or 'n/a'}"]
    model = deal.model or deal.variant_attributes.get("modelNumber") or deal.variant_attributes.get("model")
    if model:
        footer_bits.append(f"Model: {model[:32]}")
    if deal.variant_label:
        footer_bits.append(f"Option: {deal.variant_label[:40]}")
    footer_bits.append("Trusted price proof required")
    embed.set_footer(text=" • ".join(footer_bits))
    return embed


def price_block(deal: NormalizedDeal, proof) -> str:
    if deal.current_price is None:
        return "Current price unavailable"
    lines = [f"Current: **{money(deal.current_price)}**"]
    if proof.discount_percent is not None and deal.typical_price:
        lines.append(f"Was/typical: ~~{money(deal.typical_price)}~~")
        lines.append(f"Verified save: **{money(proof.savings_amount)} ({proof.discount_percent:.0f}%)**")
        lines.append("Proof: **trusted Walmart reference price**")
    else:
        lines.append("Was/typical: **Not counted**")
        lines.append("Proof: **no trusted Walmart reference price**")
    if deal.coupon_savings:
        lines.append(f"Coupon: **{money(deal.coupon_savings)}**")
    cash = deal.variant_attributes.get("walmartCashSavings")
    if cash:
        lines.append(f"Walmart Cash: **{money(float(cash))} reward/value**")
    return "\n".join(lines)


def liveness_block(deal: NormalizedDeal, proof) -> str:
    if deal.option_mismatch_warning:
        return "🛠️ **Staff review required.** The priced option may not match the parent listing."
    if proof.discount_percent is None:
        return "🟡 **Value watch only.** Coupon/Cash may be useful, but SniperPlug did not prove a true markdown from a trusted Walmart reference price."
    if proof.discount_percent >= 80:
        return "🔥 **High-value candidate.** Re-run scan before posting because price errors can revert fast."
    if proof.discount_percent >= 50:
        return "💎 **Strong verified discount.** Verify checkout price and stock before posting."
    if proof.discount_percent >= 30:
        return "✅ **Verified discount.** Good for watchlist, but not a true glitch yet."
    return "🔎 Recheck before posting."


def proof_lines_for(candidate: SourceCandidate, decision, proof) -> list[str]:
    lines = [f"• {reason}" for reason in decision.anomaly.reasons[:2]]
    if proof.price_proof_level == "trusted_reference_price":
        lines.append("• trusted Walmart reference price used for % off")
    else:
        lines.append("• no trusted Walmart was/strike/reference price; percent-off suppressed")
    important_signals = [
        signal
        for signal in candidate.signals
        if signal.startswith("Walmart current price source")
        or signal.startswith("Walmart reference price source")
        or signal.startswith("ignored low-confidence")
        or signal.startswith("selected option")
        or signal.startswith("condition")
        or signal.startswith("max order quantity")
        or signal.startswith("offer type")
        or signal.startswith("Walmart coupon")
        or signal.startswith("Walmart Cash")
        or signal in {"rollback", "clearance", "special buy", "marketplace seller", "bundle"}
    ]
    lines.extend(f"• {signal}" for signal in important_signals[:4])
    return deal_scanner.dedupe_lines(lines) or ["• Product link and current price returned by Walmart API"]


def strict_discount_percent(current_price: float | None, typical_price: float | None) -> float | None:
    if current_price is None or not typical_price or typical_price <= current_price:
        return None
    return max(0.0, (typical_price - current_price) / typical_price * 100)


def money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"
