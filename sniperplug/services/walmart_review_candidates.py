from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import discord

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.comp_discovery_links import build_free_comp_links, comp_link_block
from sniperplug.services.direct_search_rescue import direct_match_score
from sniperplug.services.price_proof import verified_deal_value
from sniperplug.services.safe_links import product_link_choices


REVIEW_CANDIDATE_LIMIT = 25
REVIEW_MIN_TRUSTED_DISCOUNT = 20.0
REVIEW_MIN_COUPON_OR_CASH = 5.0
REVIEW_MIN_CONTEXT_DISCOUNT = 35.0
REVIEW_MIN_CONTEXT_PROFIT = 15.0
REVIEW_MIN_CONTEXT_MARGIN = 0.25
MAX_VALUE_RATIO = 0.80

LOW_TRUST_REFERENCE_SOURCES = {"msrp", "listprice", "list_price", "retailprice", "retail_price"}
FRAGRANCE_TERMS = ("fragrance", "cologne", "perfume", "parfum", "eau de parfum", "eau de toilette", "edt", "edp")


@dataclass(frozen=True)
class ReviewCandidateResult:
    cards: list[DealCard]
    under_threshold_count: int = 0
    missing_reference_count: int = 0
    weak_reference_count: int = 0
    missing_current_count: int = 0
    no_value_signal_count: int = 0
    rejected_bad_value_count: int = 0
    exact_match_count: int = 0

    def summary_line(self) -> str:
        return (
            f"review candidates: **{len(self.cards)}** • "
            f"under 50% trusted: **{self.under_threshold_count}** • "
            f"weak reference ignored: **{self.weak_reference_count}** • "
            f"bad value rejected: **{self.rejected_bad_value_count}** • "
            f"missing was/reference: **{self.missing_reference_count}** • "
            f"exact matches rescued: **{self.exact_match_count}**"
        )


def build_review_candidate_cards(candidates: list[SourceCandidate], *, limit: int = REVIEW_CANDIDATE_LIMIT, query: str | None = None) -> ReviewCandidateResult:
    under_threshold = 0
    missing_reference = 0
    weak_reference = 0
    missing_current = 0
    no_value_signal = 0
    rejected_bad_value = 0
    exact_match_count = 0

    scored: list[tuple[float, DealCard]] = []
    for candidate in candidates:
        deal = candidate.to_normalized_deal()
        proof = verified_deal_value(deal)
        if deal.current_price is None or deal.current_price <= 0:
            missing_current += 1
            continue
        if proof.discount_percent is not None and proof.discount_percent >= 50:
            continue

        match_score = direct_match_score(query or "", deal.title, sku=deal.sku, upc=deal.upc, product_id=candidate.product_id) if query else 0.0
        is_exact_search_match = match_score >= 0.45
        if is_exact_search_match:
            exact_match_count += 1

        coupon = safe_value_amount(deal.variant_attributes.get("couponSavings"), deal.current_price)
        cash = safe_value_amount(deal.variant_attributes.get("walmartCashSavings"), deal.current_price)
        if (deal.variant_attributes.get("couponSavings") and coupon is None) or (deal.variant_attributes.get("walmartCashSavings") and cash is None):
            rejected_bad_value += 1
        coupon = coupon or 0.0
        cash = cash or 0.0
        trusted_discount = proof.discount_percent or 0.0

        raw_context_price = float_or_none(deal.variant_attributes.get("referenceContextPrice"))
        context_source = str(deal.variant_attributes.get("referenceContextSource") or "")
        context_price = trusted_context_price(current_price=deal.current_price, context_price=raw_context_price, context_source=context_source, title=deal.title)
        context_discount = percent_off(deal.current_price, context_price)
        context_profit = estimated_spread(deal.current_price, context_price)
        context_margin = margin_percent(context_profit, deal.current_price)
        profit_signal = has_profit_context_signal(current_price=deal.current_price, context_price=context_price, context_discount=context_discount, context_profit=context_profit, context_margin=context_margin)

        if proof.discount_percent is not None and proof.discount_percent < 50:
            under_threshold += 1
        elif raw_context_price is not None and context_price is None:
            weak_reference += 1
        elif raw_context_price is not None:
            weak_reference += 1
        else:
            missing_reference += 1

        has_value_signal = trusted_discount >= REVIEW_MIN_TRUSTED_DISCOUNT or coupon >= REVIEW_MIN_COUPON_OR_CASH or cash >= REVIEW_MIN_COUPON_OR_CASH or safe_markdown_signal(candidate) or is_exact_search_match or profit_signal
        if not has_value_signal:
            no_value_signal += 1
            continue

        context_score = 0.0
        if profit_signal:
            context_score = min(65.0, (context_discount or 0.0) * 0.70 + min(context_profit or 0.0, 60.0) * 0.45 + (context_margin or 0.0) * 18.0)
        review_score = trusted_discount + coupon + cash + context_score + (5 if safe_markdown_signal(candidate) else 0) + (35 * match_score)

        card = build_review_card(candidate, deal, proof, context_price=context_price, context_discount=context_discount, ignored_context_price=raw_context_price if context_price is None else None, coupon=coupon, cash=cash, direct_match_score=match_score)
        scored.append((review_score, card))

    scored.sort(key=lambda item: item[0], reverse=True)
    return ReviewCandidateResult(cards=[card for _, card in scored[:limit]], under_threshold_count=under_threshold, missing_reference_count=missing_reference, weak_reference_count=weak_reference, missing_current_count=missing_current, no_value_signal_count=no_value_signal, rejected_bad_value_count=rejected_bad_value, exact_match_count=exact_match_count)


