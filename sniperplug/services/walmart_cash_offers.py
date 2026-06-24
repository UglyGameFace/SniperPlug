from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import discord

from sniperplug.models.candidate import SourceCandidate
from sniperplug.models.deal import NormalizedDeal
from sniperplug.services.safe_links import LinkChoice


@dataclass(frozen=True)
class WalmartCashOffer:
    amount: float | None
    evidence: tuple[str, ...]
    promo_text: str | None = None


def walmart_cash_search_terms(search: str | None) -> tuple[str, ...]:
    base = " ".join(str(search or "").split()).strip()
    if not base:
        return ("walmart cash offers", "walmart cash eligible", "personal care walmart cash", "household walmart cash")
    lowered = base.lower()
    if "walmart cash" in lowered:
        return (base,)
    return (
        f"{base} walmart cash",
        f"{base} walmart cash eligible",
        f"{base} walmart cash offers",
    )


def find_walmart_cash_offer(candidate: SourceCandidate, deal: NormalizedDeal) -> WalmartCashOffer | None:
    attrs = dict(deal.variant_attributes or {})
    text_parts: list[str] = []
    for key, value in attrs.items():
        text_parts.append(str(key))
        text_parts.append(str(value))
    text_parts.extend(str(signal) for signal in candidate.signals or [])
    text_parts.extend(str(term) for term in deal.coupon_terms or [])
    text_parts.extend(str(note) for note in deal.verification_notes or [])
    joined = " ".join(text_parts)
    normalized = joined.lower().replace("_", "").replace("-", "").replace(" ", "")

    amount = _float_or_none(attrs.get("walmartCashSavings"))
    explicit = (
        "walmartcash" in normalized
        or "walmart cash" in joined.lower()
        or "walmartCashSavings" in attrs
    )

    # Do not mistake OnePay/card cashback or generic reward text for Walmart Cash.
    if not explicit:
        return None
    if "onepay" in joined.lower() and "walmartcash" not in normalized:
        return None

    promo_text = str(attrs.get("apiPromotionText") or "").strip() or None
    evidence: list[str] = []

    if amount and amount > 0:
        if not _amount_is_sane(amount, deal.current_price):
            return None
        evidence.append(f"API returned walmartCashSavings: {money(amount)}")
    elif promo_text and "walmart cash" in promo_text.lower():
        evidence.append("API promo text explicitly mentions Walmart Cash")
    else:
        # Explicit eligibility can be useful even if the API does not expose a clean amount.
        eligible_value = first_present(attrs, ("walmartCashEligible", "walmartCashOffer", "walmartCash", "cashOffer"))
        if eligible_value:
            evidence.append(f"API returned Walmart Cash eligibility: {short(eligible_value, 80)}")
        else:
            return None

    for key in ("apiValueKind", "apiPromotionText", "offerType", "sellerName", "availableOnline"):
        value = attrs.get(key)
        if value:
            evidence.append(f"{key}: {short(value, 90)}")

    return WalmartCashOffer(amount=amount if amount and amount > 0 else None, evidence=tuple(dedupe(evidence)[:6]), promo_text=promo_text)


