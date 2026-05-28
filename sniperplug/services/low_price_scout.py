from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

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

BAD_TITLE_TERMS = ("sample", "decant", "empty bottle", "case only", "replacement cap", "refill only")
DEAL_ROUTE_TERMS = ("clearance", "rollback", "fragrance", "cologne", "perfume", "designer")
QUERY_STOPWORDS = {"the", "and", "for", "with", "walmart", "deal", "deals", "clearance", "rollback", "sale"}


@dataclass(frozen=True)
class ScoutLead:
    candidate: SourceCandidate
    score: float
    reasons: tuple[str, ...]


def scout_low_price_leads(candidates: Iterable[SourceCandidate], *, limit: int = 8, search_query: str = "") -> list[DealCard]:
    scored: list[ScoutLead] = []
    seen: set[str] = set()
    for candidate in candidates:
        lead = score_candidate(candidate, search_query=search_query)
        if lead is None:
            continue
        key = candidate.selected_offer_id or candidate.product_id or candidate.sku or candidate.upc or candidate.product_url or candidate.title
        if key in seen:
            continue
        seen.add(key)
        scored.append(lead)
    scored.sort(key=lambda item: item.score, reverse=True)
    return [build_scout_card(lead) for lead in scored[:limit]]


def score_candidate(candidate: SourceCandidate, *, search_query: str = "") -> ScoutLead | None:
    if candidate.current_price is None or candidate.current_price <= 0:
        return None
    if candidate.current_price > 250:
        return None

    product_text = product_terms(candidate)
    route_text = route_terms(candidate)
    if any(term in product_text for term in BAD_TITLE_TERMS):
        return None

    reasons: list[str] = []
    score = 0.0

    intent_score, intent_reasons = score_search_intent(product_text, search_query)
    score += intent_score
    reasons.extend(intent_reasons)

    product_brand_hits = [term for term in HOT_BRAND_TERMS if term in product_text]
    route_brand_hits = [term for term in HOT_BRAND_TERMS if term in route_text]
    product_category_hits = [term for term in HOT_CATEGORY_TERMS if term in product_text]
    route_category_hits = [term for term in HOT_CATEGORY_TERMS if term in route_text]

    if route_brand_hits and not product_brand_hits:
        score -= 22
        reasons.append(f"brand route mismatch: route had {', '.join(route_brand_hits[:2])}, title did not")

    if product_brand_hits:
        score += 28
        reasons.append(f"hot product title/brand terms: {', '.join(product_brand_hits[:3])}")
    if product_category_hits:
        score += 20
        reasons.append(f"hot product category terms: {', '.join(product_category_hits[:3])}")
    elif route_category_hits:
        score += 8
        reasons.append(f"hot route category terms: {', '.join(route_category_hits[:3])}")

    if any(term in route_text for term in DEAL_ROUTE_TERMS):
        score += 14
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

    raw_reference = reference_price(candidate)
    if raw_reference and raw_reference > candidate.current_price:
        markdown = percent_off(candidate.current_price, raw_reference)
        score += min(18, max(3, markdown / 3))
        reasons.append(f"Walmart raw price context shows about {markdown:.0f}% off")

    stock = (candidate.stock_status or "").lower()
    attrs = candidate.variant_attributes or {}
    if "available" in stock or str(attrs.get("availableOnline", "")).lower() in {"true", "yes", "1"}:
        score += 6
        reasons.append("appears available online/API")

    seller = (candidate.seller_name or attrs.get("seller") or "").lower()
    if seller == "walmart" or "walmart" in seller:
        score += 8
        reasons.append("seller looks like Walmart")

    if not product_brand_hits and not product_category_hits and not route_category_hits and intent_score <= 0:
        return None
    if score < 35:
        return None
    return ScoutLead(candidate=candidate, score=round(score, 1), reasons=tuple(reasons))


