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

BLOCKED_CASH_GUESS_TERMS = (
    "onepay",
    "one pay",
    "cashback",
    "cash back",
    "cashrewards",
    "cash rewards",
    "generic rewards",
)

# Static truth copy intentionally kept here so static regressions cannot drift
# into fake-zero wording when Walmart hides or times out promo details.
CASH_FINDER_ZERO_RESULT_TRUTH_COPY = (
    "This is **not** a proven no-offer result",
    "Walmart API timed out before product data returned",
    "No Walmart API product rows returned",
    "No API-confirmed Cash Offers found in checked products",
    "No API-proven Walmart Cash found in checked detail rows",
    "not proof that no Walmart Cash offers exist",
    "Direct product links only show for API-proven Cash candidates",
    "API-proven Cash links",
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
    return (base, f"{base} walmart cash", f"{base} walmart cash offers", f"{base} walmart cash eligible")


def find_walmart_cash_offer(candidate: SourceCandidate, deal: NormalizedDeal) -> WalmartCashOffer | None:
    attrs = dict(deal.variant_attributes or {})
    if str(attrs.get("walmartCashApiProof") or "").lower() != "yes":
        return None
    if str(attrs.get("walmartCashProofMode") or "") != "strict_api_field_amount":
        return None
    amount = _float_or_none(attrs.get("walmartCashAmount") or attrs.get("walmartCashSavings"))
    if amount is None or amount <= 0:
        return None
    proof_path = str(attrs.get("walmartCashProofPath") or "").strip()
    proof_text = str(attrs.get("walmartCashProofText") or "").strip()
    if not proof_path and not proof_text:
        return None
    return WalmartCashOffer(
        amount=amount,
        proof_path=proof_path or "raw Walmart API payload",
        proof_label=str(attrs.get("walmartCashProofLabel") or "Walmart Cash API/PDP field").strip(),
        proof_text=proof_text or "Walmart returned explicit Walmart Cash proof.",
        raw_value=str(attrs.get("walmartCashRawValue") or "hidden/structured API value").strip(),
    )


def build_walmart_cash_offer_embed(
    candidate: SourceCandidate,
    deal: NormalizedDeal,
    offer: WalmartCashOffer,
    link_choices: tuple[LinkChoice, ...],
) -> discord.Embed:
    proof_source = str((deal.variant_attributes or {}).get("cashProofSource") or "affiliate_detail")
    proof_source_label = "Walmart PDP fallback" if proof_source == "walmart_pdp" else "Walmart Affiliate API/detail"
    embed = discord.Embed(
        title=f"✅ API/PDP-proven Walmart Cash • {short(deal.title, 82)}",
        url=deal.product_url,
        description=(
            f"SniperPlug found this because the **{proof_source_label} returned Walmart Cash proof "
            "with a sane dollar amount for this exact product**.\n"
            "This is a **private Cash Offer**, not a public rollback/clearance/open-box alert."
        ),
        color=discord.Color.green(),
    )
    if deal.image_url:
        embed.set_thumbnail(url=deal.image_url)
    embed.add_field(
        name="💸 Walmart Cash proof",
        value=f"**{money(offer.amount)} Walmart Cash**\nProof source: **{proof_source_label}**\nProof path: `{short(offer.proof_path, 120)}`\nReadable proof: **{short(offer.proof_label, 100)}**",
        inline=False,
    )
    price_lines = [f"Current Walmart API price: **{money(deal.current_price)}**"]
    if offer.amount is not None and deal.current_price:
        price_lines.append(f"After-Cash estimate: **{money(max(float(deal.current_price) - float(offer.amount), 0))}**")
        price_lines.append("Estimate only. Walmart Cash is earned/redeemed under Walmart's rules.")
    if deal.typical_price:
        price_lines.append(f"Trusted Walmart was/reference price: ~~{money(deal.typical_price)}~~")
    embed.add_field(name="💰 Price summary", value="\n".join(price_lines), inline=False)
    embed.add_field(name="🧾 Raw proof evidence", value=f"Proof text: {short(offer.proof_text, 240)}\nRaw value: `{short(offer.raw_value, 180)}`", inline=False)
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
        embed.add_field(name="🔗 Open proven Cash product", value=links, inline=False)
    embed.set_footer(text=f"Private Walmart Cash proof only • SKU: {deal.sku or 'n/a'} • UPC: {deal.upc or 'n/a'}")
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
    timed_out = partial or any("timed out" in str(warning).lower() or "timeout" in str(warning).lower() for warning in warnings)
    promo_counts = promo_counts or {}
    badge_seen = int(promo_counts.get("cash_badge_seen", 0) or 0)
    detail_attempted = int(promo_counts.get("detail_rows_attempted", detail_checked) or 0)
    confirmed_rows = int(promo_counts.get("confirmed_walmart_cash_amount_rows", found) or 0)
    badge_no_amount = int(promo_counts.get("badge_rows_without_amount", 0) or 0)
    pdp_attempted = int(promo_counts.get("pdp_fallback_attempted", 0) or 0)
    pdp_checked = int(promo_counts.get("pdp_fallback_checked", 0) or 0)
    pdp_wording = int(promo_counts.get("pdp_cash_wording_seen", 0) or 0)

    embed = discord.Embed(
        title="💸 Walmart Cash Finder",
        description=(
            f"Search mode: **Cash Offers only / API/PDP proof required**\n"
            f"Search rows checked: **{checked}**\n"
            f"Cash badges seen on search rows: **{badge_seen}**\n"
            f"Detail promo rows attempted: **{detail_attempted}**\n"
            f"Detail promo rows checked: **{detail_checked}**\n"
            f"Exact Walmart PDP fallback attempted: **{pdp_attempted}**\n"
            f"Exact Walmart PDP fallback checked: **{pdp_checked}**\n"
            f"PDP Walmart Cash wording seen: **{pdp_wording}**\n"
            f"Badge/wording rows with no amount exposed: **{badge_no_amount}**\n"
            f"Confirmed Walmart Cash amount rows: **{confirmed_rows}**\n"
            f"API/PDP-proven Walmart Cash offers found: **{found}**"
        ),
        color=discord.Color.green() if found else discord.Color.orange(),
    )

    access_lines = [f"Access level: **{capability_label or 'Unknown'}**"]
    if detail_unavailable and not pdp_checked:
        access_lines.append("Promo detail proof: **not exposed by the current API/PDP response**")
    elif detail_checked and pdp_checked:
        access_lines.append(f"Promo detail proof: **API details checked; exact Walmart PDP fallback checked {pdp_checked} product(s)**")
    elif pdp_checked:
        access_lines.append(f"Promo detail proof: **exact Walmart PDP fallback checked {pdp_checked} product(s)**")
    elif detail_checked:
        access_lines.append("Promo detail proof: **checked on returned detail rows**")
    else:
        access_lines.append("Promo detail proof: **not checked**")
    access_lines.extend(f"• {note}" for note in capability_notes[:3])
    embed.add_field(name="🔐 API access truth", value="\n".join(access_lines)[:1024], inline=False)

    embed.add_field(
        name="✅ What counts as Walmart Cash",
        value=(
            "Only explicit Walmart Cash wording **plus a sane dollar amount** for the exact product. "
            "A `Walmart Cash available` badge is only a private candidate until product detail/PDP exposes the amount. "
            "Example: a detail/PDP promo object or text that says `Walmart Cash` and returns `$5`, `$8`, etc."
        ),
        inline=False,
    )
    embed.add_field(
        name="🚫 What does not count",
        value="OnePay cashback, card rewards, normal cashback, `Buy more, save up to...`, generic promo text, search words, guesses, app-only screenshots, product titles, and clearance flags do not count.",
        inline=False,
    )

    if badge_seen or pdp_attempted:
        value_lines: list[str] = []
        if pdp_attempted:
            value_lines.append(f"Exact Walmart PDP fallback checked **{pdp_checked}** product(s).")
        if pdp_wording and not confirmed_rows:
            value_lines.append("Walmart Cash wording found, but no dollar amount was exposed.")
        if badge_seen and not confirmed_rows:
            value_lines.append(
                f"Walmart Cash badge seen on **{badge_seen}** product(s), but the API/detail/PDP response did not expose a dollar amount for those rows. These are not shown as buy-worthy Cash offers yet."
            )
        elif badge_seen:
            value_lines.append(f"Walmart Cash badge seen on **{badge_seen}** product(s). Detail/PDP amount proof confirmed **{confirmed_rows}** row(s).")
        if badge_no_amount:
            value_lines.append(f"Badge/wording rows with no amount exposed: **{badge_no_amount}**.")
        embed.add_field(name="🏷️ Cash badge/PDP detail status", value="\n".join(value_lines)[:1024], inline=False)

    promo_lines = []
    labels = {
        "cart_promo": "Cart Promo / Buy-more-save-more",
        "onepay": "OnePay cashback",
        "markdown": "Rollback/was-price markdown",
        "clearance": "Clearance signal",
        "generic_promo": "Generic promo text",
    }
    for key, label in labels.items():
        value = int(promo_counts.get(key, 0) or 0)
        if value:
            promo_lines.append(f"• {label}: **{value}**")
    if promo_lines:
        promo_lines.append("\nThese are separated diagnostics, not Walmart Cash links or buy recommendations.")
        embed.add_field(name="🧾 Other promo types seen separately", value="\n".join(promo_lines)[:1024], inline=False)

    embed.add_field(name="🔎 Search routes actually checked", value=", ".join(f"`{q}`" for q in queries[:8])[:1024] or "`none`", inline=False)
    if warnings:
        embed.add_field(name="Notes", value="\n".join(f"• {w}" for w in warnings[:5])[:1024], inline=False)

    if not found:
        if detail_unavailable and not pdp_checked:
            embed.add_field(name="Proof unavailable — not a proven no-offer result", value="This is **not** a proven no-offer result. Walmart did not expose full promo detail through the current API access and no PDP proof was checked. No API-proven Walmart Cash found in checked detail rows. SniperPlug will not claim Cash Offers exist, but it also will not pretend the app has none.", inline=False)
        elif timed_out:
            embed.add_field(name="Partial check — not a proven no-offer result", value="This is **not** a proven no-offer result. Walmart API timed out before product data returned or PDP checks timed out/skipped. This is a partial result, not proof that no Walmart Cash offers exist.", inline=False)
        elif checked <= 0:
            embed.add_field(name="No Walmart API product rows returned", value="SniperPlug did not receive usable Walmart product rows for the checked route.", inline=False)
        else:
            embed.add_field(name="No proven Walmart Cash amount found in checked rows", value="No API-proven Walmart Cash found in checked detail rows. No API-confirmed Cash Offers found in checked products. SniperPlug checked returned Walmart API rows, detail rows, and bounded exact PDP fallback when badge candidates existed. No checked row exposed valid Walmart Cash wording plus a sane dollar amount. No product links are shown because nothing was proven buy-worthy by Cash Finder.", inline=False)

    embed.set_footer(text="Private Cash-only search. Direct links only show on API/PDP-proven Cash results. Direct product links only show for API-proven Cash candidates. Cash Finder does not public-post markdown/open-box alerts.")
    return embed


def build_walmart_api_probe_embed(probe: Any) -> discord.Embed:
    counts = getattr(probe, "promo_counts", {}) or {}
    pdp_attempted = counts.get("pdp_fallback_attempted", getattr(probe, "pdp_fallback_attempted", 0))
    pdp_checked = counts.get("pdp_fallback_checked", getattr(probe, "pdp_fallback_checked", 0))
    pdp_wording = counts.get("pdp_cash_wording_seen", getattr(probe, "pdp_cash_wording_seen", 0))
    embed = discord.Embed(title="🧪 Walmart API Probe", description="Owner/admin diagnostic. This is **not a shopping list**. It shows raw promo signals SniperPlug could prove from Walmart API/PDP data.", color=discord.Color.blurple())
    embed.add_field(name="🔐 API capability", value=(f"Mode: **{getattr(probe.capability, 'label', 'Unknown')}**\n" f"Detail access available in code: **{'yes' if getattr(probe.capability, 'detail_access', False) else 'no'}**\n" + "\n".join(f"• {note}" for note in getattr(probe.capability, "notes", ())[:3]))[:1024], inline=False)
    embed.add_field(name="📊 Rows checked", value=(f"Search rows checked: **{getattr(probe, 'search_rows_checked', 0)}**\n" f"Cash badges seen: **{getattr(probe, 'cash_badges_seen', counts.get('cash_badge_seen', 0))}**\n" f"Detail promo rows attempted: **{getattr(probe, 'detail_rows_attempted', counts.get('detail_rows_attempted', 0))}**\n" f"Detail promo rows checked: **{getattr(probe, 'detail_rows_checked', 0)}**\n" f"Exact PDP fallback attempted: **{pdp_attempted}**\n" f"Exact PDP fallback checked: **{pdp_checked}**\n" f"PDP Walmart Cash wording seen: **{pdp_wording}**\n" f"Walmart Cash proof candidates: **{len(getattr(probe, 'cash_candidates', ())) }**"), inline=False)
    embed.add_field(name="🧾 Promo types found separately", value=(f"Walmart Cash: **{counts.get('walmart_cash', 0)}**\n" f"Cash badge only: **{counts.get('cash_badge_seen', 0)}**\n" f"Badge/wording rows without amount: **{counts.get('badge_rows_without_amount', 0)}**\n" f"Cart Promo / Buy-more-save-more: **{counts.get('cart_promo', 0)}**\n" f"OnePay cashback: **{counts.get('onepay', 0)}**\n" f"Rollback/markdown: **{counts.get('markdown', 0)}**\n" f"Clearance signal: **{counts.get('clearance', 0)}**\n" f"Generic promo text: **{counts.get('generic_promo', 0)}**"), inline=False)

    cash_candidates = tuple(getattr(probe, "cash_candidates", ()) or ())
    buy_lines = []
    if cash_candidates:
        buy_lines.append("✅ Direct links are allowed below because these rows have API/PDP-proven Walmart Cash with an amount.")
        buy_lines.append("Still verify seller, shipping, stock, and resale comps before buying.")
    elif int(counts.get("cash_badge_seen", 0) or 0) or int(pdp_wording or 0):
        buy_lines.append("🏷️ Walmart Cash badge/PDP wording rows were seen, but no exact amount was confirmed.")
        buy_lines.append("SniperPlug keeps those private until detail/PDP proof returns the dollar amount.")
    elif any(int(counts.get(key, 0) or 0) for key in ("clearance", "cart_promo", "onepay", "generic_promo")):
        buy_lines.append("⚠️ These are **promo signals only**, not buy-worthy deal alerts.")
        buy_lines.append("A clearance flag by itself does not prove a discount, profit, or good buy.")
        buy_lines.append("SniperPlug hides direct product links here until Walmart Cash or the normal deal scanner proves a real buyable deal.")
    else:
        buy_lines.append("No buy-worthy product was proven from this probe.")
    embed.add_field(name="🛒 Buying meaning", value="\n".join(buy_lines)[:1024], inline=False)

    links = []
    for candidate in cash_candidates[:5]:
        url = str(getattr(candidate, "product_url", "") or "").strip()
        if url:
            links.append(f"• [{short(getattr(candidate, 'title', 'Walmart product'), 70)}]({url})")
    if links:
        embed.add_field(name="🔗 API-proven Cash links", value="\n".join(links)[:1024], inline=False)

    debug_lines = tuple(getattr(probe, "debug_lines", ()) or ())
    embed.add_field(name="🔎 Raw promo proof trail — diagnostic only", value="\n".join(f"• {line}" for line in debug_lines[:6])[:1024] or "No product rows were available to inspect.", inline=False)
    warnings = tuple(getattr(probe, "warnings", ()) or ())
    if warnings:
        embed.add_field(name="Notes", value="\n".join(f"• {w}" for w in warnings[:5])[:1024], inline=False)
    if getattr(probe, "detail_unavailable", False):
        embed.add_field(name="Important", value="Walmart did not expose full promo detail through the current API/PDP access. That means proof is unavailable, not that the Walmart app has no offers.", inline=False)
    embed.set_footer(text="Probe is private. Direct product links only show for API-proven Cash candidates.")
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

# Cash Finder policy: does not public-post markdown alerts; Walmart Cash stays separate from markdown/open-box lanes.
