from __future__ import annotations

import re
from typing import Any, Iterable

from sniperplug.services.dm_deal_alerts import normalize_categories
from sniperplug.services.dm_deal_routes import (
    DM_ROUTE_PREFIX,
    dm_route_tokens,
    split_dm_route_preferences,
)
from sniperplug.services.dm_product_interests import (
    BRAND_PREFIX,
    PRODUCT_INTEREST_PREFIX,
    product_preference_tokens,
)
from sniperplug.services.opportunity_watchlist import (
    OPPORTUNITY_CATEGORIES,
    OpportunityCategory,
    category_for_title,
)


MUTED_CATEGORY_PREFIX = "muted:"
FAVORITE_CATEGORY_PREFIX = "favorite:"
FLIP_ALERTS_TOKEN = "flip:enabled"
FLIP_MIN_PROFIT_PREFIX = "flip_profit:"
DEFAULT_FLIP_MIN_PROFIT_CENTS = 5_000
CATEGORY_PAGE_SIZE = 25

_CATEGORY_LABELS = {category.key: category.label for category in OPPORTUNITY_CATEGORIES}
_CATEGORY_KEYS = frozenset(_CATEGORY_LABELS)
_CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "baby": ("baby_kids",),
    "babies": ("baby_kids",),
    "infant": ("baby_kids",),
    "infants": ("baby_kids",),
    "newborn": ("baby_kids",),
    "newborns": ("baby_kids",),
    "toddler": ("baby_kids",),
    "toddlers": ("baby_kids",),
    "kids": ("baby_kids",),
    "children": ("baby_kids",),
    "baby_kids": ("baby_kids",),
    "pet": ("pet_supplies",),
    "pets": ("pet_supplies",),
    "pet_supplies": ("pet_supplies",),
    "pc": ("desktop_computers", "gpus", "cpus", "ram", "ssds"),
    "computers": ("desktop_computers", "gpus", "cpus", "ram", "ssds"),
    "desktops": ("desktop_computers",),
    "desktop": ("desktop_computers",),
    "prebuilt": ("desktop_computers",),
    "pc_parts": ("gpus", "cpus", "ram", "ssds"),
    "computer_parts": ("gpus", "cpus", "ram", "ssds"),
    "electronics": (
        "desktop_computers",
        "brand_direct_electronics",
        "apple",
        "gpus",
        "cpus",
        "ram",
        "ssds",
        "mobile_accessories",
        "smart_home",
    ),
    "mobile": ("mobile_accessories",),
    "phones": ("apple", "brand_direct_electronics", "mobile_accessories"),
    "open_box": ("open_box_restored",),
    "refurbished": ("open_box_restored",),
    "refurb": ("open_box_restored",),
}

_STRUCTURED_CATEGORY_KEYS = (
    "category",
    "productCategory",
    "productType",
    "department",
    "taxonomy",
    "breadcrumb",
    "breadcrumbs",
    "productClass",
    "shelfName",
)
_TRUSTED_BABY_CATEGORY_KEYS = frozenset(
    {
        "category",
        "productCategory",
        "productType",
        "department",
        "productClass",
        "shelfName",
    }
)

_STRONG_BABY_TERMS = (
    "infant",
    "newborn",
    "new born",
    "toddler",
    "onesie",
    "bodysuit",
    "body suit",
    "layette",
    "diaper",
    "baby wipes",
    "baby formula",
    "stroller",
    "crib",
    "booster seat",
    "baby monitor",
)

_BABY_APPAREL_RE = re.compile(
    r"\bbaby\s+(?:boys?|girls?|clothes?|clothing|apparel|outfits?|sets?|"
    r"dresses?|shirts?|pants?|pajamas?|sleepwear|shoes?|socks?|hats?|jackets?)\b"
    r"|\b(?:boys?|girls?)\s+baby\b",
    re.IGNORECASE,
)
_BABY_CATEGORY_VALUE_RE = re.compile(
    r"(?:^|[>/|])\s*"
    r"(?:baby(?:\s*(?:&|and)\s*(?:kids?|toddlers?))?|"
    r"infants?|newborns?|toddlers?)"
    r"(?:\s+(?:clothes?|clothing|apparel|gear|feeding|nursery|care|health|"
    r"products?|essentials|furniture|travel|safety|diapers?|toys?))?"
    r"\s*(?:$|[>/|])",
    re.IGNORECASE,
)