def score_search_intent(product_text: str, search_query: str) -> tuple[float, list[str]]:
    query = normalize_text(search_query)
    if not query:
        return 0.0, []
    tokens = meaningful_query_tokens(query)
    if not tokens:
        return 0.0, []

    score = 0.0
    reasons: list[str] = []
    if query in product_text:
        score += 45
        reasons.append("exact search phrase matched product title")

    matched = [token for token in tokens if token in product_text]
    missing = [token for token in tokens if token not in product_text]
    if matched:
        score += min(32, len(matched) * 8)
        reasons.append(f"matched search terms: {', '.join(matched[:5])}")
    if missing:
        penalty = min(36, len(missing) * 10)
        score -= penalty
        reasons.append(f"missing search terms: {', '.join(missing[:5])}")
    if "one" in tokens and "one" not in product_text:
        score -= 30
        reasons.append("missing distinctive term: one")
    return score, reasons


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def meaningful_query_tokens(query: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", query.lower()) if len(token) >= 3 and token not in QUERY_STOPWORDS]


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

    price_lines = [f"Current Walmart API price: **{money(deal.current_price)}**"]
    raw_reference = reference_price(candidate)
    if raw_reference and deal.current_price and raw_reference > deal.current_price:
        savings = raw_reference - deal.current_price
        markdown = percent_off(deal.current_price, raw_reference)
        source = reference_source(candidate)
        price_lines.extend([
            f"Raw was/typical/reference: **{money(raw_reference)}** `{source}`",
            f"Raw Walmart savings: **{money(savings)}** / **{markdown:.0f}%**",
            "Raw price context is useful for review, but this card is still not auto-post verified.",
        ])
    else:
        price_lines.append("Was/typical/reference: **not returned by API for this scout card**")
    price_lines.extend([
        f"Scout score: **{lead.score:.1f}/100**",
        "This is not auto-verified markdown proof. It is surfaced because it looks unusually worth checking.",
    ])
    embed.add_field(name="💰 Scout price", value="\n".join(price_lines), inline=False)
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


def product_terms(candidate: SourceCandidate) -> str:
    values = [candidate.title, candidate.parent_title, candidate.variant_label, candidate.seller_name, " ".join(str(signal) for signal in candidate.signals or ())]
    return normalize_text(" ".join(str(value) for value in values if value))


def route_terms(candidate: SourceCandidate) -> str:
    attrs = candidate.variant_attributes or {}
    return normalize_text(f"{attrs.get('finderSourceQuery', '')} {attrs.get('finderSourceQueries', '')}")


def reference_price(candidate: SourceCandidate) -> float | None:
    if candidate.typical_price and candidate.current_price and candidate.typical_price > candidate.current_price:
        return float(candidate.typical_price)
    attrs = candidate.variant_attributes or {}
    for key in ("referenceContextPrice", "wasPrice", "listPrice", "msrp"):
        parsed = float_or_none(attrs.get(key))
        if parsed and candidate.current_price and parsed > candidate.current_price:
            return parsed
    return None


def reference_source(candidate: SourceCandidate) -> str:
    attrs = candidate.variant_attributes or {}
    if candidate.typical_price:
        return "typical_price"
    return str(attrs.get("referenceContextSource") or "raw_api_reference")


def percent_off(current: float, reference: float) -> float:
    if reference <= 0 or reference <= current:
        return 0.0
    return max(0.0, (reference - current) / reference * 100)


def api_lines(candidate: SourceCandidate, deal: Any) -> list[str]:
    attrs = deal.variant_attributes or {}
    lines: list[str] = []
    for label, value in (("SKU", deal.sku), ("UPC", deal.upc), ("Offer ID", deal.selected_offer_id), ("Seller", candidate.seller_name or deal.seller_name or attrs.get("seller")), ("Stock", candidate.stock_status), ("Available online", attrs.get("availableOnline")), ("Finder route", attrs.get("finderSourceQuery"))):
        if value:
            lines.append(f"• {label}: **{str(value)[:90]}**")
    return lines


def float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def money(value: Any) -> str:
    if value is None:
        return "N/A"
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "N/A"
