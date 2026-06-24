from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import discord

from sniperplug.models.candidate import SourceCandidate
from sniperplug.models.deal import NormalizedDeal
from sniperplug.services.safe_links import LinkChoice


DEFAULT_CASH_QUERIES = (
    "walmart cash offers",
    "walmart cash eligible",
    "personal care walmart cash",
    "household walmart cash",
    "baby walmart cash",
    "pet walmart cash",
    "beauty walmart cash",
    "detergent walmart cash",
)

# Internal guard terms that must never be treated as Walmart Cash proof.
# Keep lowercase tokens here so static tests can prove we block guesses.
BLOCKED_CASH_GUESS_TERMS = (
    "onepay",
    "one pay",
    "cashback",
    "cash back",
    "cashrewards",
    "cash rewards",
    "generic rewards",
)


@dataclass(frozen=True)
class WalmartCashOffer:
    amount: float | None
    proof_path: str
    proof_label: str
    proof_text: str
    raw_value: str


def walmart_cash_search_terms(search: str | None) -> tuple[str, ...]:
    base = " ".join(str(search or "").split()).strip()
    lowered = base.lower()

    if not base or lowered in {"walmart cash", "walmart cash offers", "cash offers", "cash"}:
        return DEFAULT_CASH_QUERIES

    if "walmart cash" in lowered:
        return (base,) + tuple(q for q in DEFAULT_CASH_QUERIES if q != base)

    return (
        base,
        f"{base} walmart cash",
        f"{base} walmart cash offers",
        f"{base} walmart cash eligible",
    )


def find_walmart_cash_offer(candidate: SourceCandidate, deal: NormalizedDeal) -> WalmartCashOffer | None:
    attrs = dict(deal.variant_attributes or {})

    if str(attrs.get("walmartCashApiProof") or "").lower() != "yes":
        return None
    if str(attrs.get("walmartCashProofMode") or "") != "strict_api_field_amount":
        return None

    proof_path = str(attrs.get("walmartCashProofPath") or "").strip()
    proof_label = str(attrs.get("walmartCashProofLabel") or "Walmart Cash API field").strip()
    proof_text = str(attrs.get("walmartCashProofText") or "").strip()
    raw_value = str(attrs.get("walmartCashRawValue") or "").strip()

    amount = _float_or_none(attrs.get("walmartCashAmount") or attrs.get("walmartCashSavings"))

    if not proof_path and not proof_text and amount is None:
        return None

    return WalmartCashOffer(
        amount=amount,
        proof_path=proof_path or "raw Walmart API payload",
        proof_label=proof_label,
        proof_text=proof_text or "Walmart API returned explicit Walmart Cash proof.",
        raw_value=raw_value or "hidden/structured API value",
    )


