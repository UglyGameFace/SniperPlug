from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Any

import discord

from sniperplug.cogs import deal_scanner
from sniperplug.cogs.deal_scanner import DealCard
from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.safe_links import product_link_choices


HOT_BRAND_TERMS = (
    "dolce", "gabbana", "d&g", "gucci", "versace", "armani", "ysl", "prada", "burberry", "calvin klein",
    "samsung", "galaxy", "iphone", "apple", "sony", "lg", "lenovo", "hp", "asus", "acer",
    "lego", "pokemon", "barbie", "dewalt", "milwaukee", "hart", "hyper tough",
)

HOT_CATEGORY_TERMS = (
    "fragrance", "cologne", "perfume", "parfum", "eau de parfum", "eau de toilette", "edp", "edt",
    "phone", "tablet", "monitor", "tv", "ssd", "laptop", "headphones", "earbuds",
    "toy", "tool", "drill", "vacuum", "air fryer",
)

BAD_TITLE_TERMS = (
    "sample", "decant", "empty bottle", "case only", "replacement cap", "refill only",
)


@dataclass(frozen=True)
class ScoutLead:
    candidate: SourceCandidate
    score: float
    reasons: tuple[str, ...]


def scout_low_price_leads(candidates: Iterable[SourceCandidate], *, limit: int = 8) -> list[DealCard]:
    scored: list[ScoutLead] = []
    seen: set[str] = set()
    for candidate in candidates:
        lead = score_candidate(candidate)
        if lead is None:
            continue
        key = candidate.selected_offer_id or candidate.product_id or candidate.sku or candidate.upc or candidate.product_url or candidate.title
        if key in seen:
            continue
        seen.add(key)
        scored.append(lead)
    scored.sort(key=lambda item: item.score, reverse=True)
    return [build_scout_card(lead) for lead in scored[:limit]]


def score_candidate(candidate: SourceCandidate) -> ScoutLead | None:
    if candidate.current_price is None or candidate.current_price <= 0:
        return None
    if candidate.current_price > 250:
        return None
    text = candidate_text(candidate)
    if any(term in text for term in BAD_TITLE_TERMS):
        return None

    reasons: list[str] = []
    score = 0.0

    brand_hits = [term for term in HOT_BRAND_TERMS if term in text]
    category_hits = [term for term in HOT_CATEGORY_TERMS if term in text]
    if brand_hits:
        score += 25
        reasons.append(f"hot brand/title terms: {', '.join(brand_hits[:3])}")
    if category_hits:
        score += 20
        reasons.append(f"hot category terms: {', '.join(category_hits[:3])}")

    route_text = route_terms(candidate)
    if any(term in route_text for term in ("clearance", "rollback", "fragrance", "cologne", "perfume", "designer")):
        score += 18
        reasons.append("found through deal-focused route")

    if candidate.current_price <= 25:
        score += 18
        reasons.append("very low entry price")
    elif candidate.current_price <= 75:
        score += 14
        reasons.append("low entry price")
    elif candidate.current_price <= 125:
        score += 8
        reasons.append("moderate scout price")

    stock = (candidate.stock_status or "").lower()
    attrs = candidate.variant_attributes or {}
    if "available" in stock or str(attrs.get("availableOnline", "")).lower() in {"true", "yes", "1"}:
        score += 6
        reasons.append("appears available online/API")

    seller = (candidate.seller_name or attrs.get("seller") or "").lower()
    if seller == "walmart" or "walmart" in seller:
        score += 8
        reasons.append("seller looks like Walmart")

    if not brand_hits and not category_hits:
        return None
    if score < 35:
        return None
    return ScoutLead(candidate=candidate, score=round(score, 1), reasons=tuple(reasons))


def build_scout_card(lead: ScoutLead) -> DealCard:
    candidate = lead.candidate
    deal = candidate.to_normalized_deal()
    choices = product_link_choices(
        retailer=deal.retailer,
        product_url=deal.product_url,
        title=deal.title,
        product_id=candidate.product_id,
        sku=deal.sku,
        asin=deal.asin,
    )
    embed = discord.Embed(
        title=f"🔎 Low-price scout • {deal_scanner.trim_title(deal.title, 72)}",
        url=deal.product_url,
        color=discord.Color.teal(),
    )
    if deal.image_url:
        embed.set_thumbnail(url=deal.image_url)
    embed.add_field(
        name="💰 Scout price",
        value=(
            f"Current Walmart API price: **{money(deal.current_price)}**\n"
            f"Scout score: **{lead.score:.1f}/100**\n"
            "This is not auto-verified markdown proof. It is surfaced because it looks unusually worth checking."
        ),
        inline=False,
    )
    embed.add_field(name="Why shown", value="\n".join(f"• {reason}" for reason in lead.reasons[:6]), inline=False)
    api = api_lines(candidate, deal)
    if api:
        embed.add_field(name="🧾 API fields", value="\n".join(api[:8]), inline=False)
    links = deal_scanner.product_link_block(choices, fallback_url=deal.product_url)
    if links:
        embed.add_field(name="🔗 Links", value=links, inline=False)
    embed.set_footer(text="Private scout lead. Manually publish only after checking price, seller, exact variant, and comps.")
    card = DealCard(embed=embed, url=deal.product_url, label=deal_scanner.short_button_label(deal.title), score=int(lead.score), discount=0.0, link_choices=choices)
    card.retailer = deal.retailer
    card.should_alert = False
    card.current_price = deal.current_price
    card.selected_offer_id = deal.selected_offer_id
    card.sku = deal.sku
    card.upc = deal.upc
    card.manual_share_allowed = True
    card.low_price_scout = True
    return card


def candidate_text(candidate: SourceCandidate) -> str:
    attrs = candidate.variant_attributes or {}
    values = [
        candidate.title,
        candidate.seller_name,
        candidate.stock_status,
        " ".join(str(signal) for signal in candidate.signals or ()),
        str(attrs.get("finderSourceQuery") or ""),
        str(attrs.get("finderSourceQueries") or ""),
    ]
    return " ".join(str(value).lower() for value in values if value)


def route_terms(candidate: SourceCandidate) -> str:
    attrs = candidate.variant_attributes or {}
    return f"{attrs.get('finderSourceQuery', '')} {attrs.get('finderSourceQueries', '')}".lower()


def api_lines(candidate: SourceCandidate, deal: Any) -> list[str]:
    attrs = deal.variant_attributes or {}
    lines: list[str] = []
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


def money(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "N/A"
