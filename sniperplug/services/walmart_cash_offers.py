from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re

import discord

from sniperplug.models.candidate import SourceCandidate
from sniperplug.models.deal import NormalizedDeal
from sniperplug.services.safe_links import LinkChoice


# Walmart's product search indexes products, not the member/app Cash UI. Broad
# discovery therefore searches likely product departments and inspects the raw
# official API promo fields returned for each exact item.
DEFAULT_CASH_QUERIES = (
    "personal care",
    "beauty",
    "household essentials",
    "baby care",
    "pet supplies",
    "grocery",
    "oral care",
    "laundry detergent",
    "skin care",
    "vitamins",
)

BLOCKED_CASH_GUESS_TERMS = (
    "onepay",
    "one pay",
    "cashback",
    "cash back",
    "cashrewards",
    "cash rewards",
    "generic rewards",
)

# Proof and usefulness are separate decisions. Any positive, sane amount from a
# strict Walmart Cash API field is valid proof; ranking/caps decide what is shown.
MIN_CONFIRMED_WALMART_CASH_AMOUNT = 0.01
MIN_USEFUL_WALMART_CASH_AMOUNT = MIN_CONFIRMED_WALMART_CASH_AMOUNT

CASH_FINDER_ZERO_RESULT_TRUTH_COPY = (
    "No API-proven Walmart Cash in this scan",
    "This does not prove the Walmart app has no Cash offers",
    "Official Walmart API only",
    "No public PDP scraping or Robot/Human-page probing",
    "Unconfirmed badges are hidden until an exact dollar amount is returned",
)


@dataclass(frozen=True)
class WalmartCashOffer:
    amount: float | None
    proof_path: str
    proof_label: str
    proof_text: str
    raw_value: str


def walmart_cash_search_terms(search: str | None) -> tuple[str, ...]:
    """Return useful product queries without poisoning search with promo words."""

    base = " ".join(str(search or "").split()).strip()
    lowered = base.lower()
    if not base or lowered in {
        "walmart cash",
        "walmart cash offers",
        "cash offers",
        "cash offer",
        "cash",
    }:
        return DEFAULT_CASH_QUERIES

    cleaned = re.sub(
        r"\b(?:walmart\s+cash|cash\s+offers?|cash\s+eligible|eligible)\b",
        " ",
        base,
        flags=re.IGNORECASE,
    )
    cleaned = " ".join(cleaned.split()).strip()
    return (cleaned,) if cleaned else DEFAULT_CASH_QUERIES


def find_walmart_cash_offer(candidate: SourceCandidate, deal: NormalizedDeal) -> WalmartCashOffer | None:
    attrs = dict(deal.variant_attributes or {})
    if str(attrs.get("walmartCashApiProof") or "").lower() != "yes":
        return None
    if str(attrs.get("walmartCashProofMode") or "") != "strict_api_field_amount":
        return None

    amount = _float_or_none(attrs.get("walmartCashAmount") or attrs.get("walmartCashSavings"))
    if amount is None or amount < MIN_CONFIRMED_WALMART_CASH_AMOUNT:
        return None

    proof_path = str(attrs.get("walmartCashProofPath") or "").strip()
    proof_text = str(attrs.get("walmartCashProofText") or "").strip()
    if not proof_path and not proof_text:
        return None

    return WalmartCashOffer(
        amount=amount,
        proof_path=proof_path or "official Walmart API payload",
        proof_label=str(attrs.get("walmartCashProofLabel") or "Walmart Cash API field").strip(),
        proof_text=proof_text or "Walmart returned an explicit Walmart Cash amount.",
        raw_value=str(attrs.get("walmartCashRawValue") or "structured API value").strip(),
    )