def all_personal_categories() -> tuple[OpportunityCategory, ...]:
    """Return every live opportunity category, including future additions."""

    return tuple(OPPORTUNITY_CATEGORIES)


def search_personal_categories(query: str = "") -> tuple[OpportunityCategory, ...]:
    normalized = " ".join(str(query or "").strip().lower().split())
    if not normalized:
        return all_personal_categories()

    matches: list[OpportunityCategory] = []
    for category in OPPORTUNITY_CATEGORIES:
        haystack = " ".join(
            (
                category.key.replace("_", " "),
                category.label,
                *category.terms,
                *category.economic_drivers,
            )
        ).lower()
        if normalized in haystack:
            matches.append(category)
    return tuple(matches)


def personal_category_pages(
    query: str = "",
    *,
    page_size: int = CATEGORY_PAGE_SIZE,
) -> tuple[tuple[OpportunityCategory, ...], ...]:
    size = max(1, min(CATEGORY_PAGE_SIZE, int(page_size)))
    categories = search_personal_categories(query)
    if not categories:
        return ()
    return tuple(
        categories[index : index + size]
        for index in range(0, len(categories), size)
    )


def normalize_personal_categories(values: Iterable[str] | str | None) -> tuple[str, ...]:
    raw = _split_values(values)
    expanded: list[str] = []
    for value in raw:
        key = value.replace("-", "_").replace(" ", "_")
        aliases = _CATEGORY_ALIASES.get(key)
        if aliases:
            expanded.extend(aliases)
            continue
        expanded.extend(
            category
            for category in normalize_categories((key,))
            if category in _CATEGORY_KEYS
        )
    return _dedupe(expanded)


def split_category_preferences(
    values: Iterable[str] | str | None,
) -> tuple[str, ...]:
    """Return the selected categories stored in a preference token list.

    Legacy ``favorite:`` tokens are ignored — a favorite was a soft
    priority marker, never a subscription — and legacy ``muted:`` tokens
    are ignored because muting a category no longer exists. Route,
    product, brand and flip tokens belong to other layers and are skipped
    here.
    """

    selected: list[str] = []
    for value in _split_values(values):
        if (
            value.startswith(FAVORITE_CATEGORY_PREFIX)
            or value.startswith(MUTED_CATEGORY_PREFIX)
            or value == FLIP_ALERTS_TOKEN
            or value.startswith(FLIP_MIN_PROFIT_PREFIX)
            or value.startswith(PRODUCT_INTEREST_PREFIX)
            or value.startswith(BRAND_PREFIX)
            or value.startswith(DM_ROUTE_PREFIX)
        ):
            continue
        selected.extend(normalize_personal_categories((value,)))
    return _dedupe(selected)


def explicit_flip_min_profit_cents(
    values: Iterable[str] | str | None,
) -> int:
    """The price-mistake profit floor the user actually stored, else 0.

    ``flip_settings`` reports a default floor when no token exists; the
    matcher gate must only apply to a floor that is really in storage.
    """

    for value in _split_values(values):
        if value.startswith(FLIP_MIN_PROFIT_PREFIX):
            return flip_settings(values)[1]
    return 0


def flip_settings(
    values: Iterable[str] | str | None,
) -> tuple[bool, int]:
    enabled = False
    minimum_profit_cents = DEFAULT_FLIP_MIN_PROFIT_CENTS
    for value in _split_values(values):
        if value == FLIP_ALERTS_TOKEN:
            enabled = True
        elif value.startswith(FLIP_MIN_PROFIT_PREFIX):
            raw = value[len(FLIP_MIN_PROFIT_PREFIX) :].strip()
            try:
                minimum_profit_cents = int(raw)
            except (TypeError, ValueError):
                continue
    return enabled, max(1_000, min(10_000_000, minimum_profit_cents))


