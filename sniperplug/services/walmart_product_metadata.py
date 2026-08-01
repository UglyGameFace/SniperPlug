from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable


MAX_METADATA_TEXT = 220
MAX_CONDITION_OPTIONS = 6


@dataclass(frozen=True)
class WalmartProductMetadata:
    attributes: dict[str, str]
    signals: tuple[str, ...]


@dataclass(frozen=True)
class PayloadSnapshot:
    containers: tuple[tuple[str, dict[str, Any]], ...]
    leaves: tuple[tuple[str, Any], ...]


_BADGE_LABELS: tuple[tuple[str, str], ...] = (
    ("clearance", "Clearance"),
    ("rollback", "Rollback"),
    ("roll back", "Rollback"),
    ("special buy", "Special Buy"),
    ("overall pick", "Overall Pick"),
    ("best seller", "Best Seller"),
    ("bestseller", "Best Seller"),
    ("popular pick", "Popular Pick"),
    ("reduced price", "Reduced Price"),
    ("price drop", "Price Drop"),
)
_PRIVATE_PROMO_TOKENS = (
    "walmart cash",
    "cashback",
    "cash back",
    "onepay",
    "one pay",
    "coupon",
)
_BADGE_KEYS = (
    "badges",
    "badge",
    "productBadges",
    "merchandisingBadges",
    "labels",
    "tags",
)
_CONDITION_KEYS = (
    "conditionOptions",
    "conditionOffers",
    "secondaryOffers",
    "buyingOptions",
    "variantOffers",
    "offers",
)
_FULFILLMENT_KEYS = (
    "fulfillmentOptions",
    "fulfillmentMethods",
    "fulfillmentSummary",
    "shippingOption",
    "pickupOption",
    "deliveryOption",
)
_LOCATION_PATHS = (
    "fulfillmentLocation",
    "location",
    "storeLocation",
    "pickupStore",
    "store",
    "address",
)


