from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from statistics import median
import re
from typing import Any, Iterable, Mapping

from sniperplug.ebay_watcher.models import (
    ComparableReference,
    EbayListing,
)


IDENTITY_ASPECTS = (
    "brand",
    "model",
    "mpn",
    "storage capacity",
    "color",
    "size",
    "platform",
    "edition",
    "number in pack",
    "unit quantity",
    "quantity",
)

SUSPICIOUS_PHRASES = (
    "box only",
    "empty box",
    "case only",
    "manual only",
    "photo only",
    "picture only",
    "account only",
    "digital code only",
    "replacement shell",
    "replacement part",
    "for parts",
    "not working",
    "broken",
    "untested",
    "as is",
    "read description",
    "replica",
    "reproduction",
    "inspired by",
)


def parse_ebay_item(payload: Mapping[str, Any]) -> EbayListing:
    item_id = _text(payload.get("itemId"))
    legacy_item_id = _text(payload.get("legacyItemId"))
    title = _compact(payload.get("title"), 300)
    product_url = _text(
        payload.get("itemAffiliateWebUrl")
        or payload.get("itemWebUrl")
        or payload.get("itemHref")
    )
    image_url = _nested_text(payload, "image", "imageUrl")

    item_price, currency = _amount(payload.get("price"))
    if item_price is None:
        item_price, currency = _amount(payload.get("currentBidPrice"))
    item_price = float(item_price or 0.0)
    shipping_price, shipping_known = _shipping_price(payload)
    delivered_price = (
        round(item_price + shipping_price, 2)
        if item_price > 0 and shipping_price is not None
        else None
    )

    condition_id = _text(payload.get("conditionId"))
    condition_name = _text(payload.get("condition"))
    condition_bucket = normalize_condition(condition_id, condition_name)

    seller = payload.get("seller") if isinstance(payload.get("seller"), Mapping) else {}
    seller_id = _text(
        seller.get("username")
        or seller.get("userId")
        or seller.get("userID")
        or seller.get("sellerId")
    )
    seller_feedback_percentage = _float(seller.get("feedbackPercentage"))
    seller_feedback_score = _int(seller.get("feedbackScore"))

    buying_options = tuple(
        dict.fromkeys(
            _text(value).upper()
            for value in _iterable(payload.get("buyingOptions"))
            if _text(value)
        )
    )
    item_creation_date = _text(
        payload.get("itemOriginDate") or payload.get("itemCreationDate")
    )
    item_end_date = _text(payload.get("itemEndDate"))
    availability = _availability_status(payload)

    aspects = _localized_aspects(payload)
    product = payload.get("product") if isinstance(payload.get("product"), Mapping) else {}
    gtin = _first_text(
        payload.get("gtin"),
        payload.get("gtins"),
        product.get("gtin"),
        product.get("gtins"),
        _aspect(aspects, "upc"),
        _aspect(aspects, "ean"),
        _aspect(aspects, "isbn"),
    )
    epid = _first_text(
        payload.get("epid"),
        product.get("epid"),
    )
    brand = _first_text(
        payload.get("brand"),
        product.get("brand"),
        _aspect(aspects, "brand"),
    )
    model = _first_text(
        payload.get("model"),
        product.get("model"),
        _aspect(aspects, "model"),
    )
    mpn = _first_text(
        payload.get("mpn"),
        product.get("mpn"),
        _aspect(aspects, "mpn"),
    )
    fingerprint, exact_identity = build_listing_fingerprint(
        item_id=item_id,
        gtin=gtin,
        epid=epid,
        brand=brand,
        model=model,
        mpn=mpn,
        aspects=aspects,
    )

    short_description = _compact(payload.get("shortDescription"), 1200)
    suspicious_reason = suspicious_listing_reason(
        title=title,
        short_description=short_description,
        condition_bucket=condition_bucket,
    )
    marketing_original_price = _marketing_original_price(payload)

    return EbayListing(
        item_id=item_id,
        legacy_item_id=legacy_item_id,
        title=title,
        product_url=product_url,
        image_url=image_url,
        item_price=item_price,
        shipping_price=shipping_price,
        delivered_price=delivered_price,
        currency=currency or "USD",
        shipping_known=shipping_known,
        condition_id=condition_id,
        condition_name=condition_name,
        condition_bucket=condition_bucket,
        seller_id=seller_id,
        seller_feedback_percentage=seller_feedback_percentage,
        seller_feedback_score=seller_feedback_score,
        buying_options=buying_options,
        item_creation_date=item_creation_date,
        item_end_date=item_end_date,
        estimated_availability_status=availability,
        gtin=gtin,
        epid=epid,
        brand=brand,
        model=model,
        mpn=mpn,
        aspects=aspects,
        short_description=short_description,
        marketing_original_price=marketing_original_price,
        fingerprint=fingerprint,
        exact_identity=exact_identity,
        suspicious_reason=suspicious_reason,
        watch_count=_int(payload.get("watchCount")),
        bid_count=_int(payload.get("bidCount")),
    )


