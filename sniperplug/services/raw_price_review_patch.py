from __future__ import annotations

from typing import Any

import discord


_PATCHED = False
_ORIGINAL_BUILD = None

HOT_TERMS = (
    "fragrance", "cologne", "perfume", "parfum", "eau de parfum", "eau de toilette", "edp", "edt",
    "dolce", "gabbana", "gucci", "versace", "armani", "ysl", "prada", "burberry", "calvin klein",
    "lego", "samsung", "galaxy", "iphone", "monitor", "tv", "ssd", "dewalt", "milwaukee",
)


def install_raw_price_review_patch() -> None:
    global _PATCHED, _ORIGINAL_BUILD
    if _PATCHED:
        return
    from sniperplug.services import walmart_review_candidates

    _ORIGINAL_BUILD = walmart_review_candidates.build_review_candidate_cards
    walmart_review_candidates.build_review_candidate_cards = build_review_candidate_cards_with_raw_leads
    _PATCHED = True


def build_review_candidate_cards_with_raw_leads(candidates, *, limit=None):
    from sniperplug.services import walmart_review_candidates

    safe_limit = limit or walmart_review_candidates.REVIEW_CANDIDATE_LIMIT
    base = _ORIGINAL_BUILD(candidates, limit=safe_limit)
    existing = list(base.cards)
    existing_keys = {card_key(card) for card in existing}
    raw_cards = []

    for candidate in candidates:
        deal = candidate.to_normalized_deal()
        if not raw_price_signal(candidate, deal):
            continue
        key = candidate.selected_offer_id or candidate.product_id or deal.sku or deal.upc or deal.product_url or deal.title
        if key in existing_keys:
            continue
        raw_cards.append((raw_price_score(candidate, deal), build_raw_price_card(candidate, deal)))

    raw_cards.sort(key=lambda item: item[0], reverse=True)
    merged = existing + [card for _, card in raw_cards]
    return walmart_review_candidates.ReviewCandidateResult(
        cards=merged[:safe_limit],
        under_threshold_count=base.under_threshold_count,
        missing_reference_count=base.missing_reference_count,
        weak_reference_count=base.weak_reference_count,
        missing_current_count=base.missing_current_count,
        no_value_signal_count=base.no_value_signal_count,
        rejected_bad_value_count=base.rejected_bad_value_count,
    )


def raw_price_signal(candidate, deal) -> bool:
    if deal.current_price is None or deal.current_price <= 0 or deal.current_price > 250:
        return False
    attrs = deal.variant_attributes or {}
    haystack = " ".join(
        str(value).lower()
        for value in (
            deal.title,
            candidate.title,
            attrs.get("finderSourceQuery"),
            attrs.get("finderSourceQueries"),
            " ".join(str(signal) for signal in candidate.signals or ()),
        )
        if value
    )
    return any(term in haystack for term in HOT_TERMS)


def raw_price_score(candidate, deal) -> float:
    title = (deal.title or candidate.title or "").lower()
    score = 8.0
    if any(term in title for term in ("cologne", "perfume", "fragrance", "parfum", "dolce", "gabbana")):
        score += 12
    if deal.current_price is not None:
        score += max(0, min(15, (100 - deal.current_price) / 10))
    return score


def build_raw_price_card(candidate, deal):
    from sniperplug.cogs import deal_scanner
    from sniperplug.services.safe_links import product_link_choices

    choices = product_link_choices(
        retailer=deal.retailer,
        product_url=deal.product_url,
        title=deal.title,
        product_id=candidate.product_id,
        sku=deal.sku,
        asin=deal.asin,
    )
    embed = discord.Embed(
        title=f"🧪 Raw price lead • {deal_scanner.trim_title(deal.title, 72)}",
        url=deal.product_url,
        color=discord.Color.blurple(),
    )
    if deal.image_url:
        embed.set_thumbnail(url=deal.image_url)
    lines = [
        f"Current product price: **{money(deal.current_price)}**",
        "Was/reference: **not returned or not trusted by Walmart API**",
        "Shown because this matched a hot/category route and has a real API price.",
        "Manual review needed: compare comps and verify exact size/variant first.",
    ]
    embed.add_field(name="💰 API price/value", value="\n".join(lines), inline=False)
    api = api_lines(candidate, deal)
    if api:
        embed.add_field(name="🧾 API fields", value="\n".join(api[:8]), inline=False)
    links = deal_scanner.product_link_block(choices, fallback_url=deal.product_url)
    if links:
        embed.add_field(name="🔗 Links", value=links, inline=False)
    embed.set_footer(text="Private review lead. Auto-post stays strict; manually share only after checking it.")
    card = deal_scanner.DealCard(embed=embed, url=deal.product_url, label=deal_scanner.short_button_label(deal.title), score=0, discount=0.0, link_choices=choices)
    card.retailer = deal.retailer
    card.should_alert = False
    card.current_price = deal.current_price
    card.selected_offer_id = deal.selected_offer_id
    card.sku = deal.sku
    card.upc = deal.upc
    card.manual_share_allowed = True
    card.raw_price_lead = True
    return card


def api_lines(candidate, deal) -> list[str]:
    attrs = deal.variant_attributes or {}
    lines = []
    for label, value in (
        ("SKU", deal.sku),
        ("UPC", deal.upc),
        ("Offer ID", deal.selected_offer_id),
        ("Seller", candidate.seller_name or deal.seller_name or attrs.get("seller")),
        ("Stock", candidate.stock_status),
        ("Available online", attrs.get("availableOnline")),
        ("Finder route", attrs.get("finderSourceQuery")),
    ):
        if value:
            lines.append(f"• {label}: **{str(value)[:90]}**")
    return lines


def card_key(card) -> str:
    return str(getattr(card, "selected_offer_id", None) or getattr(card, "sku", None) or getattr(card, "upc", None) or getattr(card, "url", None) or getattr(card, "label", ""))


def money(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "N/A"
