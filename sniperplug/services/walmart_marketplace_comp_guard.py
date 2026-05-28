from __future__ import annotations

from typing import Any


_PATCHED = False
_ORIGINAL_PROOF_ATTRIBUTES = None
_ORIGINAL_REVIEW_API_LINES = None


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
    comp_price = attrs.get("marketplaceCompPrice")
    if not comp_price:
        return lines

    comp_source = attrs.get("marketplaceCompSource") or "Walmart API marketplace comp"
    seller = attrs.get("marketplaceCompSeller")
    note = attrs.get("marketplaceCompNote") or "flip research only; not Walmart discount proof"
    line = f"• Marketplace comp: **${comp_price}** `{comp_source}`"
    if seller:
        line += f" seller: **{seller}**"
    line += f" — {note}"
    lines.append(line)
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
