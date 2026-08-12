from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sniperplug.models.candidate import SourceCandidate
from sniperplug.services.walmart_exact_price_enrichment import (
    _positive_number,
    _trusted_reference,
)


DISCOVERY_PROOF_VERSION = "walmart_affiliate_catalog_selected_offer_v1"
DISCOVERY_PROOF_MAX_AGE_SECONDS = 15 * 60
DISCOVERY_PROOF_REFRESH_INTERVAL_SECONDS = 10 * 60
MAX_DISCOVERY_SNAPSHOT_BYTES = 16 * 1024

# Persist only normalized proof needed to reconstruct one selected Walmart offer.
# Raw API payloads, sibling offer arrays, page HTML, and challenge responses are
# intentionally excluded.
_PROOF_ATTRIBUTE_KEYS = frozenset(
    {
        "selectedOfferNodeSource",
        "selectedOfferSeller",
        "selectedOfferSellerId",
        "selectedOfferId",
        "selectedOfferFulfillment",
        "selectedOfferCondition",
        "selectedOfferMarketplace",
        "selectedOfferReferencePrice",
        "selectedOfferReferenceSource",
        "selectedOfferReferenceTrusted",
        "selectedOfferReferenceStatus",
        "selectedOfferItemPrice",
        "selectedOfferItemPriceSource",
        "selectedOfferShippingCost",
        "selectedOfferShippingSource",
        "selectedOfferShippingStatus",
        "selectedOfferDeliveredPrice",
        "selectedOfferDeliveredPriceSource",
        "selectedOfferPublicPriceStatus",
        "referencePriceTrusted",
        "trustedReferencePrice",
        "trustedReferenceSource",
        "referenceContextPrice",
        "referenceContextSource",
        "crossOfferReferenceBlocked",
        "currentPriceSource",
        "seller",
        "sellerId",
        "fulfillment",
        "condition",
        "walmartSeller",
        "marketplace",
        "isMarketPlaceItem",
        "isMarketplaceItem",
        "color",
        "size",
        "packSize",
        "unitSize",
        "platform",
        "edition",
        "model",
        "modelNumber",
        "clearance",
        "rollback",
        "specialBuy",
        "availableOnline",
        "category",
    }
)


def build_fresh_discovery_offer_snapshot(candidate: SourceCandidate) -> str:
    """Serialize one already-normalized official catalog selected offer.

    This is deliberately stricter than ordinary discovery. A snapshot is stored
    only when the catalog candidate itself has enough atomic selected-offer
    identity to be safely promoted later without another network request.
    """

    if str(getattr(candidate, "retailer", "") or "").strip().lower() != "walmart":
        return ""
    item_id = _candidate_item_id(candidate)
    current = _positive_number(
        getattr(candidate, "api_current_price", None)
        or getattr(candidate, "current_price", None)
    )
    offer_id = _clean_text(getattr(candidate, "selected_offer_id", None))
    attrs = _proof_attrs(candidate)
    marketplace = _explicit_bool(
        attrs.get("selectedOfferMarketplace"),
        attrs.get("isMarketPlaceItem"),
        attrs.get("isMarketplaceItem"),
        attrs.get("marketplace"),
    )
    seller = _clean_text(
        getattr(candidate, "seller_name", None)
        or attrs.get("selectedOfferSeller")
        or attrs.get("seller")
    )
    if not seller and marketplace is False:
        seller = "Walmart"
        attrs.setdefault("selectedOfferSeller", "Walmart")
        attrs.setdefault("seller", "Walmart")
        attrs.setdefault("walmartSeller", "yes")
    if not item_id or current is None or not offer_id or not seller:
        return ""
    if _clean_text(getattr(candidate, "option_mismatch_warning", None)):
        return ""

    third_party = marketplace is True or not _seller_is_walmart(seller)
    shipping_status = _clean_text(
        getattr(candidate, "shipping_status", None)
        or attrs.get("selectedOfferShippingStatus")
    ).lower()
    delivered = _positive_number(
        getattr(candidate, "delivered_price", None)
        or attrs.get("selectedOfferDeliveredPrice")
    )
    if third_party:
        if shipping_status not in {"free", "paid"} or delivered is None:
            return ""
        if _price_to_cents(current) != _price_to_cents(delivered):
            return ""

    reference = _trusted_reference(candidate)
    reference_source = _clean_text(
        getattr(candidate, "api_reference_path", None)
        or attrs.get("trustedReferenceSource")
    )
    if (
        _normalized(attrs.get("referencePriceTrusted")) != "yes"
        or _normalized(attrs.get("crossOfferReferenceBlocked")) == "yes"
        or not reference_source
    ):
        reference = None
        reference_source = ""
        attrs["referencePriceTrusted"] = "no"
        attrs.pop("trustedReferencePrice", None)
        attrs.pop("trustedReferenceSource", None)
    elif reference is not None:
        attrs["trustedReferencePrice"] = f"{reference:.2f}"
        attrs["trustedReferenceSource"] = reference_source

    payload = {
        "version": DISCOVERY_PROOF_VERSION,
        "item_id": item_id,
        "title": _compact(getattr(candidate, "title", None), 300),
        "product_url": _compact(
            getattr(candidate, "direct_product_url", None)
            or getattr(candidate, "product_url", None),
            1000,
        ),
        "image_url": _compact(getattr(candidate, "image_url", None), 1000),
        "current_price": current,
        "reference_price": reference,
        "reference_source": reference_source,
        "api_price_path": _compact(getattr(candidate, "api_price_path", None), 300),
        "selected_offer_id": offer_id,
        "seller_name": seller,
        "fulfillment_type": _compact(getattr(candidate, "fulfillment_type", None), 120),
        "condition": _compact(getattr(candidate, "condition", None), 120),
        "stock_status": _compact(getattr(candidate, "stock_status", None), 120),
        "can_add_to_cart": getattr(candidate, "can_add_to_cart", None),
        "variant_label": _compact(getattr(candidate, "variant_label", None), 240),
        "pack_size": _compact(getattr(candidate, "pack_size", None), 120),
        "color": _compact(getattr(candidate, "color", None), 120),
        "platform": _compact(getattr(candidate, "platform", None), 120),
        "model": _compact(getattr(candidate, "model", None), 160),
        "attrs": attrs,
    }
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    if len(encoded.encode("utf-8")) > MAX_DISCOVERY_SNAPSHOT_BYTES:
        return ""
    return encoded


