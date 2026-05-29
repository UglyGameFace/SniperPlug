from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import discord

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.direct_search_rescue import direct_match_score
from sniperplug.services.price_proof import verified_deal_value
from sniperplug.services.safe_links import product_link_choices


REVIEW_CANDIDATE_LIMIT = 10
REVIEW_MIN_TRUSTED_DISCOUNT = 20.0
REVIEW_MIN_COUPON_OR_CASH = 5.0
MAX_VALUE_RATIO = 0.80

LOW_TRUST_REFERENCE_SOURCES = {"msrp", "listprice", "list_price", "retailprice", "retail_price"}


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

        match_score = direct_match_score(
            query or "",
            deal.title,
            sku=deal.sku,
            upc=deal.upc,
            product_id=candidate.product_id,
        ) if query else 0.0
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
        context_price = trusted_context_price(
            current_price=deal.current_price,
            context_price=raw_context_price,
            context_source=context_source,
            title=deal.title,
        )
        context_discount = percent_off(deal.current_price, context_price)

        if proof.discount_percent is not None and proof.discount_percent < 50:
            under_threshold += 1
        elif raw_context_price is not None and context_price is None:
            weak_reference += 1
        elif raw_context_price is not None:
            weak_reference += 1
        else:
            missing_reference += 1

        has_value_signal = (
            trusted_discount >= REVIEW_MIN_TRUSTED_DISCOUNT
            or coupon >= REVIEW_MIN_COUPON_OR_CASH
            or cash >= REVIEW_MIN_COUPON_OR_CASH
            or safe_markdown_signal(candidate)
            or is_exact_search_match
        )
        if not has_value_signal:
            no_value_signal += 1
            continue

        review_score = trusted_discount + coupon + cash + (5 if safe_markdown_signal(candidate) else 0) + (35 * match_score)
        card = build_review_card(
            candidate,
            deal,
            proof,
            context_price=context_price,
            context_discount=context_discount,
            ignored_context_price=raw_context_price if context_price is None else None,
            coupon=coupon,
            cash=cash,
            direct_match_score=match_score,
        )
        scored.append((review_score, card))

    scored.sort(key=lambda item: item[0], reverse=True)
    return ReviewCandidateResult(
        cards=[card for _, card in scored[:limit]],
        under_threshold_count=under_threshold,
        missing_reference_count=missing_reference,
        weak_reference_count=weak_reference,
        missing_current_count=missing_current,
        no_value_signal_count=no_value_signal,
        rejected_bad_value_count=rejected_bad_value,
        exact_match_count=exact_match_count,
    )


def build_review_card(
    candidate: SourceCandidate,
    deal,
    proof,
    *,
    context_price: float | None,
    context_discount: float | None,
    ignored_context_price: float | None,
    coupon: float,
    cash: float,
    direct_match_score: float = 0.0,
) -> DealCard:
    choices = product_link_choices(
        retailer=deal.retailer,
        product_url=deal.product_url,
        title=deal.title,
        product_id=candidate.product_id,
        sku=deal.sku,
        asin=deal.asin,
    )
    title_prefix = "🔎 Exact product match" if direct_match_score >= 0.45 else "🟨 Review candidate"
    embed = discord.Embed(
        title=f"{title_prefix} • {deal_scanner.trim_title(deal.title, 72)}",
        url=deal.product_url,
        color=discord.Color.gold(),
    )
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
            lines.append(f"Reference context: **{money(context_price)}** `{context_source or 'unknown'}`")
            lines.append(f"Context math: **{context_discount:.0f}%** — not verified / not auto-postable")
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

    proof_lines = api_lines(candidate, deal)
    if proof_lines:
        embed.add_field(name="🧾 API fields", value="\n".join(proof_lines[:8]), inline=False)

    link_block = deal_scanner.product_link_block(choices, fallback_url=deal.product_url)
    if link_block:
        embed.add_field(name="🔗 Links", value=link_block, inline=False)

    embed.set_footer(text="Review-only: API-backed lead, not a verified 50% deal. Exact matches may still need comp/profit checks before public posting.")
    card = DealCard(embed=embed, url=deal.product_url, label=deal_scanner.short_button_label(deal.title), score=0, discount=proof.discount_percent or 0.0, link_choices=choices)
    card.retailer = deal.retailer
    card.should_alert = False
    card.current_price = deal.current_price
    card.selected_offer_id = deal.selected_offer_id
    card.sku = deal.sku
    card.upc = deal.upc
    return card


def api_lines(candidate: SourceCandidate, deal) -> list[str]:
    attrs = deal.variant_attributes or {}
    lines: list[str] = []
    for label, value in (
        ("SKU", deal.sku),
        ("UPC", deal.upc),
        ("Offer ID", deal.selected_offer_id),
        ("Seller", candidate.seller_name or deal.seller_name or attrs.get("seller")),
        ("Condition", candidate.condition or deal.condition or attrs.get("condition")),
        ("Fulfillment", candidate.fulfillment_type or deal.fulfillment_type or attrs.get("fulfillment")),
        ("Stock", candidate.stock_status),
        ("Available online", attrs.get("availableOnline")),
        ("Offer type", attrs.get("offerType")),
        ("Max order qty", attrs.get("maxOrderQty")),
    ):
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
    if ratio >= 4:
        return None
    if is_consumable_or_size_sensitive(title) and ratio >= 2.5:
        return None
    return context_price


def safe_value_amount(value: Any, current_price: float) -> float | None:
    parsed = float_or_none(value)
    if parsed is None or parsed <= 0:
        return None
    if parsed > max(current_price * MAX_VALUE_RATIO, 50):
        return None
    return parsed


def is_consumable_or_size_sensitive(title: str) -> bool:
    text = title.lower()
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
