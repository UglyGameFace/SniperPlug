from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re

import discord

from sniperplug.models.candidate import SourceCandidate
from sniperplug.models.deal import NormalizedDeal
from sniperplug.services.safe_links import LinkChoice


WALMART_CASH_OFFICIAL_CATALOG_URL = (
    "https://www.walmart.com/shop/walmart-member-item-rewards/home"
)
WALMART_CASH_HELP_URL = (
    "https://www.walmart.com/help/article/walmart-cash/"
    "77662758469249c29aed82885d5e554f"
)

# These product departments are retained as future routing hints only. Walmart's
# connected Affiliate Product API does not provide a documented/supported
# Walmart Cash offer feed, so Cash Finder must not search ordinary catalog rows
# and present that sample as offer coverage.
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
    "Walmart Cash feed unavailable",
    "The Affiliate Product API is not a supported Walmart Cash offer feed",
    "No ordinary product searches or detail probes are run",
    "Open Walmart's official Manufacturer Offers catalog",
    "No public PDP scraping or Robot/Human-page probing",
)


@dataclass(frozen=True)
class WalmartCashOffer:
    amount: float | None
    proof_path: str
    proof_label: str
    proof_text: str
    raw_value: str


def walmart_cash_search_terms(search: str | None) -> tuple[str, ...]:
    """Return no routes until a supported Walmart Cash offer feed is connected.

    Searching phrases such as ``walmart cash offers`` or ordinary departments
    through the Affiliate Product API checks product catalog records, not the
    account-linked Ibotta/Manufacturer Offers inventory. Returning an empty route
    set deliberately prevents a false-looking zero and unnecessary API calls.
    """

    _ = search, DEFAULT_CASH_QUERIES
    return ()