def build_walmart_cash_offer_embed(
    candidate: SourceCandidate,
    deal: NormalizedDeal,
    offer: WalmartCashOffer,
    link_choices: tuple[LinkChoice, ...],
) -> discord.Embed:
    embed = discord.Embed(
        title=f"💸 {money(offer.amount)} Walmart Cash • {short(deal.title, 72)}",
        url=deal.product_url,
        description=(
            "Exact product with an explicit Walmart Cash dollar amount returned by the "
            "**official Walmart API**. Private result; verify the offer in your Walmart account before buying."
        ),
        color=discord.Color.green(),
    )
    if deal.image_url:
        embed.set_thumbnail(url=deal.image_url)

    price_lines = [f"Current price: **{money(deal.current_price)}**"]
    if offer.amount is not None and deal.current_price is not None:
        net = max(float(deal.current_price) - float(offer.amount), 0)
        price_lines.append(f"After-Cash value: **{money(net)}**")
    if deal.typical_price:
        price_lines.append(f"Trusted was/reference price: ~~{money(deal.typical_price)}~~")
    embed.add_field(name="💰 Value", value="\n".join(price_lines), inline=False)

    status_lines: list[str] = []
    if candidate.stock_status:
        status_lines.append(f"Stock: **{short(candidate.stock_status, 70)}**")
    if candidate.can_add_to_cart is True:
        status_lines.append("Online availability: **confirmed**")
    elif candidate.can_add_to_cart is False:
        status_lines.append("Online availability: **not confirmed**")
    if deal.seller_name:
        status_lines.append(f"Seller: **{short(deal.seller_name, 65)}**")
    if deal.fulfillment_type:
        status_lines.append(f"Fulfillment: **{short(deal.fulfillment_type, 65)}**")
    if status_lines:
        embed.add_field(name="📦 Product status", value="\n".join(status_lines), inline=False)

    links = product_link_block(link_choices, fallback_url=deal.product_url)
    if links:
        embed.add_field(name="🔗 Open exact product", value=links, inline=False)

    source = str((deal.variant_attributes or {}).get("cashProofSource") or "affiliate_detail")
    source_label = "API search row" if source == "affiliate_search" else "API item detail"
    embed.set_footer(
        text=(
            f"Official Walmart {source_label} proof • SKU: {deal.sku or 'n/a'} • "
            "Cash availability can be account/activation dependent"
        )
    )
    return embed


def build_walmart_cash_summary_embed(
    search: str,
    queries: tuple[str, ...],
    checked: int,
    found: int,
    warnings: tuple[str, ...],
    *,
    detail_checked: int = 0,
    detail_unavailable: bool = False,
    partial: bool = False,
    capability_label: str = "",
    capability_notes: tuple[str, ...] = (),
    promo_counts: dict[str, int] | None = None,
) -> discord.Embed:
    promo_counts = promo_counts or {}
    badge_seen = int(promo_counts.get("cash_badge_seen", 0) or 0)
    badge_no_amount = int(promo_counts.get("badge_rows_without_amount", 0) or 0)
    detail_attempted = int(promo_counts.get("detail_rows_attempted", detail_checked) or 0)
    confirmed_rows = int(promo_counts.get("confirmed_walmart_cash_amount_rows", found) or 0)
    timed_out = partial or any(
        "timed out" in str(warning).lower() or "timeout" in str(warning).lower()
        for warning in warnings
    )

    embed = discord.Embed(
        title="💸 Walmart Cash Finder",
        description=(
            "**Official Walmart API only** — no public PDP scraping, HTML parsing, or blocked-page probing.\n"
            f"Products searched: **{checked}**\n"
            f"Exact API detail checks: **{detail_checked}/{detail_attempted}**\n"
            f"Confirmed Cash offers: **{found}**"
        ),
        color=discord.Color.green() if found else discord.Color.orange(),
    )

    if found:
        embed.add_field(
            name="✅ Result",
            value=(
                f"Showing **{found}** exact product(s) where Walmart returned an explicit Cash dollar amount. "
                "The product cards below contain the useful shopping information."
            ),
            inline=False,
        )
    elif checked <= 0:
        embed.add_field(
            name="No products returned",
            value="The official Walmart API did not return usable products for this search. Try a product or category name.",
            inline=False,
        )
    else:
        embed.add_field(
            name="No API-proven Walmart Cash in this scan",
            value=(
                "None of the checked exact products returned a valid Walmart Cash dollar amount. "
                "This does **not** prove the Walmart app has no Cash offers; it only means the official API did not expose one in this batch."
            ),
            inline=False,
        )

    if badge_seen or badge_no_amount:
        embed.add_field(
            name="🏷️ Unconfirmed Cash hints hidden",
            value=(
                f"Walmart Cash-style badge hints: **{badge_seen}** • hints without an amount: **{badge_no_amount}**. "
                "They are not shown as deals until Walmart returns an exact dollar amount."
            ),
            inline=False,
        )

    if detail_unavailable or timed_out:
        embed.add_field(
            name="⚠️ Coverage note",
            value=(
                "Some official API detail checks were unavailable or timed out, so this was a partial scan. "
                "SniperPlug did not turn missing data into a fake zero or a guessed offer."
            ),
            inline=False,
        )

    public_notes = _public_warning_lines(warnings)
    if public_notes:
        embed.add_field(name="Scan note", value="\n".join(f"• {line}" for line in public_notes), inline=False)

    embed.set_footer(
        text=(
            "Private Cash-only search • explicit Walmart Cash amount required • "
            "OnePay, generic cashback, markdowns, and promo wording do not count"
        )
    )
    return embed


