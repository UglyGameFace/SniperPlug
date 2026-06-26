from __future__ import annotations

from typing import Iterable

import discord

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.safe_links import product_link_choices
from sniperplug.services.walmart_review_candidates import ReviewCandidateResult, build_review_candidate_cards, is_fragrance_or_beauty, money


RAW_PRICE_ROUTE_TERMS = ("fragrance", "cologne", "perfume", "parfum", "beauty", "dolce")


def raw_price_signal(candidate: SourceCandidate, deal) -> bool:
    """Allow private raw-price review leads for exact high-value beauty/fragrance routes.

    This is private review-only. It does not create public markdown proof.
    """

    if deal.current_price is None or deal.current_price <= 0:
        return False
    title = str(deal.title or candidate.title or "")
    attrs = candidate.variant_attributes or {}
    route = str(attrs.get("finderSourceQuery") or attrs.get("finderSourceQueries") or "").lower()
    if not is_fragrance_or_beauty(title):
        return False
    return any(term in route for term in RAW_PRICE_ROUTE_TERMS)


def build_review_candidate_cards_with_raw_leads(candidates: Iterable[SourceCandidate], *, limit: int = 25) -> ReviewCandidateResult:
    candidate_list = list(candidates)
    base = build_review_candidate_cards(candidate_list, limit=limit)
    cards = list(base.cards)
    existing_urls = {getattr(card, "url", "") for card in cards}

    for candidate in candidate_list:
        if len(cards) >= limit:
            break
        deal = candidate.to_normalized_deal()
        if deal.product_url in existing_urls:
            continue
        if not raw_price_signal(candidate, deal):
            continue
        card = _build_raw_price_card(candidate, deal)
        cards.append(card)
        existing_urls.add(deal.product_url)

    return ReviewCandidateResult(
        cards=cards[:limit],
        under_threshold_count=base.under_threshold_count,
        missing_reference_count=base.missing_reference_count,
        weak_reference_count=base.weak_reference_count,
        missing_current_count=base.missing_current_count,
        no_value_signal_count=base.no_value_signal_count,
        rejected_bad_value_count=base.rejected_bad_value_count,
        exact_match_count=base.exact_match_count,
    )


def _build_raw_price_card(candidate: SourceCandidate, deal) -> DealCard:
    embed = discord.Embed(
        title=f"🟨 Raw price lead • {deal_scanner.trim_title(deal.title, 72)}",
        url=deal.product_url,
        description="Manual review needed. This is a raw price lead, not public deal proof.",
        color=discord.Color.gold(),
    )
    if deal.image_url:
        embed.set_thumbnail(url=deal.image_url)
    embed.add_field(
        name="💰 Raw price lead",
        value=(
            f"Current Walmart price: **{money(deal.current_price)}**\n"
            "Was/reference: **not returned or not trusted by Walmart API**\n"
            "Manual review needed before sharing or buying."
        ),
        inline=False,
    )
    route = (candidate.variant_attributes or {}).get("finderSourceQuery")
    if route:
        embed.add_field(name="🔎 Route", value=f"`{route}`", inline=False)
    card = DealCard(
        embed=embed,
        url=deal.product_url,
        label=deal_scanner.short_button_label(deal.title),
        score=0,
        discount=0.0,
        link_choices=product_link_choices(retailer=deal.retailer, product_url=deal.product_url, title=deal.title, product_id=candidate.product_id, sku=deal.sku, upc=deal.upc),
    )
    card.raw_price_lead = True
    card.manual_share_allowed = True
    card.should_alert = False
    card.retailer = deal.retailer
    card.current_price = deal.current_price
    card.sku = deal.sku
    card.upc = deal.upc
    return card