def extract_walmart_product_metadata(
    item: dict[str, Any],
    *,
    current_price: float | None = None,
    reference_price: float | None = None,
    exact_detail: bool = False,
) -> WalmartProductMetadata:
    """Extract compact factual listing metadata from a Walmart API item.

    The extractor never derives facts from the title, search query, or screenshot.
    It uses direct known fields first. Exact-detail payloads are recursively
    indexed once, and all fallback extractors reuse that one snapshot.
    """

    if not isinstance(item, dict):
        return WalmartProductMetadata(attributes={}, signals=())

    snapshot = _snapshot_payload(item) if exact_detail else _shallow_snapshot(item)
    attrs: dict[str, str] = {
        "retailerMetadataSource": "exact_detail" if exact_detail else "search",
    }
    signals: list[str] = []

    badges = _extract_badges(item, snapshot)
    if badges:
        attrs["retailerTags"] = " | ".join(badges)
        signals.append("Walmart listing tags: " + ", ".join(badges))

    current = _positive_number(current_price)
    reference = _positive_number(reference_price)
    if current is not None and reference is not None and reference > current:
        savings = round(reference - current, 2)
        attrs["officialSavingsAmount"] = f"{savings:.2f}"
        attrs["officialSavingsSource"] = "trusted_current_and_reference"
        signals.append(f"Walmart official savings math: ${savings:,.2f}")

    rating = _first_number_at_paths(
        item,
        (
            "customerRating",
            "rating",
            "averageRating",
            "reviews.averageRating",
            "reviewSummary.averageRating",
        ),
    )
    if rating is not None and 0 <= rating <= 5:
        attrs["rating"] = f"{rating:g}"

    reviews = _first_integer_at_paths(
        item,
        (
            "numReviews",
            "reviewCount",
            "reviews.count",
            "reviewSummary.reviewCount",
            "reviewSummary.count",
        ),
    )
    if reviews is not None and reviews >= 0:
        attrs["reviews"] = str(reviews)

    purchase_context = _first_text_at_paths(
        item,
        (
            "priceDisplayCodes.priceDisplayCondition",
            "priceInfo.priceDisplayCondition",
            "priceInfo.priceDisplayText",
            "purchaseContext",
            "priceContext",
        ),
    )
    if purchase_context:
        attrs["purchaseContext"] = purchase_context

    return_policy = _extract_return_policy(item, snapshot)
    if return_policy:
        attrs["returnPolicy"] = return_policy

    location = _extract_location(item, snapshot)
    if location:
        attrs["fulfillmentLocation"] = location

    condition_options = _extract_condition_options(item, snapshot)
    if condition_options:
        attrs["conditionOptionsJson"] = json.dumps(
            condition_options,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        attrs["conditionOptions"] = " | ".join(
            _condition_option_label(option) for option in condition_options
        )[:MAX_METADATA_TEXT]

    fulfillment = _extract_fulfillment(item, snapshot)
    for method, detail in fulfillment.items():
        status = detail.get("status")
        text = detail.get("text")
        if status:
            attrs[f"{method}Status"] = status
        if text:
            attrs[f"{method}Text"] = text
        if status or text:
            rendered = " — ".join(value for value in (status, text) if value)
            signals.append(f"Walmart {method}: {rendered}")

    return WalmartProductMetadata(attributes=attrs, signals=tuple(_dedupe(signals)))


def _extract_badges(item: dict[str, Any], snapshot: PayloadSnapshot) -> list[str]:
    badges: list[str] = []
    for key, label in (
        ("clearance", "Clearance"),
        ("rollBack", "Rollback"),
        ("rollback", "Rollback"),
        ("specialBuy", "Special Buy"),
    ):
        if item.get(key) is True:
            badges.append(label)

    direct_values = [item.get(key) for key in _BADGE_KEYS if item.get(key) is not None]
    for value in direct_values:
        badges.extend(_badge_labels_from_value(value))

    if direct_values:
        return _dedupe(badges)

    for path, value in snapshot.leaves:
        normalized_path = _normalize_key(path)
        if not any(token in normalized_path for token in ("badge", "tag", "label", "flag")):
            continue
        badges.extend(_badge_labels_from_value(value))
    return _dedupe(badges)


def _badge_labels_from_value(value: Any) -> list[str]:
    labels: list[str] = []
    for text in _text_values(value):
        lowered = text.lower()
        if any(token in lowered for token in _PRIVATE_PROMO_TOKENS):
            continue
        for needle, label in _BADGE_LABELS:
            if needle in lowered:
                labels.append(label)
    return labels


def _extract_condition_options(
    item: dict[str, Any],
    snapshot: PayloadSnapshot,
) -> list[dict[str, str]]:
    direct = _direct_dict_containers(item, _CONDITION_KEYS)
    containers = direct or [
        (path, value)
        for path, value in snapshot.containers
        if any(token in _normalize_key(path) for token in ("condition", "offer", "buybox", "variant"))
    ]

    options: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for path, value in containers:
        normalized_path = _normalize_key(path)
        condition = _first_text(
            value.get("condition"),
            value.get("conditionType"),
            value.get("conditionName"),
            value.get("conditionDisplayName"),
            _nested(value, "condition", "name"),
            _nested(value, "condition", "type"),
        )
        if not condition:
            continue
        if not direct and not any(
            token in normalized_path for token in ("condition", "offer", "buybox", "variant")
        ):
            continue

        price = _first_number(
            value.get("salePrice"),
            value.get("currentPrice"),
            value.get("price"),
            _nested(value, "priceInfo", "currentPrice"),
            _nested(value, "price", "amount"),
        )
        status = _availability_status(value)
        key = (condition.lower(), status or "", f"{price:.2f}" if price is not None else "")
        if key in seen:
            continue
        seen.add(key)

        option: dict[str, str] = {"condition": _clean_text(condition)}
        if status:
            option["status"] = status
        if price is not None and price >= 0:
            option["price"] = f"{price:.2f}"
        if _explicit_true(value.get("selected"), value.get("isSelected"), value.get("isCurrent")):
            option["selected"] = "yes"
        options.append(option)
        if len(options) >= MAX_CONDITION_OPTIONS:
            break
    return options


def _extract_fulfillment(
    item: dict[str, Any],
    snapshot: PayloadSnapshot,
) -> dict[str, dict[str, str]]:
    direct = _direct_dict_containers(item, _FULFILLMENT_KEYS)
    containers = direct or [
        (path, value)
        for path, value in snapshot.containers
        if any(token in _normalize_key(path) for token in ("fulfillment", "shipping", "pickup", "delivery"))
    ]

    output: dict[str, dict[str, str]] = {}
    methods = ("shipping", "pickup", "delivery")
    for path, value in containers:
        haystack = " ".join(
            str(part or "")
            for part in (
                path,
                value.get("type"),
                value.get("method"),
                value.get("fulfillmentType"),
                value.get("fulfillmentMethod"),
                value.get("displayName"),
                value.get("name"),
            )
        ).lower()
        method = next((candidate for candidate in methods if candidate in haystack), None)
        if method is None:
            continue

        status = _availability_status(value)
        text = _first_text(
            value.get("message"),
            value.get("displayText"),
            value.get("fulfillmentText"),
            value.get("arrivalText"),
            value.get("deliveryDate"),
            value.get("arrivalDate"),
            value.get("sla"),
            _nested(value, "availability", "displayText"),
            _nested(value, "availability", "status"),
        )
        if not status and not text:
            continue

        existing = output.setdefault(method, {})
        if status and "status" not in existing:
            existing["status"] = status
        if text and "text" not in existing:
            existing["text"] = _clean_text(text)
    return output


def _extract_return_policy(item: dict[str, Any], snapshot: PayloadSnapshot) -> str | None:
    direct = _first_text_at_paths(
        item,
        (
            "returnPolicy",
            "returnPolicyText",
            "returnsText",
            "returnInfo.displayText",
            "returnPolicy.returnWindow",
            "returnPolicy.displayText",
            "returnPolicy.text",
            "returnPolicy.description",
        ),
    )
    if direct:
        return direct

    for path, value in snapshot.leaves:
        if "return" not in _normalize_key(path) or isinstance(value, bool):
            continue
        text = _clean_text(value)
        if re.search(r"\b\d{1,3}\s*[- ]?day", text, flags=re.IGNORECASE):
            return text
        number = _positive_number(value)
        if number is not None and number <= 365:
            return f"{int(number)} days"
    return None


def _extract_location(item: dict[str, Any], snapshot: PayloadSnapshot) -> str | None:
    # Prefer explicit location objects and never accept a store/display label by
    # itself. Geographic proof requires a postal code or a city/state pair.
    direct: list[tuple[str, dict[str, Any]]] = []
    for path in _LOCATION_PATHS:
        value = _path(item, path)
        if isinstance(value, dict):
            direct.append((path, value))
    for _, value in direct:
        location = _location_from_dict(value)
        if location:
            return location

    for path, value in snapshot.containers:
        normalized = _normalize_key(path)
        if not any(token in normalized for token in ("location", "store", "address")):
            continue
        location = _location_from_dict(value)
        if location:
            return location
    return None


def _location_from_dict(value: dict[str, Any]) -> str | None:
    city = _first_text(value.get("city"), value.get("locality"))
    state = _first_text(value.get("state"), value.get("stateCode"), value.get("region"))
    postal = _first_text(value.get("postalCode"), value.get("zipCode"), value.get("zip"))
    if not postal and not (city and state):
        return None
    store = _first_text(value.get("storeName"), value.get("displayName"))
    parts = [part for part in (store, city, state, postal) if part]
    return ", ".join(_dedupe(parts))[:MAX_METADATA_TEXT]


def _availability_status(value: dict[str, Any]) -> str | None:
    raw = _first_text(
        value.get("availabilityStatus"),
        value.get("stockStatus"),
        value.get("availability"),
        value.get("status"),
        _nested(value, "availability", "status"),
    )
    if raw:
        normalized = re.sub(r"[_-]+", " ", raw).strip().lower()
        aliases = {
            "in stock": "Available",
            "available": "Available",
            "out of stock": "Out of stock",
            "not available": "Not available",
            "unavailable": "Not available",
            "check nearby": "Check nearby",
            "limited stock": "Limited stock",
        }
        return aliases.get(normalized, normalized.title())

    explicit = _first_explicit_bool(
        value.get("available"),
        value.get("isAvailable"),
        value.get("inStock"),
    )
    if explicit is True:
        return "Available"
    if explicit is False:
        return "Not available"
    return None


def _condition_option_label(option: dict[str, str]) -> str:
    bits = [option.get("condition") or "Unknown"]
    if option.get("status"):
        bits.append(option["status"])
    if option.get("price"):
        bits.append(f"${float(option['price']):,.2f}")
    return " — ".join(bits)


def _snapshot_payload(value: Any) -> PayloadSnapshot:
    containers: list[tuple[str, dict[str, Any]]] = []
    leaves: list[tuple[str, Any]] = []

    def walk(current: Any, prefix: str = "") -> None:
        if isinstance(current, dict):
            containers.append((prefix, current))
            for key, child in current.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                walk(child, path)
            return
        if isinstance(current, list):
            for index, child in enumerate(current):
                path = f"{prefix}[{index}]"
                walk(child, path)
            return
        leaves.append((prefix, current))

    walk(value)
    return PayloadSnapshot(tuple(containers), tuple(leaves))


def _shallow_snapshot(item: dict[str, Any]) -> PayloadSnapshot:
    containers: list[tuple[str, dict[str, Any]]] = [("", item)]
    leaves: list[tuple[str, Any]] = []
    for key, value in item.items():
        if isinstance(value, dict):
            containers.append((str(key), value))
        elif not isinstance(value, list):
            leaves.append((str(key), value))
    return PayloadSnapshot(tuple(containers), tuple(leaves))


def _direct_dict_containers(
    item: dict[str, Any],
    keys: tuple[str, ...],
) -> list[tuple[str, dict[str, Any]]]:
    output: list[tuple[str, dict[str, Any]]] = []
    for key in keys:
        value = item.get(key)
        if isinstance(value, dict):
            output.append((key, value))
        elif isinstance(value, list):
            output.extend(
                (f"{key}[{index}]", child)
                for index, child in enumerate(value)
                if isinstance(child, dict)
            )
    return output


def _text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    if isinstance(value, dict):
        output: list[str] = []
        for key in (
            "text",
            "label",
            "name",
            "title",
            "displayName",
            "displayText",
            "value",
        ):
            child = value.get(key)
            if isinstance(child, str):
                output.append(child)
        return output
    if isinstance(value, list):
        output: list[str] = []
        for child in value:
            output.extend(_text_values(child))
        return output
    return []


def _first_text_at_paths(item: dict[str, Any], paths: tuple[str, ...]) -> str | None:
    for path in paths:
        text = _first_text(_path(item, path))
        if text:
            return _clean_text(text)
    return None


def _first_number_at_paths(item: dict[str, Any], paths: tuple[str, ...]) -> float | None:
    for path in paths:
        parsed = _first_number(_path(item, path))
        if parsed is not None:
            return parsed
    return None


def _first_integer_at_paths(item: dict[str, Any], paths: tuple[str, ...]) -> int | None:
    number = _first_number_at_paths(item, paths)
    return int(number) if number is not None else None


def _path(item: dict[str, Any], path: str) -> Any:
    current: Any = item
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _nested(value: dict[str, Any], *parts: str) -> Any:
    current: Any = value
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, dict):
            for key in (
                "text",
                "label",
                "name",
                "title",
                "displayName",
                "displayText",
                "returnWindow",
                "value",
            ):
                child = value.get(key)
                if isinstance(child, str) and child.strip():
                    return _clean_text(child)
            continue
        text = _clean_text(value)
        if text:
            return text
    return None


def _first_number(*values: Any) -> float | None:
    for value in values:
        parsed = _positive_number(value, allow_zero=True)
        if parsed is not None:
            return parsed
    return None


def _positive_number(value: Any, *, allow_zero: bool = False) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        for key in ("amount", "price", "value", "displayValue", "currencyAmount"):
            parsed = _positive_number(value.get(key), allow_zero=allow_zero)
            if parsed is not None:
                return parsed
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    if not match:
        return None
    try:
        parsed = float(match.group(0))
    except ValueError:
        return None
    if parsed > 0 or (allow_zero and parsed == 0):
        return parsed
    return None


def _first_explicit_bool(*values: Any) -> bool | None:
    for value in values:
        if isinstance(value, bool):
            return value
        text = str(value or "").strip().lower()
        if text in {"true", "yes", "1", "y"}:
            return True
        if text in {"false", "no", "0", "n"}:
            return False
    return None


def _explicit_true(*values: Any) -> bool:
    return _first_explicit_bool(*values) is True


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())[:MAX_METADATA_TEXT]


def _normalize_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _dedupe(values: Iterable[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_text(value)
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output