def promote_fresh_discovery_offer_snapshot(
    snapshot_json: str,
    *,
    snapshot_at: str | None,
    item_id: str,
    apparent_current_cents: int | None,
    now: datetime | None = None,
) -> SourceCandidate | None:
    """Promote a fresh normalized catalog offer into strict exact proof.

    Promotion is fail-closed: freshness, item ID, payable price, selected offer,
    seller, marketplace shipping, and variant identity are revalidated. A stale
    or conflicting snapshot simply falls through to the ordinary exact network
    recovery path.
    """

    observed = _parse_datetime(snapshot_at)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    if observed is None:
        return None
    age = current_time.astimezone(timezone.utc) - observed.astimezone(timezone.utc)
    if age < timedelta(seconds=-60) or age > timedelta(seconds=DISCOVERY_PROOF_MAX_AGE_SECONDS):
        return None

    try:
        payload = json.loads(str(snapshot_json or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != DISCOVERY_PROOF_VERSION:
        return None

    clean_item_id = str(item_id or "").strip()
    if not clean_item_id or str(payload.get("item_id") or "").strip() != clean_item_id:
        return None
    current = _positive_number(payload.get("current_price"))
    if current is None or apparent_current_cents is None:
        return None
    if _price_to_cents(current) != int(apparent_current_cents):
        return None

    attrs = {
        str(key): str(value)
        for key, value in dict(payload.get("attrs") or {}).items()
        if str(key) in _PROOF_ATTRIBUTE_KEYS and value not in (None, "")
    }
    reference = _positive_number(payload.get("reference_price"))
    reference_source = _clean_text(payload.get("reference_source"))
    if (
        reference is None
        or not reference_source
        or _normalized(attrs.get("referencePriceTrusted")) != "yes"
        or _normalized(attrs.get("crossOfferReferenceBlocked")) == "yes"
    ):
        reference = None
        reference_source = ""
        attrs["referencePriceTrusted"] = "no"
        attrs.pop("trustedReferencePrice", None)
        attrs.pop("trustedReferenceSource", None)

    attrs["exactDetailPriceProof"] = "yes"
    attrs["exactDetailItemId"] = clean_item_id
    attrs["exactDetailPriceProofSource"] = "affiliate_catalog_fresh_snapshot"
    attrs["officialExactSource"] = "affiliate_catalog_fresh_snapshot"
    attrs["freshDiscoveryProofObservedAt"] = observed.astimezone(timezone.utc).isoformat()
    attrs["exactDetailOfferIdentityStatus"] = "verified"
    attrs["exactDetailOfferIdentitySource"] = "affiliate catalog selected offer"
    attrs["exactDetailSellerIdentityStatus"] = "verified"
    attrs["exactDetailSellerIdentitySource"] = "affiliate catalog selected offer"
    if reference is not None:
        attrs["exactDetailReferenceStatus"] = "trusted"
        attrs["exactDetailReferenceSource"] = reference_source
        attrs["referencePriceTrusted"] = "yes"
        attrs["trustedReferencePrice"] = f"{reference:.2f}"
        attrs["trustedReferenceSource"] = reference_source
    else:
        attrs["exactDetailReferenceStatus"] = "missing"

    candidate = SourceCandidate(
        source_key="walmart_exact_detail_queue",
        retailer="Walmart",
        title=_clean_text(payload.get("title")) or f"Walmart item {clean_item_id}",
        product_url=_clean_text(payload.get("product_url")) or f"https://www.walmart.com/ip/{clean_item_id}",
        direct_product_url=_clean_text(payload.get("product_url")) or f"https://www.walmart.com/ip/{clean_item_id}",
        image_url=_clean_text(payload.get("image_url")) or None,
        current_price=current,
        typical_price=reference,
        api_current_price=current,
        api_reference_price=reference,
        api_reference_path=reference_source or None,
        api_price_path=_clean_text(payload.get("api_price_path")) or None,
        product_id=clean_item_id,
        product_id_type="sku",
        sku=clean_item_id,
        selected_offer_id=_clean_text(payload.get("selected_offer_id")) or None,
        variant_label=_clean_text(payload.get("variant_label")) or None,
        variant_attributes=attrs,
        pack_size=_clean_text(payload.get("pack_size")) or None,
        color=_clean_text(payload.get("color")) or None,
        platform=_clean_text(payload.get("platform")) or None,
        model=_clean_text(payload.get("model")) or None,
        seller_name=_clean_text(payload.get("seller_name")) or None,
        fulfillment_type=_clean_text(payload.get("fulfillment_type")) or None,
        condition=_clean_text(payload.get("condition")) or None,
        stock_status=_clean_text(payload.get("stock_status")) or None,
        can_add_to_cart=payload.get("can_add_to_cart") if isinstance(payload.get("can_add_to_cart"), bool) else None,
        signals=["fresh Walmart Affiliate catalog selected-offer proof reused without refetch"],
    )
    if _candidate_item_id(candidate) != clean_item_id:
        return None
    if not _promotion_identity_ready(candidate):
        return None
    payable = _positive_number(candidate.api_current_price or candidate.current_price)
    if payable is None or _price_to_cents(payable) != int(apparent_current_cents):
        return None
    return candidate


def _promotion_identity_ready(candidate: SourceCandidate) -> bool:
    attrs = dict(candidate.variant_attributes or {})
    offer_id = _clean_text(candidate.selected_offer_id)
    seller = _clean_text(candidate.seller_name or attrs.get("selectedOfferSeller") or attrs.get("seller"))
    if not offer_id or not seller or _clean_text(candidate.option_mismatch_warning):
        return False
    marketplace = _explicit_bool(
        attrs.get("selectedOfferMarketplace"),
        attrs.get("isMarketPlaceItem"),
        attrs.get("isMarketplaceItem"),
        attrs.get("marketplace"),
    )
    third_party = marketplace is True or not _seller_is_walmart(seller)
    if not third_party:
        return True
    shipping_status = _clean_text(
        candidate.shipping_status or attrs.get("selectedOfferShippingStatus")
    ).lower()
    delivered = _positive_number(
        candidate.delivered_price or attrs.get("selectedOfferDeliveredPrice")
    )
    payable = _positive_number(candidate.api_current_price or candidate.current_price)
    return (
        shipping_status in {"free", "paid"}
        and delivered is not None
        and payable is not None
        and _price_to_cents(delivered) == _price_to_cents(payable)
    )


def _proof_attrs(candidate: SourceCandidate) -> dict[str, str]:
    source = dict(getattr(candidate, "variant_attributes", None) or {})
    return {
        str(key): _compact(value, 400)
        for key, value in source.items()
        if str(key) in _PROOF_ATTRIBUTE_KEYS and _compact(value, 400)
    }


def _candidate_item_id(candidate: SourceCandidate) -> str | None:
    for value in (candidate.product_id, candidate.sku):
        text = str(value or "").strip()
        if text:
            return text
    return None


def _seller_is_walmart(value: Any) -> bool:
    return _normalized(value) in {
        "walmart",
        "walmart.com",
        "walmart stores inc",
        "walmart stores, inc.",
    }


def _explicit_bool(*values: Any) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
        text = _normalized(value)
        if text in {"yes", "true", "1"}:
            return True
        if text in {"no", "false", "0"}:
            return False
    return None


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _price_to_cents(value: Any) -> int | None:
    parsed = _positive_number(value)
    return int(round(parsed * 100)) if parsed is not None else None


def _compact(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[: max(0, int(limit))]


def _clean_text(value: Any) -> str:
    return _compact(value, 1000)


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())
