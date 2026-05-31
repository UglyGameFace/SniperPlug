from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any


DEFAULT_MARKETPLACE_FEE_RATE = 0.13
DEFAULT_TAX_RATE = 0.07
DEFAULT_SHIPPING_ESTIMATE = 8.00
MIN_STRONG_FLIP_ROI = 0.35
MIN_STRONG_FLIP_NET = 15.00
MARKETPLACE_COMP_SOURCES = {"bestmarketplaceprice.price", "bestmarketplaceprice", "best_marketplace_price.price", "best_marketplace_price"}


@dataclass(frozen=True)
class FlipEstimate:
    walmart_price: float
    comp_price: float
    spread: float
    fee_estimate: float
    tax_estimate: float
    shipping_estimate: float
    net_estimate: float
    roi_percent: float
    score: int
    verdict: str


def is_marketplace_comp_source(source: str | None) -> bool:
    normalized = str(source or "").strip().lower().replace("_", "").replace(" ", "")
    return normalized in MARKETPLACE_COMP_SOURCES or "bestmarketplaceprice" in normalized


def marketplace_comp_from_item(item: dict[str, Any]) -> dict[str, str]:
    """Extract Walmart API marketplace comp as flip context only.

    This is deliberately not a Walmart was/regular/reference price. It is a comp
    candidate for manual flip research, so downstream code should never use it to
    prove a markdown percentage.
    """
    best_marketplace = item.get("bestMarketplacePrice") or item.get("best_marketplace_price")
    if not isinstance(best_marketplace, dict):
        return {}

    price = float_or_none(best_marketplace.get("price") or best_marketplace.get("amount") or best_marketplace.get("value"))
    if price is None or price <= 0:
        return {}

    attrs = {
        "marketplaceCompPrice": f"{price:.2f}",
        "marketplaceCompSource": "bestMarketplacePrice.price",
        "marketplaceCompNote": "Walmart API marketplace comp; not was/regular price; use for flip research only",
    }
    seller = clean_string(
        best_marketplace.get("sellerName")
        or best_marketplace.get("seller")
        or best_marketplace.get("sellerDisplayName")
        or best_marketplace.get("sellerId")
    )
    if seller:
        attrs["marketplaceCompSeller"] = seller
    return attrs


def marketplace_comp_from_attrs(attrs: dict[str, Any]) -> tuple[float | None, str | None, str | None, str | None]:
    comp_price = float_or_none(attrs.get("marketplaceCompPrice"))
    comp_source = clean_string(attrs.get("marketplaceCompSource"))
    comp_seller = clean_string(attrs.get("marketplaceCompSeller"))
    comp_note = clean_string(attrs.get("marketplaceCompNote"))
    if comp_price is not None:
        return comp_price, comp_source or "Walmart API marketplace comp", comp_seller, comp_note

    context_source = clean_string(attrs.get("referenceContextSource"))
    if not is_marketplace_comp_source(context_source):
        return None, None, None, None
    context_price = float_or_none(attrs.get("referenceContextPrice"))
    if context_price is None or context_price <= 0:
        return None, None, None, None
    return context_price, context_source or "bestMarketplacePrice.price", None, "flip research only; not Walmart discount proof"


def marketplace_api_lines(*, current_price: float | None, attrs: dict[str, Any]) -> list[str]:
    comp_price, comp_source, seller, note = marketplace_comp_from_attrs(attrs)
    if comp_price is None:
        return []
    source = comp_source or "Walmart API marketplace comp"
    note = note or "flip research only; not Walmart discount proof"
    line = f"• Marketplace comp: **${comp_price:,.2f}** `{source}`"
    if seller:
        line += f" seller: **{seller}**"
    line += f" — {note}"
    lines = [line]

    estimate = flip_estimate(walmart_price=current_price, comp_price=comp_price)
    if estimate:
        lines.append(
            "• Flip estimate: "
            f"score **{estimate.score}/100** • spread **${estimate.spread:,.2f}** • "
            f"est. net **${estimate.net_estimate:,.2f}** • ROI **{estimate.roi_percent:.1f}%** • {estimate.verdict}"
        )
        lines.append(
            "• Estimate assumptions: "
            f"{DEFAULT_MARKETPLACE_FEE_RATE:.0%} selling fee, {DEFAULT_TAX_RATE:.0%} tax, "
            f"${estimate.shipping_estimate:,.2f} shipping placeholder"
        )
    return lines


def flip_estimate(*, walmart_price: float | None, comp_price: float | None) -> FlipEstimate | None:
    if walmart_price is None or walmart_price <= 0 or comp_price is None or comp_price <= 0:
        return None
    spread = comp_price - walmart_price
    fee = comp_price * DEFAULT_MARKETPLACE_FEE_RATE
    tax = walmart_price * DEFAULT_TAX_RATE
    shipping = DEFAULT_SHIPPING_ESTIMATE if comp_price >= 25 else 5.00
    net = comp_price - walmart_price - fee - tax - shipping
    roi = (net / walmart_price) * 100 if walmart_price else 0.0
    score = max(0, min(100, round((roi * 1.2) + (net * 1.1))))
    if net >= MIN_STRONG_FLIP_NET and roi >= MIN_STRONG_FLIP_ROI * 100:
        verdict = "worth deeper comp check"
    elif net > 0:
        verdict = "thin margin / verify sold comps first"
    else:
        verdict = "not enough estimated margin"
    return FlipEstimate(
        walmart_price=round(walmart_price, 2),
        comp_price=round(comp_price, 2),
        spread=round(spread, 2),
        fee_estimate=round(fee, 2),
        tax_estimate=round(tax, 2),
        shipping_estimate=round(shipping, 2),
        net_estimate=round(net, 2),
        roi_percent=round(roi, 1),
        score=score,
        verdict=verdict,
    )


def comp_search_links(*, title: str, sku: str | None = None, upc: str | None = None) -> tuple[str, ...]:
    identity = upc or sku or title
    query = urllib.parse.quote_plus(identity)
    title_query = urllib.parse.quote_plus(title)
    return (
        f"[eBay sold](https://www.ebay.com/sch/i.html?_nkw={query}&LH_Sold=1&LH_Complete=1)",
        f"[eBay active](https://www.ebay.com/sch/i.html?_nkw={query})",
        f"[Google Shopping](https://www.google.com/search?tbm=shop&q={title_query})",
    )


def float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_string(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    return str(value).strip() or None
