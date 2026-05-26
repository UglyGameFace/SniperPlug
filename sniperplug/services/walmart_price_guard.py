from __future__ import annotations

import re
from typing import Any

from sniperplug.providers import walmart


AMBIGUOUS_CURRENT_PATHS = (
    "price",
    "priceInfo.price",
    "price_info.price",
    "minPrice",
    "min_price",
)

CURRENT_PRICE_PATHS = (
    "salePrice",
    "sale_price",
    "currentPrice",
    "current_price",
    "priceInfo.currentPrice",
    "priceInfo.current_price",
    "priceInfo.salePrice",
    "priceInfo.sale_price",
    "priceInfo.linePrice",
    "priceInfo.itemPrice",
    "price_info.currentPrice",
    "price_info.current_price",
    "price_info.salePrice",
    "price_info.sale_price",
    "price_info.linePrice",
    "price_info.itemPrice",
    "price",
    "priceInfo.price",
    "price_info.price",
    "minPrice",
    "min_price",
)

ACCEPTED_PRICE_KEYS = (
    "currentPrice",
    "current_price",
    "salePrice",
    "sale_price",
    "linePrice",
    "line_price",
    "itemPrice",
    "item_price",
    "finalPrice",
    "final_price",
    "price",
    "amount",
    "value",
    "displayValue",
    "display_value",
    "displayPrice",
    "display_price",
    "priceString",
    "price_string",
    "currencyAmount",
    "currency_amount",
    "currencyValue",
    "currency_value",
    "min",
    "max",
)

UNIT_PRICE_TOKENS = (
    "unitprice",
    "unit_price",
    "priceperunit",
    "price_per_unit",
    "ppu",
    "perunit",
    "per_unit",
    "baseprice",
    "base_price",
)

PACK_COUNT_RE = re.compile(r"\b(\d{1,3})\s*(?:pc|pcs|piece|pieces|ct|count|pack)\b", re.IGNORECASE)


def install_walmart_price_guard() -> None:
    """Protect Walmart scans from using unit/per-item prices as item prices."""
    if getattr(walmart, "_sniperplug_unit_price_guard_installed", False):
        return
    walmart._trusted_current_price = guarded_trusted_current_price
    walmart._current_price_candidates = guarded_current_price_candidates
    walmart._price_from_value = guarded_price_from_value
    walmart._sniperplug_unit_price_guard_installed = True


def guarded_trusted_current_price(item: dict) -> tuple[float | None, str | None]:
    rejected: list[str] = []
    for source, value in guarded_current_price_candidates(item):
        if value is None or value < 0:
            continue
        reason = unit_price_rejection_reason(item, source=source, value=value)
        if reason:
            rejected.append(f"{source}=${value:,.2f} ({reason})")
            continue
        return value, f"Walmart current price source: {source}"
    if rejected:
        return None, "Walmart current price rejected as unit/per-item price: " + ", ".join(rejected[:3])
    return None, "Walmart current price missing"


def guarded_current_price_candidates(item: dict) -> list[tuple[str, float | None]]:
    return [(path, guarded_price_from_path(item, path)) for path in CURRENT_PRICE_PATHS]


def guarded_price_from_path(item: dict, dotted_path: str) -> float | None:
    value: Any = item
    for part in dotted_path.split("."):
        if path_mentions_unit(part):
            return None
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return guarded_price_from_value(value)


def guarded_price_from_value(value: Any) -> float | None:
    if isinstance(value, dict):
        return price_from_accepted_dict(value)
    return float_or_none(value)


def price_from_accepted_dict(value: dict[str, Any]) -> float | None:
    for key in ACCEPTED_PRICE_KEYS:
        if key not in value or path_mentions_unit(key):
            continue
        parsed = guarded_price_from_value(value.get(key)) if isinstance(value.get(key), dict) else float_or_none(value.get(key))
        if parsed is not None:
            return parsed
    return None


def unit_price_rejection_reason(item: dict, *, source: str, value: float) -> str | None:
    if path_mentions_unit(source):
        return "source path is unit price"
    known_units = known_unit_prices(item)
    if any(abs(value - unit_value) <= 0.02 for unit_value in known_units):
        return "matches Walmart unit price field"
    if source in AMBIGUOUS_CURRENT_PATHS and looks_like_unit_price_for_pack(item, value):
        return "ambiguous price matches per-unit pack math"
    return None


def known_unit_prices(value: Any) -> list[float]:
    prices: list[float] = []
    for path, leaf in walk_payload(value):
        if not path_mentions_unit(path):
            continue
        parsed = guarded_price_from_value(leaf)
        if parsed is not None and parsed > 0:
            prices.append(parsed)
    return prices


def looks_like_unit_price_for_pack(item: dict, value: float) -> bool:
    title = str(item.get("name") or item.get("title") or "")
    pack_count = pack_count_from_title(title)
    if pack_count is None or pack_count < 3:
        return False
    if value > 3:
        return False
    # If Walmart has any higher reference/sale-ish amount that is close to unit × pack,
    # treat the tiny ambiguous value as a unit/per-item value, not the item price.
    extended = value * pack_count
    possible_totals = [price for path, price in broad_non_unit_prices(item) if price and price > value]
    return any(abs(price - extended) <= max(0.75, extended * 0.08) for price in possible_totals)


def pack_count_from_title(title: str) -> int | None:
    match = PACK_COUNT_RE.search(title or "")
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def broad_non_unit_prices(item: dict) -> list[tuple[str, float | None]]:
    prices: list[tuple[str, float | None]] = []
    for path, leaf in walk_payload(item):
        if path_mentions_unit(path):
            continue
        parsed = guarded_price_from_value(leaf)
        if parsed is not None:
            prices.append((path, parsed))
    return prices


def walk_payload(value: Any, prefix: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            yield from walk_payload(child, child_prefix)
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            child_prefix = f"{prefix}[{index}]"
            yield from walk_payload(child, child_prefix)
        return
    yield prefix, value


def path_mentions_unit(path: str) -> bool:
    normalized = str(path).replace(".", "_").replace("-", "_").lower()
    compact = normalized.replace("_", "")
    return any(token in normalized or token.replace("_", "") in compact for token in UNIT_PRICE_TOKENS)


def float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        text = value.replace("$", "").replace(",", "").strip()
        match = re.search(r"-?\d+(?:\.\d+)?", text)
        if not match:
            return None
        value = match.group(0)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
