from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Any


DEFAULT_MARKETPLACE_FEE_RATE = 0.13
DEFAULT_TAX_RATE = 0.07
DEFAULT_SHIPPING_ESTIMATE = 8.00
MIN_STRONG_FLIP_ROI = 0.35
MIN_STRONG_FLIP_NET = 15.00

_PATCHED = False
_ORIGINAL_PROOF_ATTRIBUTES = None
_ORIGINAL_REVIEW_API_LINES = None


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


def install_walmart_marketplace_comp_guard() -> None:
    """Keep Walmart marketplace comp prices out of discount math.

    `bestMarketplacePrice.price` can be useful flip context, but it is not a
    Walmart was/regular price for the selected offer. It must never create
    reference/context markdown math.
    """
    global _PATCHED, _ORIGINAL_PROOF_ATTRIBUTES, _ORIGINAL_REVIEW_API_LINES
    if _PATCHED:
        return

    from sniperplug.providers import walmart as walmart_provider
    from sniperplug.services import walmart_review_candidates

    walmart_provider._best_marketplace_reference_prices = _no_marketplace_reference_prices

    _ORIGINAL_PROOF_ATTRIBUTES = walmart_provider._walmart_proof_attributes
    walmart_provider._walmart_proof_attributes = _proof_attributes_with_marketplace_comp

    _ORIGINAL_REVIEW_API_LINES = walmart_review_candidates.api_lines
    walmart_review_candidates.api_lines = _review_api_lines_with_marketplace_comp

    _PATCHED = True


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


def _no_marketplace_reference_prices(item: dict[str, Any]) -> list[tuple[str, float | None]]:
    return []


def _proof_attributes_with_marketplace_comp(item: dict[str, Any], variant_attrs: dict[str, str], selected_offer=None, promotions=None) -> dict[str, str]:
    attrs = _ORIGINAL_PROOF_ATTRIBUTES(item, variant_attrs, selected_offer, promotions)
    comp = _marketplace_comp_from_item(item)
    if comp:
        attrs.update(comp)
    return attrs


def _review_api_lines_with_marketplace_comp(candidate, deal) -> list[str]:
    lines = list(_ORIGINAL_REVIEW_API_LINES(candidate, deal))
    attrs = deal.variant_attributes or {}
    comp_price = _float_or_none(attrs.get("marketplaceCompPrice"))
    if comp_price is None:
        return lines

    comp_source = attrs.get("marketplaceCompSource") or "Walmart API marketplace comp"
    seller = attrs.get("marketplaceCompSeller")
    note = attrs.get("marketplaceCompNote") or "flip research only; not Walmart discount proof"
    line = f"• Marketplace comp: **${comp_price:,.2f}** `{comp_source}`"
    if seller:
        line += f" seller: **{seller}**"
    line += f" — {note}"
    lines.append(line)

    estimate = flip_estimate(walmart_price=deal.current_price, comp_price=comp_price)
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


def _marketplace_comp_from_item(item: dict[str, Any]) -> dict[str, str]:
    best_marketplace = item.get("bestMarketplacePrice") or item.get("best_marketplace_price")
    if not isinstance(best_marketplace, dict):
        return {}

    price = _float_or_none(best_marketplace.get("price") or best_marketplace.get("amount") or best_marketplace.get("value"))
    if price is None or price <= 0:
        return {}

    attrs = {
        "marketplaceCompPrice": f"{price:.2f}",
        "marketplaceCompSource": "bestMarketplacePrice.price",
        "marketplaceCompNote": "Walmart API marketplace comp; not was/regular price; use for flip research only",
    }

    seller = _clean_string(
        best_marketplace.get("sellerName")
        or best_marketplace.get("seller")
        or best_marketplace.get("sellerDisplayName")
        or best_marketplace.get("sellerId")
    )
    if seller:
        attrs["marketplaceCompSeller"] = seller
    return attrs


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip()
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clean_string(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    return str(value).strip() or None