def build_review_card(candidate: SourceCandidate, deal, proof, *, context_price: float | None, context_discount: float | None, ignored_context_price: float | None, coupon: float, cash: float, direct_match_score: float = 0.0, context_profit: float | None = None, context_margin: float | None = None) -> DealCard:
    if context_profit is None:
        context_profit = estimated_spread(deal.current_price, context_price)
    if context_margin is None:
        context_margin = margin_percent(context_profit, deal.current_price)

    category = deal.variant_attributes.get("category") or candidate.category
    choices = product_link_choices(retailer=deal.retailer, product_url=deal.product_url, title=deal.title, product_id=candidate.product_id, sku=deal.sku, asin=deal.asin, upc=deal.upc, brand=deal.brand, model=deal.model, category=category)
    if direct_match_score >= 0.45:
        title_prefix = "🔎 Exact product match"
    elif context_profit is not None and context_margin is not None and context_profit >= REVIEW_MIN_CONTEXT_PROFIT:
        title_prefix = "💸 Flip/value lead"
    else:
        title_prefix = "🟨 Review candidate"
    embed = discord.Embed(title=f"{title_prefix} • {deal_scanner.trim_title(deal.title, 72)}", url=deal.product_url, color=discord.Color.gold())
    if deal.image_url:
        embed.set_thumbnail(url=deal.image_url)

    lines = [f"Current product price: **{money(deal.current_price)}**"]
    if direct_match_score >= 0.45:
        lines.append(f"Direct search match: **{direct_match_score:.0%}** — shown even without Walmart markdown proof")
    if proof.discount_percent is not None and deal.typical_price:
        lines.append(f"Trusted was/typical: **{money(deal.typical_price)}**")
        lines.append(f"Trusted API markdown: **{proof.discount_percent:.0f}%** — below 50% hunt threshold")
    else:
        context_source = deal.variant_attributes.get("referenceContextSource")
        if context_price and context_discount is not None:
            lines.append(f"Reference/comp context: **{money(context_price)}** `{context_source or 'unknown'}`")
            lines.append(f"Context math: **{context_discount:.0f}%** — not verified Walmart markdown proof")
            if context_profit is not None and context_margin is not None:
                lines.append(f"Rough spread before fees/tax/shipping: **{money(context_profit)}** / **{context_margin:.0%} margin**")
        elif ignored_context_price:
            lines.append(f"Ignored reference: **{money(ignored_context_price)}** `{context_source or 'unknown'}`")
            lines.append("Reference math: **blocked as low-trust/suspicious**")
        else:
            lines.append("Was/reference: **not returned or not trusted by Walmart API**")
    if coupon:
        lines.append(f"Coupon from API: **{money(coupon)}**")
    if cash:
        lines.append(f"Walmart Cash from API: **{money(cash)}**")
    embed.add_field(name="💰 API price/value", value="\n".join(lines), inline=False)

    comp_links = build_free_comp_links(title=deal.title, brand=deal.brand, upc=deal.upc, model=deal.model, sku=deal.sku or candidate.product_id, category=category, max_links=7)
    comp_block = comp_link_block(comp_links, max_links=7)
    if comp_block:
        embed.add_field(name="🧭 Free comp research", value=f"{comp_block}\nUse these to verify market price. These links are **not auto-proof** until a retailer/API/parser confirms the price.", inline=False)

    proof_lines = api_lines(candidate, deal)
    if proof_lines:
        embed.add_field(name="🧾 API fields", value="\n".join(proof_lines[:8]), inline=False)

    link_block = deal_scanner.product_link_block(choices, fallback_url=deal.product_url)
    if link_block:
        embed.add_field(name="🔗 Links", value=link_block, inline=False)

    embed.set_footer(text="Review-only: API-backed lead, not a verified 50% deal. Use comps/profit checks before public posting.")
    card = DealCard(embed=embed, url=deal.product_url, label=deal_scanner.short_button_label(deal.title), score=0, discount=proof.discount_percent or 0.0, link_choices=choices)
    card.retailer = deal.retailer
    card.should_alert = False
    card.current_price = deal.current_price
    card.selected_offer_id = deal.selected_offer_id
    card.sku = deal.sku
    card.upc = deal.upc
    card.manual_share_allowed = True
    if context_profit is not None:
        card.estimated_profit = context_profit
    if context_margin is not None:
        card.estimated_margin = context_margin
    return card


