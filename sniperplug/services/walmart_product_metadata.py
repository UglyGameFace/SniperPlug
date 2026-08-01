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


def extract_walmart_product_metadata(
    item: dict[str, Any],
    *,
    current_price: float | None = None,
    reference_price: float | None = None,
    exact_detail: bool = False,
) -> WalmartProductMetadata:
    """Extract compact factual listing metadata from a Walmart API item.

    Nothing is inferred from the title, search query, or a screenshot. Values are
    emitted only when the API payload contains them, except savings math, which
    is derived only from an already trusted current/reference price pair.
    """

    if not isinstance(item, dict):
        return WalmartProductMetadata(attributes={}, signals=())

    attrs: dict[str, str] = {
        "retailerMetadataSource": "exact_detail" if exact_detail else "search",
    }
    signals: list[str] = []

    badges = _extract_badges(item)
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

    return_policy = _extract_return_policy(item)
    if return_policy:
        attrs["returnPolicy"] = return_policy

    location = _extract_location(item)
    if location:
        attrs["fulfillmentLocation"] = location

    condition_options = _extract_condition_options(item)
    if condition_options:
        attrs["conditionOptionsJson"] = json.dumps(
            condition_options,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        attrs["conditionOptions"] = " | ".join(
            _condition_option_label(option) for option in condition_options
        )[:MAX_METADATA_TEXT]

    fulfillment = _extract_fulfillment(item)
    for method, detail in fulfillment.items():
        title = method.title()
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


def _extract_badges(item: dict[str, Any]) -> list[str]:
    badges: list[str] = []

    for key, label in (
        ("clearance", "Clearance"),
        ("rollBack", "Rollback"),
        ("rollback", "Rollback"),
        ("specialBuy", "Special Buy"),
    ):
        if item.get(key) is True:
            badges.append(label)

    for path, value in _walk_payload(item):
        normalized_path = _normalize_key(path)
        if not any(token in normalized_path for token in ("badge", "tag", "label", "flag")):
            continue
        for text in _text_values(value):
            lowered = text.lower()
            if any(token in lowered for token in _PRIVATE_PROMO_TOKENS):
                continue
            for needle, label in _BADGE_LABELS:
                if needle in lowered:
                    badges.append(label)
    return _dedupe(badges)


def _extract_condition_options(item: dict[str, Any]) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()

    for path, value in _walk_containers(item):
        normalized_path = _normalize_key(path)
        if not isinstance(value, dict):
            continue
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
        if not any(token in normalized_path for token in ("condition", "offer", "buybox", "variant")):
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


def _extract_fulfillment(item: dict[str, Any]) -> dict[str, dict[str, str]]:
    output: dict[str, dict[str, str]] = {}
    methods = ("shipping", "pickup", "delivery")

    for path, value in _walk_containers(item):
        if not isinstance(value, dict):
            continue
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


def _extract_return_policy(item: dict[str, Any]) -> str | None:
    direct = _first_text_at_paths(
        item,
        (
            "returnPolicy",
            "returnPolicyText",
            "returnsText",
            "returnInfo.displayText",
            "returnPolicy.displayText",
            "returnPolicy.text",
            "returnPolicy.description",
        ),
    )
    if direct:
        return direct

    for path, value in _walk_payload(item):
        normalized = _normalize_key(path)
        if "return" not in normalized:
            continue
        if isinstance(value, bool):
            continue
        text = _clean_text(value)
        if not text:
            continue
        if re.search(r"\b\d{1,3}\s*[- ]?day", text, flags=re.IGNORECASE):
            return text
        number = _positive_number(value)
        if number is not None and number <= 365:
            return f"{int(number)} days"
    return None


def _extract_location(item: dict[str, Any]) -> str | None:
    for path, value in _walk_containers(item):
        normalized = _normalize_key(path)
        if not isinstance(value, dict):
            continue
        if not any(token in normalized for token in ("location", "store", "fulfillment", "address")):
            continue
        city = _first_text(value.get("city"), value.get("locality"))
        state = _first_text(value.get("state"), value.get("stateCode"), value.get("region"))
        postal = _first_text(value.get("postalCode"), value.get("zipCode"), value.get("zip"))
        store = _first_text(value.get("storeName"), value.get("displayName"))
        parts = [part for part in (store, city, state, postal) if part]
        if parts:
            return ", ".join(_dedupe(parts))[:MAX_METADATA_TEXT]
    return None


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
    condition = option.get("condition") or "Unknown"
    bits = [condition]
    if option.get("status"):
        bits.append(option["status"])
    if option.get("price"):
        bits.append(f"${float(option['price']):,.2f}")
    return " — ".join(bits)


def _walk_payload(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk_payload(child, path)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            path = f"{prefix}[{index}]"
            yield from _walk_payload(child, path)
        return
    yield prefix, value


def _walk_containers(value: Any, prefix: str = "") -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        yield prefix, value
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            yield from _walk_containers(child, path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            path = f"{prefix}[{index}]"
            yield from _walk_containers(child, path)


def _text_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return [str(value)]
    if isinstance(value, dict):
        output: list[str] = []
        for key in ("text", "label", "name", "title", "displayName", "displayText", "value"):
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
        value = _path(item, path)
        text = _first_text(value)
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
            for key in ("text", "label", "name", "title", "displayName", "displayText", "value"):
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
    try:
        match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
        if not match:
            return None
        parsed = float(match.group(0))
    except (TypeError, ValueError):
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