def parse_ebay_search_response(payload: Mapping[str, Any]) -> list[EbayListing]:
    rows = payload.get("itemSummaries")
    if not isinstance(rows, list):
        return []
    return [
        listing
        for row in rows
        if isinstance(row, Mapping)
        for listing in (parse_ebay_item(row),)
        if listing.item_id and listing.title
    ]


def parse_ebay_items_response(payload: Mapping[str, Any]) -> list[EbayListing]:
    rows = payload.get("items")
    if not isinstance(rows, list):
        return []
    return [
        listing
        for row in rows
        if isinstance(row, Mapping)
        for listing in (parse_ebay_item(row),)
        if listing.item_id and listing.title
    ]


def normalize_condition(condition_id: str | None, condition_name: str | None) -> str:
    text = _slug(condition_name)
    condition_id = _text(condition_id)

    if any(term in text for term in ("for_parts", "not_working", "parts_only")):
        return "for_parts"
    if any(term in text for term in ("new_with_defects", "new_defects")):
        return "new_with_defects"
    if any(
        term in text
        for term in (
            "open_box",
            "new_other",
            "new_without_tags",
            "new_without_box",
        )
    ):
        return "open_box"
    if "certified_refurbished" in text:
        return "certified_refurbished"
    if any(term in text for term in ("manufacturer_refurbished", "excellent_refurbished")):
        return "manufacturer_refurbished"
    if any(term in text for term in ("seller_refurbished", "refurbished")):
        return "seller_refurbished"
    if any(term in text for term in ("like_new", "used_excellent", "pre_owned_excellent")):
        return "used_excellent"
    if any(term in text for term in ("very_good", "used_very_good")):
        return "used_very_good"
    if text in {"good", "used_good"} or text.endswith("_good"):
        return "used_good"
    if any(term in text for term in ("acceptable", "fair", "used_acceptable")):
        return "used_acceptable"
    if text.startswith("used") or text.startswith("pre_owned"):
        return "used"
    if text.startswith("new") or text in {"brand_new", "new"}:
        return "new"

    # Common condition IDs are only a fallback because eBay condition IDs are
    # category-dependent and can expand over time.
    id_fallbacks = {
        "1000": "new",
        "1500": "open_box",
        "1750": "new_with_defects",
        "2000": "certified_refurbished",
        "2010": "manufacturer_refurbished",
        "2500": "seller_refurbished",
        "2750": "used_excellent",
        "3000": "used",
        "4000": "used_very_good",
        "5000": "used_good",
        "6000": "used_acceptable",
        "7000": "for_parts",
    }
    return id_fallbacks.get(condition_id, "unknown")


def build_listing_fingerprint(
    *,
    item_id: str,
    gtin: str = "",
    epid: str = "",
    brand: str = "",
    model: str = "",
    mpn: str = "",
    aspects: Mapping[str, str] | None = None,
) -> tuple[str, bool]:
    clean_gtin = _identity_token(gtin)
    clean_epid = _identity_token(epid)
    clean_brand = _identity_token(brand)
    clean_model = _identity_token(model)
    clean_mpn = _identity_token(mpn)
    selected_aspects = _identity_aspects(aspects or {})

    if clean_gtin:
        base = f"gtin:{clean_gtin}"
        exact = True
    elif clean_epid:
        base = f"epid:{clean_epid}"
        exact = True
    elif clean_brand and clean_mpn:
        base = f"brand_mpn:{clean_brand}:{clean_mpn}"
        exact = True
    elif clean_brand and clean_model:
        base = f"brand_model:{clean_brand}:{clean_model}"
        exact = True
    else:
        return f"listing:{_identity_token(item_id)}", False

    variant = "|".join(f"{key}={value}" for key, value in selected_aspects)
    digest = sha256(f"{base}|{variant}".encode("utf-8")).hexdigest()[:24]
    return f"ebay-product:{digest}", exact


