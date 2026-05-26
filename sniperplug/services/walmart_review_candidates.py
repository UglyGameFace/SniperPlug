from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import discord

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.price_proof import verified_deal_value
from sniperplug.services.safe_links import product_link_choices


REVIEW_CANDIDATE_LIMIT = 20
REVIEW_MIN_CONTEXT_DISCOUNT = 35.0
REVIEW_MIN_COUPON_OR_CASH = 5.0


@dataclass(frozen=True)
class ReviewCandidateResult:
    cards: list[DealCard]
    under_threshold_count: int = 0
    missing_reference_count: int = 0
    weak_reference_count: int = 0
    missing_current_count: int = 0
    no_value_signal_count: int = 0

    def summary_line(self) -> str:
        return (
            f"review candidates: **{len(self.cards)}** • "
            f"under 50%: **{self.under_threshold_count}** • "
            f"weak reference: **{self.weak_reference_count}** • "
            f"missing was/reference: **{self.missing_reference_count}** • "
            f"missing current: **{self.missing_current_count}**"
        )


def build_review_candidate_cards(candidates: list[SourceCandidate], *, limit: int = REVIEW_CANDIDATE_LIMIT) -> ReviewCandidateResult:
    review_cards: list[DealCard] = []
    under_threshold = 0
    missing_reference = 0
    weak_reference = 0
    missing_current = 0
    no_value_signal = 0

    scored: list[tuple[float, DealCard]] = []
    for candidate in candidates:
        deal = candidate.to_normalized_deal()
        proof = verified_deal_value(deal)
        if deal.current_price is None:
            missing_current += 1
            continue
        if proof.discount_percent is not None and proof.discount_percent >= 50:
            continue

        context_price = float_or_none(deal.variant_attributes.get("referenceContextPrice"))
        context_discount = percent_off(deal.current_price, context_price)
        coupon = float_or_none(deal.variant_attributes.get("couponSavings")) or 0.0
        cash = float_or_none(deal.variant_attributes.get("walmartCashSavings")) or 0.0
        trusted_discount = proof.discount_percent or 0.0

        if proof.discount_percent is not None and proof.discount_percent < 50:
            under_threshold += 1
        elif context_price is not None:
            weak_reference += 1
        else:
            missing_reference += 1

        review_score = max(trusted_discount, context_discount or 0.0) + coupon + cash
        has_value_signal = (
            (proof.discount_percent is not None and proof.discount_percent >= 20)
            or (context_discount is not None and context_discount >= REVIEW_MIN_CONTEXT_DISCOUNT)
            or coupon >= REVIEW_MIN_COUPON_OR_CASH
            or cash >= REVIEW_MIN_COUPON_OR_CASH
            or has_markdown_signal(candidate)
        )
        if not has_value_signal:
            no_value_signal += 1
            continue

        card = build_review_card(candidate, deal, proof, context_discount=context_discount, coupon=coupon, cash=cash)
        scored.append((review_score, card))

    scored.sort(key=lambda item: item[0], reverse=True)
    review_cards = [card for _, card in scored[:limit]]
    return ReviewCandidateResult(
        cards=review_cards,
        under_threshold_count=under_threshold,
        missing_reference_count=missing_reference,
        weak_reference_count=weak_reference,
        missing_current_count=missing_current,
        no_value_signal_count=no_value_signal,
    )


def build_review_card(candidate: SourceCandidate, deal, proof, *, context_discount: float | None, coupon: float, cash: float) -> DealCard:
    choices = product_link_choices(
        retailer=deal.retailer,
        product_url=deal.product_url,
        title=deal.title,
        product_id=candidate.product_id,
        sku=deal.sku,
        asin=deal.asin,
    )
    embed = discord.Embed(
        title=f"🟨 Review candidate • {deal_scanner.trim_title(deal.title, 72)}",
        url=deal.product_url,
        color=discord.Color.gold(),
    )
    if deal.image_url:
        embed.set_thumbnail(url=deal.image_url)

    lines = [f"Current: **{money(deal.current_price)}**"]
    if proof.discount_percent is not None and deal.typical_price:
        lines.append(f"Trusted was/typical: **{money(deal.typical_price)}**")
        lines.append(f"Trusted API markdown: **{proof.discount_percent:.0f}%** — below 50% hunt threshold")
    else:
        context_price = float_or_none(deal.variant_attributes.get("referenceContextPrice"))
        context_source = deal.variant_attributes.get("referenceContextSource")
        if context_price:
            lines.append(f"Reference shown: **{money(context_price)}** `{context_source or 'unknown'}`")
            if context_discount is not None:
                lines.append(f"Reference math: **{context_discount:.0f}%** — **not counted as verified discount**")
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

    embed.set_footer(text="Review-only: shown because the API returned value signals, but 50% trusted markdown was not proven.")
    card = DealCard(embed=embed, url=deal.product_url, label=deal_scanner.short_button_label(deal.title), score=0, discount=proof.discount_percent or context_discount or 0.0, link_choices=choices)
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


def has_markdown_signal(candidate: SourceCandidate) -> bool:
    terms = {"rollback", "clearance", "special buy", "marketplace seller", "bundle"}
    return any(str(signal).lower() in terms or "coupon" in str(signal).lower() or "cash" in str(signal).lower() for signal in candidate.signals)


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