def build_walmart_api_probe_embed(probe: Any) -> discord.Embed:
    """Compact owner diagnostic; raw proof paths stay out of normal shopping output."""

    counts = getattr(probe, "promo_counts", {}) or {}
    warnings = tuple(getattr(probe, "warnings", ()) or ())
    cash_candidates = tuple(getattr(probe, "cash_candidates", ()) or ())
    embed = discord.Embed(
        title="🧪 Walmart Cash API Diagnostic",
        description="Owner-only official API capability check. This is not a shopping list.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="API coverage",
        value=(
            f"Mode: **{getattr(probe.capability, 'label', 'Unknown')}**\n"
            f"Search rows: **{getattr(probe, 'search_rows_checked', 0)}**\n"
            f"Detail attempted: **{getattr(probe, 'detail_rows_attempted', 0)}**\n"
            f"Detail checked: **{getattr(probe, 'detail_rows_checked', 0)}**\n"
            f"Strict Cash amount rows: **{getattr(probe, 'confirmed_cash_amount_rows', len(cash_candidates))}**"
        ),
        inline=False,
    )
    embed.add_field(
        name="Separated signals",
        value=(
            f"Cash badge only: **{counts.get('cash_badge_seen', 0)}**\n"
            f"Badge without amount: **{counts.get('badge_rows_without_amount', 0)}**\n"
            f"Cart promo: **{counts.get('cart_promo', 0)}**\n"
            f"OnePay: **{counts.get('onepay', 0)}**\n"
            f"Markdown: **{counts.get('markdown', 0)}**\n"
            f"Clearance: **{counts.get('clearance', 0)}**"
        ),
        inline=False,
    )
    debug_lines = tuple(getattr(probe, "debug_lines", ()) or ())
    if debug_lines:
        embed.add_field(
            name="Proof trail",
            value="\n".join(f"• {short(line, 260)}" for line in debug_lines[:5])[:1024],
            inline=False,
        )
    public_notes = _public_warning_lines(warnings, diagnostic=True)
    if public_notes:
        embed.add_field(name="Notes", value="\n".join(f"• {line}" for line in public_notes), inline=False)
    embed.set_footer(text="Official Walmart API only • public PDP scraping disabled")
    return embed


def product_link_block(link_choices: tuple[LinkChoice, ...], *, fallback_url: str) -> str:
    choices = link_choices or (LinkChoice("App/Web", fallback_url),)
    return " • ".join(f"[{choice.label}]({choice.url})" for choice in choices[:3])


def _public_warning_lines(warnings: tuple[str, ...], *, diagnostic: bool = False) -> list[str]:
    lines: list[str] = []
    for warning in warnings:
        text = " ".join(str(warning or "").split())
        lowered = text.lower()
        if not text:
            continue
        if "publisher_id" in lowered or "direct walmart links" in lowered:
            continue
        if "http" in lowered or "pdp" in lowered or "robot or human" in lowered or "html_" in lowered:
            continue
        if "timed out" in lowered or "timeout" in lowered:
            clean = "One or more official Walmart API requests timed out."
        elif "detail promo proof unavailable" in lowered:
            clean = "One or more official Walmart item-detail checks were unavailable."
        elif diagnostic:
            clean = short(text, 220)
        else:
            continue
        if clean not in lines:
            lines.append(clean)
        if len(lines) >= 3:
            break
    return lines


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
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


# Cash Finder policy: does not public-post markdown alerts; Walmart Cash stays
# separate from markdown/open-box lanes and requires an explicit API amount.