def compose_category_preferences(
    selected: Iterable[str] | str | None,
    *,
    flip_enabled: bool = False,
    flip_min_profit_cents: int = DEFAULT_FLIP_MIN_PROFIT_CENTS,
    product_tokens: Iterable[str] | str | None = None,
    routes: Iterable[str] | str | None = None,
) -> tuple[str, ...]:
    selected_values = normalize_personal_categories(selected)
    flip_tokens: list[str] = []
    safe_profit = max(1_000, min(10_000_000, int(flip_min_profit_cents)))
    if flip_enabled:
        flip_tokens.append(FLIP_ALERTS_TOKEN)
    if flip_enabled or safe_profit != DEFAULT_FLIP_MIN_PROFIT_CENTS:
        flip_tokens.append(f"{FLIP_MIN_PROFIT_PREFIX}{safe_profit}")
    return _dedupe(
        (
            *selected_values,
            *flip_tokens,
            *product_preference_tokens(product_tokens),
            *dm_route_tokens(routes),
        )
    )


def update_flip_settings(
    existing_categories: Iterable[str] | str | None,
    *,
    enabled: bool | None = None,
    minimum_profit_cents: int | None = None,
) -> tuple[str, ...]:
    selected = split_category_preferences(existing_categories)
    current_enabled, current_profit = flip_settings(existing_categories)
    return compose_category_preferences(
        selected,
        flip_enabled=current_enabled if enabled is None else bool(enabled),
        flip_min_profit_cents=(
            current_profit
            if minimum_profit_cents is None
            else int(minimum_profit_cents)
        ),
        product_tokens=existing_categories,
        routes=split_dm_route_preferences(existing_categories),
    )


def category_key_for_card(card: Any) -> str:
    title = _card_title(card)
    structured_pairs = _structured_category_pairs(card)
    structured = " ".join(value for _key, value in structured_pairs)
    combined = " ".join(part for part in (title, structured) if part).lower()

    if any(
        _is_trusted_baby_category(key, value)
        for key, value in structured_pairs
    ):
        return "baby_kids"
    if any(term in combined for term in _STRONG_BABY_TERMS):
        return "baby_kids"
    if _BABY_APPAREL_RE.search(combined):
        return "baby_kids"

    category = category_for_title(combined or title)
    return category.key if category is not None else "uncategorized"


def category_label(category_key: str) -> str:
    key = str(category_key or "uncategorized").strip().lower()
    if key == "uncategorized":
        return "Uncategorized"
    return _CATEGORY_LABELS.get(key, key.replace("_", " ").title())


def _is_trusted_baby_category(key: str, value: str) -> bool:
    if key not in _TRUSTED_BABY_CATEGORY_KEYS:
        return False
    normalized = " ".join(str(value or "").strip().split())
    return bool(_BABY_CATEGORY_VALUE_RE.search(normalized))


def _card_title(card: Any) -> str:
    label = str(getattr(card, "label", "") or "").strip()
    if label:
        return label
    embed = getattr(card, "embed", None)
    return str(getattr(embed, "title", "") or "").strip()


def _structured_category_pairs(card: Any) -> tuple[tuple[str, str], ...]:
    values: list[tuple[str, str]] = []
    for source in (
        getattr(card, "variant_attributes", None),
        getattr(getattr(card, "candidate", None), "variant_attributes", None),
        getattr(getattr(card, "deal", None), "variant_attributes", None),
    ):
        if not isinstance(source, dict):
            continue
        for key in _STRUCTURED_CATEGORY_KEYS:
            value = source.get(key)
            if value not in (None, ""):
                values.append((key, str(value)))
    return tuple(values)


def _split_values(values: Iterable[str] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        source = values.replace("\n", ",").split(",")
    else:
        source = list(values)
    output: list[str] = []
    for value in source:
        text = " ".join(str(value or "").strip().lower().split())
        if text:
            output.append(text)
    return output


def _dedupe(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = str(value or "").strip().lower()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return tuple(output)
