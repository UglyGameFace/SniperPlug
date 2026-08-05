from __future__ import annotations

import re
from typing import Any, Iterable

from sniperplug.services.dm_deal_alerts import normalize_categories, normalize_terms
from sniperplug.services.dm_flip_opportunities import DEFAULT_FLIP_MIN_PROFIT_CENTS
from sniperplug.services.opportunity_watchlist import (
    OPPORTUNITY_CATEGORIES,
    OpportunityCategory,
    category_for_title,
)


CATEGORY_MUTE_PREFIX = "category:"
FAVORITE_CATEGORY_PREFIX = "favorite:"
FLIP_ALERTS_TOKEN = "flip:enabled"
FLIP_MIN_PROFIT_PREFIX = "flip_profit:"
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
    "pc": ("gpus", "cpus", "ram", "ssds"),
    "pc_parts": ("gpus", "cpus", "ram", "ssds"),
    "computer_parts": ("gpus", "cpus", "ram", "ssds"),
    "electronics": (
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


def split_exclude_terms(values: Iterable[str] | str | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    keywords: list[str] = []
    muted_categories: list[str] = []
    for value in _split_values(values):
        if value.startswith(CATEGORY_MUTE_PREFIX):
            category = value[len(CATEGORY_MUTE_PREFIX) :].strip()
            muted_categories.extend(normalize_personal_categories((category,)))
        else:
            keywords.append(value)
    return normalize_terms(keywords), _dedupe(muted_categories)


def compose_exclude_terms(
    keywords: Iterable[str] | str | None,
    muted_categories: Iterable[str] | str | None,
) -> tuple[str, ...]:
    category_tokens = tuple(
        f"{CATEGORY_MUTE_PREFIX}{category}"
        for category in normalize_personal_categories(muted_categories)
    )
    # Category choices are intentionally uncapped. Only free-form keywords keep
    # their bounded safety cap.
    return _dedupe((*category_tokens, *normalize_terms(keywords)))


def update_category_mutes(
    existing_excludes: Iterable[str] | str | None,
    *,
    add: Iterable[str] | str | None = None,
    remove: Iterable[str] | str | None = None,
    replacement_keywords: Iterable[str] | str | None = None,
) -> tuple[str, ...]:
    current_keywords, current_muted = split_exclude_terms(existing_excludes)
    keywords = (
        normalize_terms(replacement_keywords)
        if replacement_keywords is not None
        else current_keywords
    )
    muted = list(current_muted)
    muted.extend(normalize_personal_categories(add))
    remove_set = set(normalize_personal_categories(remove))
    muted = [category for category in _dedupe(muted) if category not in remove_set]
    return compose_exclude_terms(keywords, muted)


def split_category_preferences(
    values: Iterable[str] | str | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    selected: list[str] = []
    favorites: list[str] = []
    for value in _split_values(values):
        if value.startswith(FAVORITE_CATEGORY_PREFIX):
            category = value[len(FAVORITE_CATEGORY_PREFIX) :].strip()
            favorites.extend(normalize_personal_categories((category,)))
        elif value == FLIP_ALERTS_TOKEN or value.startswith(FLIP_MIN_PROFIT_PREFIX):
            continue
        else:
            selected.extend(normalize_personal_categories((value,)))
    return _dedupe(selected), _dedupe(favorites)


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
    favorites: Iterable[str] | str | None,
    *,
    flip_enabled: bool = False,
    flip_min_profit_cents: int = DEFAULT_FLIP_MIN_PROFIT_CENTS,
) -> tuple[str, ...]:
    selected_values = normalize_personal_categories(selected)
    favorite_tokens = tuple(
        f"{FAVORITE_CATEGORY_PREFIX}{category}"
        for category in normalize_personal_categories(favorites)
    )
    flip_tokens: list[str] = []
    safe_profit = max(1_000, min(10_000_000, int(flip_min_profit_cents)))
    if flip_enabled:
        flip_tokens.append(FLIP_ALERTS_TOKEN)
    if flip_enabled or safe_profit != DEFAULT_FLIP_MIN_PROFIT_CENTS:
        flip_tokens.append(f"{FLIP_MIN_PROFIT_PREFIX}{safe_profit}")
    return _dedupe((*selected_values, *favorite_tokens, *flip_tokens))


def update_favorite_categories(
    existing_categories: Iterable[str] | str | None,
    *,
    add: Iterable[str] | str | None = None,
    remove: Iterable[str] | str | None = None,
    replacement_selected: Iterable[str] | str | None = None,
) -> tuple[str, ...]:
    current_selected, current_favorites = split_category_preferences(
        existing_categories
    )
    current_flip_enabled, current_flip_profit = flip_settings(existing_categories)
    selected = (
        normalize_personal_categories(replacement_selected)
        if replacement_selected is not None
        else current_selected
    )
    favorites = list(current_favorites)
    favorites.extend(normalize_personal_categories(add))
    remove_set = set(normalize_personal_categories(remove))
    favorites = [
        category
        for category in _dedupe(favorites)
        if category not in remove_set
    ]
    return compose_category_preferences(
        selected,
        favorites,
        flip_enabled=current_flip_enabled,
        flip_min_profit_cents=current_flip_profit,
    )


def update_flip_settings(
    existing_categories: Iterable[str] | str | None,
    *,
    enabled: bool | None = None,
    minimum_profit_cents: int | None = None,
) -> tuple[str, ...]:
    selected, favorites = split_category_preferences(existing_categories)
    current_enabled, current_profit = flip_settings(existing_categories)
    return compose_category_preferences(
        selected,
        favorites,
        flip_enabled=current_enabled if enabled is None else bool(enabled),
        flip_min_profit_cents=(
            current_profit
            if minimum_profit_cents is None
            else int(minimum_profit_cents)
        ),
    )


def category_key_for_card(card: Any) -> str:
    title = _card_title(card)
    structured = " ".join(_structured_category_values(card))
    combined = " ".join(part for part in (title, structured) if part).lower()

    structured_lower = structured.lower()
    if any(term in structured_lower for term in ("baby", "infant", "newborn", "toddler")):
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


def _card_title(card: Any) -> str:
    label = str(getattr(card, "label", "") or "").strip()
    if label:
        return label
    embed = getattr(card, "embed", None)
    return str(getattr(embed, "title", "") or "").strip()


def _structured_category_values(card: Any) -> tuple[str, ...]:
    values: list[str] = []
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
                values.append(str(value))
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
