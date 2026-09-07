from __future__ import annotations

from typing import Any

from sniperplug.services.walmart_marketplace_comp import (
    clean_string,
    first_bool,
    selected_offer_delivery_attributes,
    selected_offer_node,
    seller_name_is_walmart,
)


_WALMART_SELLER_IDS = {
    "0",
    "F55CDC31AB754BB68FE0B39041159D63",
    "WALMART",
}
_FIRST_PARTY_TRUE_KEYS = (
    "isWalmartSeller",
    "isSoldByWalmart",
    "soldByWalmart",
)
_MARKETPLACE_KEYS = (
    "isMarketPlaceItem",
    "isMarketplaceItem",
    "isMarketplace",
    "marketplace",
)


def augment_candidate_selected_offer_identity(item: dict[str, Any], candidate: Any) -> Any:
    """Fill missing selected-offer identity from explicit Walmart API aliases.

    The normal Walmart selected-offer normalizer remains authoritative. This
    compatibility step runs only after that builder and only fills identity that
    is still missing. It never borrows seller data from sibling offers and never
    infers a seller from title, URL, price, or search position.

    Walmart has used several equivalent seller/merchant field names across its
    official catalog and item-detail payloads. Production must not fall from
    dozens of safe catalog proofs to zero merely because the same selected offer
    moved from ``sellerInfo`` to ``merchantInfo`` or exposed ``seller`` as a
    scalar string. Only explicit fields on the selected offer (then the exact
    item itself) are accepted.
    """

    if not isinstance(item, dict) or candidate is None:
        return candidate

    attrs = dict(getattr(candidate, "variant_attributes", None) or {})
    canonical = selected_offer_delivery_attributes(item)
    for key, value in canonical.items():
        if value not in (None, ""):
            attrs.setdefault(str(key), str(value))

    node, _node_path = selected_offer_node(item)
    containers = (node,) if node is item else (node, item)

    seller_name = clean_string(
        getattr(candidate, "seller_name", None)
        or attrs.get("selectedOfferSeller")
        or attrs.get("seller")
    )
    seller_id = clean_string(
        attrs.get("selectedOfferSellerId")
        or attrs.get("sellerId")
    )

    if not seller_name:
        seller_name = _first_explicit_seller_name(containers)
    if not seller_id:
        seller_id = _first_explicit_seller_id(containers)

    marketplace = first_bool(
        attrs.get("selectedOfferMarketplace"),
        attrs.get("isMarketPlaceItem"),
        attrs.get("isMarketplaceItem"),
        attrs.get("marketplace"),
        *(_marketplace_values(container) for container in containers),
    )
    first_party = any(_explicit_first_party(container) for container in containers)

    normalized_seller_id = str(seller_id or "").strip().upper()
    if normalized_seller_id in _WALMART_SELLER_IDS:
        first_party = True

    if first_party:
        marketplace = False
        seller_name = seller_name or "Walmart"
    elif seller_name:
        if seller_name_is_walmart(seller_name):
            marketplace = False if marketplace is None else marketplace
        elif marketplace is None:
            marketplace = True
    elif marketplace is False:
        # An explicit selected-offer non-marketplace flag is existing SniperPlug
        # proof for Walmart first-party ownership. Preserve that established rule.
        seller_name = "Walmart"

    if seller_name:
        if not clean_string(getattr(candidate, "seller_name", None)):
            candidate.seller_name = seller_name
        attrs.setdefault("selectedOfferSeller", seller_name)
        attrs.setdefault("seller", seller_name)
    if seller_id:
        attrs.setdefault("selectedOfferSellerId", seller_id)
        attrs.setdefault("sellerId", seller_id)
    if marketplace is not None:
        value = "yes" if marketplace else "no"
        attrs.setdefault("selectedOfferMarketplace", value)
        attrs.setdefault("marketplace", value)
    if seller_name and seller_name_is_walmart(seller_name):
        attrs.setdefault("walmartSeller", "yes")

    candidate.variant_attributes = attrs
    return candidate


def _first_explicit_seller_name(containers: tuple[dict[str, Any], ...]) -> str | None:
    for value in containers:
        direct = _first_text(
            value.get("sellerName"),
            value.get("sellerDisplayName"),
            value.get("merchantName"),
            value.get("soldBy"),
            value.get("soldByName"),
            value.get("seller") if not isinstance(value.get("seller"), dict) else None,
            value.get("merchant") if not isinstance(value.get("merchant"), dict) else None,
        )
        if direct:
            return direct
        for key in ("seller", "sellerInfo", "merchant", "merchantInfo"):
            child = value.get(key)
            if not isinstance(child, dict):
                continue
            nested = _first_text(
                child.get("name"),
                child.get("displayName"),
                child.get("sellerName"),
                child.get("sellerDisplayName"),
                child.get("merchantName"),
            )
            if nested:
                return nested
    return None


def _first_explicit_seller_id(containers: tuple[dict[str, Any], ...]) -> str | None:
    for value in containers:
        direct = _first_text(
            value.get("sellerId"),
            value.get("sellerID"),
            value.get("merchantId"),
            value.get("merchantID"),
        )
        if direct:
            return direct
        for key in ("seller", "sellerInfo", "merchant", "merchantInfo"):
            child = value.get(key)
            if not isinstance(child, dict):
                continue
            nested = _first_text(
                child.get("id"),
                child.get("sellerId"),
                child.get("sellerID"),
                child.get("merchantId"),
                child.get("merchantID"),
            )
            if nested:
                return nested
    return None


def _marketplace_values(value: dict[str, Any]) -> Any:
    direct = first_bool(*(value.get(key) for key in _MARKETPLACE_KEYS))
    if direct is not None:
        return direct
    for key in ("seller", "sellerInfo", "merchant", "merchantInfo"):
        child = value.get(key)
        if not isinstance(child, dict):
            continue
        nested = first_bool(*(child.get(name) for name in _MARKETPLACE_KEYS))
        if nested is not None:
            return nested
    return None


def _explicit_first_party(value: dict[str, Any]) -> bool:
    if first_bool(*(value.get(key) for key in _FIRST_PARTY_TRUE_KEYS)) is True:
        return True
    for key in ("seller", "sellerInfo", "merchant", "merchantInfo"):
        child = value.get(key)
        if isinstance(child, dict) and first_bool(
            *(child.get(name) for name in _FIRST_PARTY_TRUE_KEYS)
        ) is True:
            return True
    seller_type = _first_text(
        value.get("sellerType"),
        value.get("merchantType"),
    )
    if seller_type and _normalized(seller_type) in {
        "walmart",
        "firstparty",
        "firstpartyseller",
        "1p",
    }:
        return True
    return False


def _first_text(*values: Any) -> str | None:
    for value in values:
        text = clean_string(value)
        if text:
            return text
    return None


def _normalized(value: Any) -> str:
    return "".join(character for character in str(value or "").lower() if character.isalnum())


__all__ = ["augment_candidate_selected_offer_identity"]