def api_lines(candidate: SourceCandidate, deal) -> list[str]:
    attrs = deal.variant_attributes or {}
    lines: list[str] = []
    for label, value in (("SKU", deal.sku), ("UPC", deal.upc), ("Offer ID", deal.selected_offer_id), ("Seller", candidate.seller_name or deal.seller_name or attrs.get("seller")), ("Condition", candidate.condition or deal.condition or attrs.get("condition")), ("Fulfillment", candidate.fulfillment_type or deal.fulfillment_type or attrs.get("fulfillment")), ("Stock", candidate.stock_status), ("Available online", attrs.get("availableOnline")), ("Offer type", attrs.get("offerType")), ("Max order qty", attrs.get("maxOrderQty"))):
        if value:
            lines.append(f"• {label}: **{str(value)[:90]}**")
    return lines


def safe_markdown_signal(candidate: SourceCandidate) -> bool:
    allowed = {"rollback", "clearance", "special buy"}
    return any(str(signal).lower().strip() in allowed for signal in candidate.signals)


def trusted_context_price(*, current_price: float, context_price: float | None, context_source: str, title: str) -> float | None:
    if context_price is None or context_price <= current_price:
        return None
    source_key = context_source.lower().replace("_", "")
    if source_key in LOW_TRUST_REFERENCE_SOURCES:
        return None
    ratio = context_price / current_price
    fragrance = is_fragrance_or_beauty(title)
    if fragrance:
        if ratio >= 6:
            return None
        return context_price
    if ratio >= 4:
        return None
    if is_consumable_or_size_sensitive(title) and ratio >= 2.5:
        return None
    return context_price


def has_profit_context_signal(*, current_price: float, context_price: float | None, context_discount: float | None, context_profit: float | None, context_margin: float | None) -> bool:
    if context_price is None or context_profit is None or context_margin is None or context_discount is None:
        return False
    if current_price <= 0:
        return False
    return context_discount >= REVIEW_MIN_CONTEXT_DISCOUNT and context_profit >= REVIEW_MIN_CONTEXT_PROFIT and context_margin >= REVIEW_MIN_CONTEXT_MARGIN


def estimated_spread(current: float | None, reference: float | None) -> float | None:
    if current is None or reference is None or reference <= current:
        return None
    return round(reference - current, 2)


def margin_percent(spread: float | None, current: float | None) -> float | None:
    if spread is None or current is None or current <= 0:
        return None
    return spread / current


def safe_value_amount(value: Any, current_price: float) -> float | None:
    parsed = float_or_none(value)
    if parsed is None or parsed <= 0:
        return None
    if parsed > max(current_price * MAX_VALUE_RATIO, 50):
        return None
    return parsed


def is_fragrance_or_beauty(title: str) -> bool:
    text = title.lower()
    return any(term in text for term in FRAGRANCE_TERMS)


def is_consumable_or_size_sensitive(title: str) -> bool:
    text = title.lower()
    if is_fragrance_or_beauty(text):
        return False
    keywords = ("peas", "carrots", "vegetable", "stuffing", "food", "beef", "turkey", "chicken", "oz", "lb", "count", "ct", "pack", "can", "detergent", "cleaner", "soap", "paper", "tissue", "diaper", "wipes")
    return any(keyword in text for keyword in keywords)


def percent_off(current: float | None, reference: float | None) -> float | None:
    if current is None or reference is None or reference <= current or reference <= 0:
        return None
    return max(0.0, (reference - current) / reference * 100)


def float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${value:,.2f}"