def find_walmart_cash_offer(candidate: SourceCandidate, deal: NormalizedDeal) -> WalmartCashOffer | None:
    attrs = dict(deal.variant_attributes or {})
    if str(attrs.get("walmartCashApiProof") or "").lower() != "yes":
        return None
    if str(attrs.get("walmartCashProofMode") or "") != "strict_api_field_amount":
        return None

    amount = _float_or_none(attrs.get("walmartCashAmount") or attrs.get("walmartCashSavings"))
    if amount is None or amount <= 0:
        return None
    if amount < MIN_CONFIRMED_WALMART_CASH_AMOUNT:
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

    # No route list means the safety gate intentionally prevented ordinary
    # Affiliate Product API rows from masquerading as Walmart Cash coverage.
    if not queries and checked == 0 and detail_checked == 0 and found == 0:
        embed = discord.Embed(
            title="💸 Walmart Cash Offers",
            description=(
                "**The connected Walmart Affiliate Product API is not a supported "
                "Walmart Cash offer feed.**\n\n"
                "The previous finder searched ordinary products, checked their normal "
                "item-detail records, and then displayed `0` when those records did not "
                "contain account-linked Manufacturer Offer data. That was not meaningful "
                "Walmart Cash coverage."
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="✅ False scan disabled",
            value=(
                "Product searches made: **0**\n"
                "Item-detail calls made: **0**\n"
                "Fake no-offer conclusion: **blocked**"
            ),
            inline=False,
        )
        embed.add_field(
            name="🛒 Open the real official offer catalog",
            value=(
                f"[Browse Walmart Manufacturer Offers]({WALMART_CASH_OFFICIAL_CATALOG_URL})\n"
                f"[How Walmart Cash works]({WALMART_CASH_HELP_URL})"
            ),
            inline=False,
        )
        embed.add_field(
            name="What SniperPlug still requires",
            value=(
                "A documented or authorized offer feed that returns the exact eligible "
                "item, Cash amount, requirements, and expiration. Until that exists, "
                "SniperPlug will not guess, scrape blocked pages, or call ordinary product "
                "records a Walmart Cash scan."
            ),
            inline=False,
        )
        embed.set_footer(
            text=(
                "Walmart account, location, activation, and offer availability can affect "
                "what the official catalog shows"
            )
        )
        return embed

    badge_seen = int(promo_counts.get("cash_badge_seen", 0) or 0)
    badge_no_amount = int(promo_counts.get("badge_rows_without_amount", 0) or 0)
    detail_attempted = int(promo_counts.get("detail_rows_attempted", detail_checked) or 0)
    timed_out = partial or any(
        "timed out" in str(warning).lower() or "timeout" in str(warning).lower()
        for warning in warnings
    )

    embed = discord.Embed(
        title="💸 Walmart Cash Finder",
        description=(
            "**Supported official Walmart Cash API feed** — no public PDP scraping, HTML parsing, or blocked-page probing.\n"
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
            value="The supported Walmart Cash feed did not return usable products for this search.",
            inline=False,
        )
    else:
        embed.add_field(
            name="No API-proven Walmart Cash in this scan",
            value=(
                "None of the checked exact products returned a valid Walmart Cash dollar amount. "
                "This does **not** prove the Walmart app has no Cash offers; it only means the supported feed did not expose one in this batch."
            ),
            inline=False,
        )

    if badge_seen or badge_no_amount:
        embed.add_field(
            name="🏷️ Unconfirmed Cash hints hidden",
            value=(
                f"Cash badges seen: **{badge_seen}** • badge hints without an amount: **{badge_no_amount}**. "
                "They are not shown as deals until Walmart returns an exact dollar amount."
            ),
            inline=False,
        )

    if detail_unavailable:
        embed.add_field(
            name="⚠️ Proof unavailable",
            value=(
                "Walmart did not expose usable official item-detail promo data for part or all of this batch. "
                "That is missing coverage, not proof that the Walmart app has no Cash offers."
            ),
            inline=False,
        )
    elif timed_out:
        embed.add_field(
            name="⚠️ Partial check",
            value=(
                "One or more official Walmart API checks timed out, so this batch was incomplete. "
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
            "OnePay cashback, generic cashback, markdowns, and promo wording do not count"
        )
    )
    return embed


def build_walmart_api_probe_embed(probe: Any) -> discord.Embed:
    """Compact owner diagnostic; raw proof paths stay out of normal shopping output."""

    counts = getattr(probe, "promo_counts", {}) or {}
    warnings = tuple(getattr(probe, "warnings", ()) or ())
    cash_candidates = tuple(getattr(probe, "cash_candidates", ()) or ())
    no_supported_feed = (
        not tuple(getattr(probe, "used_queries", ()) or ())
        and getattr(probe, "search_rows_checked", 0) == 0
        and getattr(probe, "detail_rows_checked", 0) == 0
    )
    embed = discord.Embed(
        title="🧪 Walmart Cash API Diagnostic",
        description=(
            "No supported Walmart Cash offer feed is configured; ordinary product API "
            "probing is disabled."
            if no_supported_feed
            else "Owner-only official API capability check. This is not a shopping list."
        ),
        color=discord.Color.orange() if no_supported_feed else discord.Color.blurple(),
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
    if no_supported_feed:
        embed.add_field(
            name="Correct next source",
            value=(
                f"[Official Walmart Manufacturer Offers catalog]({WALMART_CASH_OFFICIAL_CATALOG_URL})\n"
                "A supported offer feed must expose item eligibility, exact reward amount, "
                "requirements, and expiration before automation resumes."
            ),
            inline=False,
        )
        embed.set_footer(text="Product-catalog probing disabled • no fake Cash coverage")
        return embed

    embed.add_field(
        name="Separated promo signals only",
        value=(
            f"Cash badge only: **{counts.get('cash_badge_seen', 0)}**\n"
            f"Badge without amount: **{counts.get('badge_rows_without_amount', 0)}**\n"
            f"Cart promo: **{counts.get('cart_promo', 0)}**\n"
            f"OnePay cashback: **{counts.get('onepay', 0)}**\n"
            f"Markdown: **{counts.get('markdown', 0)}**\n"
            f"Clearance: **{counts.get('clearance', 0)}**\n"
            "A clearance flag by itself does not prove a discount, profit, or buy-worthy Cash offer."
        ),
        inline=False,
    )

    links: list[str] = []
    for candidate in cash_candidates[:5]:
        url = str(getattr(candidate, "product_url", "") or "").strip()
        if url.startswith("http"):
            links.append(f"• [{short(getattr(candidate, 'title', 'Walmart product'), 70)}]({url})")
    if links:
        embed.add_field(name="API-proven Cash links", value="\n".join(links)[:1024], inline=False)

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
    embed.set_footer(text="Supported official Walmart API only • public PDP scraping disabled")
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

        http_failure = re.search(r"\bwalmart\s+api\s+http\s+(\d{3})\b", lowered)
        if http_failure:
            status = http_failure.group(1)
            if diagnostic:
                clean = short(re.sub(r"https?://\S+", "[URL omitted]", text), 220)
            else:
                clean = f"Official Walmart API request failed (HTTP {status})."
        elif re.search(r"https?://", lowered):
            continue
        elif "pdp" in lowered or "robot or human" in lowered or "html_" in lowered:
            continue
        elif "timed out" in lowered or "timeout" in lowered:
            clean = "One or more official Walmart API requests timed out."
        elif "item detail unavailable" in lowered or "detail promo proof unavailable" in lowered:
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
# separate from markdown/open-box lanes and requires an explicit supported-feed amount.