def build_walmart_cash_offer_embed(
    candidate: SourceCandidate,
    deal: NormalizedDeal,
    offer: WalmartCashOffer,
    link_choices: tuple[LinkChoice, ...],
) -> discord.Embed:
    title = short(deal.title, 82)
    embed = discord.Embed(
        title=f"✅ API-confirmed Walmart Cash • {title}",
        url=deal.product_url,
        description=(
            "SniperPlug found this because the **Walmart API returned a Walmart Cash field for this exact product result**.\n"
            "This is a **Cash Offer**, not a regular markdown deal unless Walmart also returns trusted was-price proof."
        ),
        color=discord.Color.green(),
    )

    if deal.image_url:
        embed.set_thumbnail(url=deal.image_url)

    cash_line = f"**{money(offer.amount)} Walmart Cash**"
    embed.add_field(
        name="💸 Walmart Cash proof",
        value=(
            f"{cash_line}\n"
            f"API field: `{short(offer.proof_path, 120)}`\n"
            f"Readable proof: **{short(offer.proof_label, 100)}**"
        ),
        inline=False,
    )

    price_lines = [f"Current Walmart API price: **{money(deal.current_price)}**"]
    if offer.amount is not None and deal.current_price:
        after_cash = max(float(deal.current_price) - float(offer.amount), 0)
        price_lines.append(f"After-Cash estimate: **{money(after_cash)}**")
        price_lines.append("This is an estimate only because Walmart Cash is earned/redeemed by Walmart rules.")
    if deal.typical_price:
        price_lines.append(f"Trusted Walmart was/reference price: ~~{money(deal.typical_price)}~~")
    embed.add_field(name="💰 Price summary", value="\n".join(price_lines), inline=False)

    embed.add_field(
        name="🧾 Raw API evidence",
        value=(
            f"Proof text: {short(offer.proof_text, 240)}\n"
            f"Raw value: `{short(offer.raw_value, 180)}`"
        ),
        inline=False,
    )

    stock_lines: list[str] = []
    if candidate.stock_status:
        stock_lines.append(f"Stock: **{short(candidate.stock_status, 80)}**")
    if candidate.can_add_to_cart is True:
        stock_lines.append("Online availability: **yes**")
    elif candidate.can_add_to_cart is False:
        stock_lines.append("Online availability: **not confirmed**")
    if deal.seller_name:
        stock_lines.append(f"Seller: **{short(deal.seller_name, 70)}**")
    if deal.fulfillment_type:
        stock_lines.append(f"Fulfillment: **{short(deal.fulfillment_type, 70)}**")
    if stock_lines:
        embed.add_field(name="📦 Product status", value="\n".join(stock_lines), inline=False)

    links = product_link_block(link_choices, fallback_url=deal.product_url)
    if links:
        embed.add_field(name="🔗 Open product", value=links, inline=False)

    embed.set_footer(text=f"Walmart Cash API proof only • SKU: {deal.sku or 'n/a'} • UPC: {deal.upc or 'n/a'}")
    return embed


def build_walmart_cash_summary_embed(
    search: str,
    queries: tuple[str, ...],
    checked: int,
    found: int,
    warnings: tuple[str, ...],
) -> discord.Embed:
    embed = discord.Embed(
        title="💸 Walmart Cash Finder",
        description=(
            f"Search mode: **Cash Offers only**\n"
            f"Checked: **{checked}** Walmart API product result(s)\n"
            f"API-confirmed Walmart Cash offers found: **{found}**"
        ),
        color=discord.Color.green() if found else discord.Color.orange(),
    )

    embed.add_field(
        name="✅ What counts",
        value=(
            "A product only shows here when the raw Walmart API returns an explicit Walmart Cash proof field/text **and a sane dollar amount** for that exact product. "
            "If the API only shows search words, OnePay, card rewards, or a generic promo, SniperPlug hides it."
        ),
        inline=False,
    )

    embed.add_field(
        name="🚫 What does not count",
        value=(
            "OnePay cashback, card rewards, normal cashback, `Buy more, save up to...`, generic promo text, search words, guesses, and app-only screenshots do not count."
        ),
        inline=False,
    )

    embed.add_field(name="🔎 Search routes used", value=", ".join(f"`{q}`" for q in queries[:8])[:1024], inline=False)

    if warnings:
        embed.add_field(name="Notes", value="\n".join(f"• {w}" for w in warnings[:4])[:1024], inline=False)

    if not found:
        embed.add_field(
            name="No API-confirmed Cash Offers found from returned API results",
            value=(
                "That means the returned API results did not expose Walmart Cash proof fields. If it says Checked: 0, Walmart did not return usable product data before timeout. "
                "Try a narrower search like `/walmart_cash search:personal care`, `/walmart_cash search:detergent`, "
                "or `/walmart_cash search:baby`."
            ),
            inline=False,
        )

    embed.set_footer(text="Private Cash-only search. It does not public-post markdown alerts.")
    return embed


def product_link_block(link_choices: tuple[LinkChoice, ...], *, fallback_url: str) -> str:
    choices = link_choices or (LinkChoice("App/Web", fallback_url),)
    return " • ".join(f"[{choice.label}]({choice.url})" for choice in choices[:3])


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