def build_walmart_cash_offer_embed(
    candidate: SourceCandidate,
    deal: NormalizedDeal,
    offer: WalmartCashOffer,
    link_choices: tuple[LinkChoice, ...],
) -> discord.Embed:
    title = short(deal.title, 80)
    embed = discord.Embed(
        title=f"💸 Walmart Cash Offer • {title}",
        url=deal.product_url,
        description=(
            "**Cash-only result.** This product is shown because Walmart API returned explicit Walmart Cash offer evidence.\n"
            "This is **not** treated as a verified markdown deal unless Walmart also returns trusted was/reference price proof."
        ),
        color=discord.Color.green(),
    )
    if deal.image_url:
        embed.set_thumbnail(url=deal.image_url)

    price_lines = [f"Current API price: **{money(deal.current_price)}**"]
    if offer.amount:
        price_lines.append(f"Walmart Cash from API: **{money(offer.amount)}**")
    else:
        price_lines.append("Walmart Cash amount: **not returned clearly by API**")
    if deal.typical_price:
        price_lines.append(f"Walmart reference price: ~~{money(deal.typical_price)}~~")
    embed.add_field(name="💰 API price / cash", value="\n".join(price_lines), inline=False)

    embed.add_field(name="🧾 API Walmart Cash proof", value="\n".join(f"• {line}" for line in offer.evidence)[:1024], inline=False)

    links = product_link_block(link_choices, fallback_url=deal.product_url)
    if links:
        embed.add_field(name="🔗 Links", value=links, inline=False)

    stock_lines: list[str] = []
    if candidate.stock_status:
        stock_lines.append(f"Stock: **{candidate.stock_status[:80]}**")
    if candidate.can_add_to_cart is True:
        stock_lines.append("Add-to-cart: **seen**")
    elif candidate.can_add_to_cart is False:
        stock_lines.append("Add-to-cart: **not confirmed**")
    if deal.seller_name:
        stock_lines.append(f"Seller: **{short(deal.seller_name, 60)}**")
    if deal.fulfillment_type:
        stock_lines.append(f"Fulfillment: **{short(deal.fulfillment_type, 60)}**")
    if stock_lines:
        embed.add_field(name="📦 Availability", value="\n".join(stock_lines), inline=False)

    embed.set_footer(text=f"Cash-only API proof • SKU: {deal.sku or 'n/a'} • UPC: {deal.upc or 'n/a'}")
    return embed


def build_walmart_cash_summary_embed(
    search: str,
    queries: tuple[str, ...],
    checked: int,
    found: int,
    warnings: tuple[str, ...],
) -> discord.Embed:
    embed = discord.Embed(
        title="💸 Walmart Cash Offers",
        description=(
            f"Searching: **{search or 'Walmart Cash Offers'}**\n"
            f"Checked: **{checked}** API product result(s)\n"
            f"Found with explicit Walmart Cash proof: **{found}**"
        ),
        color=discord.Color.green() if found else discord.Color.orange(),
    )
    embed.add_field(
        name="What counts",
        value=(
            "Shown only when returned product data explicitly contains Walmart Cash proof such as "
            "`walmartCashSavings`, Walmart Cash promo text, or Walmart Cash eligibility fields. "
            "OnePay cashback, generic rewards, search words, and guessed promos do **not** count."
        ),
        inline=False,
    )
    embed.add_field(name="Queries used", value=", ".join(f"`{q}`" for q in queries[:4])[:1024], inline=False)
    if warnings:
        embed.add_field(name="Notes", value="\n".join(f"• {w}" for w in warnings[:4])[:1024], inline=False)
    if not found:
        embed.add_field(
            name="No Cash-only matches",
            value="Walmart may show Cash Offers in the app filter, but the API must return explicit proof before SniperPlug shows it here.",
            inline=False,
        )
    embed.set_footer(text="Cash-only search is private and does not public-post markdown alerts.")
    return embed


def product_link_block(link_choices: tuple[LinkChoice, ...], *, fallback_url: str) -> str:
    choices = link_choices or (LinkChoice("App/Web", fallback_url),)
    rows = []
    for choice in choices[:3]:
        rows.append(f"[{choice.label}]({choice.url})")
    return " • ".join(rows)


def first_present(attrs: dict[str, Any], keys: tuple[str, ...]) -> Any | None:
    lowered = {str(k).lower(): v for k, v in attrs.items()}
    for key in keys:
        if key in attrs and attrs[key]:
            return attrs[key]
        if key.lower() in lowered and lowered[key.lower()]:
            return lowered[key.lower()]
    return None


def _amount_is_sane(amount: float, current_price: float | None) -> bool:
    if amount <= 0 or amount >= 10_000:
        return False
    if current_price is None or current_price <= 0:
        return amount <= 200
    return amount <= max(current_price * 1.10, current_price + 5.00)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def money(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"${float(value):,.2f}"


def short(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
