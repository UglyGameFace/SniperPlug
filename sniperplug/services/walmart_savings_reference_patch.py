from __future__ import annotations

from typing import Any


_PATCHED = False
_ORIGINAL_TRUSTED_REFERENCE_PRICE = None
_ORIGINAL_TRUSTED_REFERENCE_SOURCE = None
_ORIGINAL_BEST_REFERENCE_CONTEXT_PRICE = None

_EXCLUDED_SAVINGS_CONTEXT = (
    "coupon",
    "cash",
    "walmartcash",
    "reward",
    "rewards",
    "promo",
    "promotion",
    "giftcard",
    "gift_card",
)

_SAVINGS_PRICE_PATHS = (
    "savings",
    "saving",
    "savingsAmount",
    "savings_amount",
    "youSave",
    "you_save",
    "priceInfo.savings",
    "priceInfo.saving",
    "priceInfo.savingsAmount",
    "priceInfo.savings_amount",
    "priceInfo.youSave",
    "priceInfo.you_save",
    "priceInfo.priceMap.savings",
    "priceInfo.priceMap.savingsAmount",
    "price_info.savings",
    "price_info.saving",
    "price_info.savingsAmount",
    "price_info.savings_amount",
    "price_info.youSave",
    "price_info.you_save",
    "price_info.priceMap.savings",
    "price_info.priceMap.savingsAmount",
)


def install_walmart_savings_reference_patch() -> None:
    """Treat Walmart page savings as trusted was-price math.

    Walmart's affiliate/search payloads do not always expose the visible product-page
    strike price as `wasPrice`. Some products expose only the current price plus a
    savings amount, while also exposing unrelated MSRP/list values. When the page says
    "You save $X" next to the current product price, the visible was price is:

        current price + savings amount

    That is stronger proof than a random marketplace/MSRP comp, and it prevents good
    deals from being demoted to review-only.
    """
    global _PATCHED, _ORIGINAL_TRUSTED_REFERENCE_PRICE, _ORIGINAL_TRUSTED_REFERENCE_SOURCE, _ORIGINAL_BEST_REFERENCE_CONTEXT_PRICE
    if _PATCHED:
        return

    from sniperplug.providers import walmart as wm

    _ORIGINAL_TRUSTED_REFERENCE_PRICE = wm._trusted_reference_price
    _ORIGINAL_TRUSTED_REFERENCE_SOURCE = wm._trusted_reference_source
    _ORIGINAL_BEST_REFERENCE_CONTEXT_PRICE = wm._best_reference_context_price

    wm._trusted_reference_price = _trusted_reference_price_with_visible_savings
    wm._trusted_reference_source = _trusted_reference_source_with_visible_savings
    wm._best_reference_context_price = _best_reference_context_price_with_visible_savings
    _PATCHED = True


def _trusted_reference_price_with_visible_savings(item: dict, title: str, current_price: float | None) -> tuple[float | None, str | None]:
    from sniperplug.providers import walmart as wm

    references = _reference_candidates_with_visible_savings(item, current_price=current_price)
    ignored: list[str] = []
    if current_price is None or current_price <= 0:
        value, source = _first_trusted_reference_from(references, title=title, current_price=current_price)
        return value, f"Walmart reference price source: {source}" if value and source else None

    for source, value in references:
        if value is None or value <= current_price:
            continue
        suspicious = wm._reference_price_looks_suspicious(source=source, title=title, current_price=current_price, reference_price=value)
        if suspicious:
            return None, wm.ReferenceSignal(
                f"ignored suspicious Walmart {source} reference price: ${value:,.2f}",
                aliases=("ignored low-confidence",),
            )
        if wm._reference_price_is_trusted(source=source, title=title, current_price=current_price, reference_price=value):
            return value, f"Walmart reference price source: {source}"
        ignored.append(f"{source}=${value:,.2f}")

    if ignored:
        return None, "ignored low-confidence Walmart reference price(s): " + ", ".join(ignored[:3])
    return None, None


def _trusted_reference_source_with_visible_savings(*, item: dict, title: str, current_price: float | None, reference_price: float) -> str | None:
    from sniperplug.providers import walmart as wm

    for source, value in _reference_candidates_with_visible_savings(item, current_price=current_price):
        if value is None or abs(value - reference_price) > 0.005:
            continue
        if wm._reference_price_is_trusted(source=source, title=title, current_price=current_price, reference_price=value):
            return source
    return None


def _best_reference_context_price_with_visible_savings(*, item: dict, current_price: float | None) -> tuple[float | None, str | None]:
    best_price: float | None = None
    best_source: str | None = None
    for source, value in _reference_candidates_with_visible_savings(item, current_price=current_price):
        if value is None or value <= 0:
            continue
        if current_price is not None and value <= current_price:
            continue
        if best_price is None or value > best_price:
            best_price = value
            best_source = source
    return best_price, best_source


def _reference_candidates_with_visible_savings(item: dict, *, current_price: float | None) -> list[tuple[str, float | None]]:
    from sniperplug.providers import walmart as wm

    references = list(_visible_savings_reference_candidates(item, current_price=current_price))
    references.extend(wm._reference_price_candidates(item))
    return wm._dedupe_price_candidates(references)


def _visible_savings_reference_candidates(item: dict, *, current_price: float | None) -> list[tuple[str, float | None]]:
    from sniperplug.providers import walmart as wm

    if current_price is None or current_price <= 0:
        return []

    candidates: list[tuple[str, float | None]] = []
    for path in _SAVINGS_PRICE_PATHS:
        if _path_has_excluded_context(path):
            continue
        savings = wm._price_from_path(item, path, allow_unit_price=False)
        reference = _reference_from_savings(current_price=current_price, savings=savings)
        if reference is not None:
            candidates.append((f"wasPriceFromSavings.{path}", reference))

    for key_path, value in wm._walk_payload(item):
        normalized = key_path.lower().replace("_", "").replace("-", "")
        if _path_has_excluded_context(normalized):
            continue
        if "savings" not in normalized and "yousave" not in normalized:
            continue
        savings = wm._price_from_value(value, allow_unit_price=False, path=key_path)
        reference = _reference_from_savings(current_price=current_price, savings=savings)
        if reference is not None:
            candidates.append((f"wasPriceFromSavings.{key_path}", reference))

    return _dedupe_price_candidates(candidates)


def _reference_from_savings(*, current_price: float, savings: float | None) -> float | None:
    if savings is None or savings <= 0:
        return None
    reference = current_price + savings
    if reference <= current_price:
        return None
    return round(reference, 2)


def _first_trusted_reference_from(references: list[tuple[str, float | None]], *, title: str, current_price: float | None) -> tuple[float | None, str | None]:
    from sniperplug.providers import walmart as wm

    for source, value in references:
        if not value or value <= 0:
            continue
        if wm._reference_price_is_trusted(source=source, title=title, current_price=current_price, reference_price=value):
            return value, source
    return None, None


def _path_has_excluded_context(path: str) -> bool:
    normalized = str(path or "").lower().replace(" ", "").replace("-", "")
    return any(term.replace("_", "") in normalized for term in _EXCLUDED_SAVINGS_CONTEXT)


def _dedupe_price_candidates(candidates: list[tuple[str, float | None]]) -> list[tuple[str, float | None]]:
    seen: set[tuple[str, float | None]] = set()
    deduped: list[tuple[str, float | None]] = []
    for source, value in candidates:
        marker = (source, value)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append((source, value))
    return deduped