def comparable_references(
    listings: Iterable[EbayListing],
    *,
    minimum_comparables: int,
) -> dict[str, ComparableReference]:
    groups: dict[tuple[str, str], list[EbayListing]] = defaultdict(list)
    for listing in listings:
        if (
            listing.exact_identity
            and listing.delivered_price is not None
            and listing.delivered_price > 0
            and listing.fixed_price
            and listing.active
            and not listing.suspicious_reason
            and listing.condition_bucket != "unknown"
        ):
            groups[listing.comparable_key].append(listing)

    references: dict[str, ComparableReference] = {}
    needed = max(3, int(minimum_comparables))
    for group in groups.values():
        for listing in group:
            # A single seller can create many overpriced listings. Count at most
            # one comparable per distinct seller and exclude the candidate's own
            # seller so the reference cannot be manufactured by one vendor.
            by_seller: dict[str, float] = {}
            for other in group:
                if (
                    other.item_id == listing.item_id
                    or other.delivered_price is None
                    or other.delivered_price <= 0
                    or not other.seller_id
                    or other.seller_id == listing.seller_id
                ):
                    continue
                price = float(other.delivered_price)
                existing = by_seller.get(other.seller_id)
                if existing is None or price < existing:
                    by_seller[other.seller_id] = price
            if len(by_seller) < needed:
                continue
            values = _trim_outliers(list(by_seller.values()))
            if len(values) < needed:
                continue
            references[listing.item_id] = ComparableReference(
                price=round(float(median(values)), 2),
                sample_size=len(values),
            )
    return references


def suspicious_listing_reason(
    *,
    title: str,
    short_description: str = "",
    condition_bucket: str = "",
) -> str:
    text = _slug(f"{title} {short_description}")
    for phrase in SUSPICIOUS_PHRASES:
        token = _slug(phrase)
        if token and token in text:
            return phrase
    if condition_bucket in {"for_parts", "new_with_defects", "unknown"}:
        return f"condition:{condition_bucket}"
    return ""


def _trim_outliers(values: list[float]) -> list[float]:
    ordered = sorted(value for value in values if value > 0)
    if len(ordered) < 10:
        return ordered
    trim = max(1, int(len(ordered) * 0.1))
    return ordered[trim:-trim] or ordered


def _identity_aspects(aspects: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    normalized = {_slug(key): _identity_token(value) for key, value in aspects.items()}
    selected = []
    for name in IDENTITY_ASPECTS:
        key = _slug(name)
        value = normalized.get(key, "")
        if value:
            selected.append((key, value))
    return tuple(selected)


def _localized_aspects(payload: Mapping[str, Any]) -> dict[str, str]:
    rows = payload.get("localizedAspects")
    result: dict[str, str] = {}
    if not isinstance(rows, list):
        return result
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        name = _compact(row.get("name"), 100)
        value = _compact(row.get("value"), 300)
        if name and value:
            result[name] = value
    return result


def _aspect(aspects: Mapping[str, str], name: str) -> str:
    target = _slug(name)
    for key, value in aspects.items():
        if _slug(key) == target:
            return value
    return ""


def _availability_status(payload: Mapping[str, Any]) -> str:
    direct = _text(payload.get("estimatedAvailabilityStatus"))
    if direct:
        return direct
    rows = payload.get("estimatedAvailabilities")
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, Mapping):
                value = _text(row.get("estimatedAvailabilityStatus"))
                if value:
                    return value
    return "AVAILABLE"


def _marketing_original_price(payload: Mapping[str, Any]) -> float | None:
    marketing = payload.get("marketingPrice")
    if not isinstance(marketing, Mapping):
        return None
    value, _ = _amount(marketing.get("originalPrice"))
    return value


def _shipping_price(payload: Mapping[str, Any]) -> tuple[float | None, bool]:
    direct, _ = _amount(payload.get("shippingCost"))
    if direct is not None:
        return round(direct, 2), True

    rows = payload.get("shippingOptions")
    costs: list[float] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            value, _ = _amount(row.get("shippingCost"))
            if value is not None:
                costs.append(value)
    if costs:
        return round(min(costs), 2), True
    return None, False


def _amount(value: Any) -> tuple[float | None, str]:
    if not isinstance(value, Mapping):
        return None, ""
    parsed = _float(value.get("value"))
    currency = _text(value.get("currency"))
    return parsed, currency


def _nested_text(mapping: Mapping[str, Any], key: str, nested: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, Mapping):
        return ""
    return _text(value.get(nested))


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, (list, tuple)):
            for nested in value:
                text = _text(nested)
                if text:
                    return text
        else:
            text = _text(value)
            if text:
                return text
    return ""


def _iterable(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple, set)) else []


def _identity_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).lower())


def _slug(value: Any) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", _text(value).lower())).strip("_")


def _compact(value: Any, limit: int) -> str:
    return " ".join(_text(value).split())[: max(0, int(limit))]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Mapping):
        return ""
    return str(value).strip()


def _float(value: Any) -> float | None:
    try:
        parsed = float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
