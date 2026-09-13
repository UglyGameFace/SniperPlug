from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from sniperplug.services.opportunity_watchlist import category_for_title


PRODUCT_INTEREST_PREFIX = "interest:"
BRAND_PREFIX = "brand:"
MAX_PRODUCT_INTERESTS = 20
MAX_BRANDS = 20


def normalize_match_text(value: object) -> str:
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def normalize_slug(value: object) -> str:
    return normalize_match_text(value).replace(" ", "_")


def phrase_in_text(normalized_text: str, phrase: str) -> bool:
    haystack = f" {normalize_match_text(normalized_text)} "
    needle = normalize_match_text(phrase)
    return bool(needle) and f" {needle} " in haystack


def split_values(values: Iterable[str] | str | None) -> list[str]:
    if values is None:
        return []
    source = (
        values.replace("\n", ",").split(",")
        if isinstance(values, str)
        else list(values)
    )
    return [
        " ".join(str(value or "").strip().split())
        for value in source
        if " ".join(str(value or "").strip().split())
    ]


def split_tokens(values: Iterable[str] | str | None) -> list[str]:
    return [value.strip().lower() for value in split_values(values) if value.strip()]


def dedupe(values: Iterable[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        text = str(value or "").strip().lower()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return tuple(output)


@dataclass(frozen=True)
class ProductInterestProfile:
    key: str
    label: str
    emoji: str
    aliases: tuple[str, ...]
    required_groups: tuple[tuple[str, ...], ...]
    excluded_terms: tuple[str, ...] = ()
    description: str = ""

    def matches(self, search_text: str) -> bool:
        normalized = normalize_match_text(search_text)
        if not normalized:
            return False
        if any(phrase_in_text(normalized, term) for term in self.excluded_terms):
            return False
        return all(
            any(phrase_in_text(normalized, term) for term in group)
            for group in self.required_groups
            if group
        )


def _profile(
    key: str,
    label: str,
    emoji: str,
    aliases: tuple[str, ...],
    *required_groups: tuple[str, ...],
    excluded: tuple[str, ...] = (),
    description: str = "",
) -> ProductInterestProfile:
    return ProductInterestProfile(
        key=key,
        label=label,
        emoji=emoji,
        aliases=aliases,
        required_groups=tuple(required_groups),
        excluded_terms=excluded,
        description=description,
    )


# Product-level profiles only. Department-sized filtering stays in the existing
# category system; these are the narrower second layer requested by users.
PRODUCT_INTEREST_PROFILES: tuple[ProductInterestProfile, ...] = (
    _profile(
        "pokemon_tcg_sealed",
        "Pokémon TCG sealed products",
        "🃏",
        ("pokemon tcg", "pokemon cards", "pokemon packs", "pokemon sealed", "pokemon resale", "pokemon booster", "pokemon etb"),
        ("pokemon", "pokémon"),
        ("booster pack", "booster bundle", "booster box", "elite trainer box", "etb", "collection box", "premium collection", "ultra premium collection", "ultra-premium collection", "blister", "collector chest", "tin", "trading card", "tcg"),
        excluded=("binder", "card sleeve", "sleeves", "deck box", "playmat", "plush", "costume", "video game", "book", "poster"),
        description="Sealed packs, boxes, tins, ETBs and collections. Resale qualification still comes from the separate strict Flip assessment.",
    ),
    _profile("needoh", "NeeDoh sensory toys", "🧸", ("needoh", "nee doh", "nee-doh"), ("needoh", "nee doh", "nee-doh")),
    _profile(
        "portable_chargers",
        "Portable chargers / power banks",
        "🔋",
        ("portable charger", "portable chargers", "power bank", "power banks", "battery bank", "portable battery"),
        ("portable charger", "power bank", "battery bank", "portable battery", "powercore", "magsafe battery", "magnetic battery pack"),
        description="Portable battery packs and power banks, not ordinary wall chargers or cables.",
    ),
    _profile(
        "magsafe_batteries",
        "MagSafe / magnetic battery packs",
        "🔋",
        ("magsafe battery", "magnetic battery", "magsafe power bank"),
        ("magsafe", "magnetic"),
        ("battery pack", "power bank", "portable charger", "portable battery"),
    ),
    _profile(
        "designer_fragrance",
        "Designer cologne / perfume",
        "🧴",
        ("cologne", "designer cologne", "mens cologne", "men's cologne", "perfume", "designer fragrance", "edp", "edt"),
        ("cologne", "perfume", "fragrance", "eau de parfum", "eau de toilette", "parfum", "edp", "edt"),
        excluded=("body spray", "body mist", "fragrance mist", "deodorant", "antiperspirant", "body lotion", "shower gel"),
    ),
    _profile("lego_sets", "LEGO sets", "🧱", ("lego", "lego sets", "lego set"), ("lego",), excluded=("storage box", "display case", "keychain", "key chain", "shirt", "book", "sticker")),
    _profile("hot_wheels_premium", "Hot Wheels premium collectibles", "🏎️", ("hot wheels premium", "hot wheels car culture", "hot wheels collectible"), ("hot wheels",), ("premium", "car culture", "team transport", "collector", "boulevard")),
    _profile("funko_pop", "Funko Pop collectibles", "🎁", ("funko", "funko pop", "pop vinyl"), ("funko pop", "funko! pop", "pop vinyl")),
    _profile("gaming_controllers", "Gaming controllers", "🎮", ("controller", "gaming controller", "xbox controller", "dualsense"), ("controller", "gamepad", "dualsense", "joy-con", "joy con")),
    _profile("headphones_earbuds", "Headphones / earbuds", "🎧", ("headphones", "earbuds", "gaming headset", "wireless earbuds"), ("headphones", "earbuds", "ear buds", "headset", "airpods")),
    _profile("bluetooth_speakers", "Bluetooth speakers", "🔊", ("bluetooth speaker", "portable speaker", "wireless speaker"), ("bluetooth speaker", "portable speaker", "wireless speaker")),
    _profile("ssds", "SSDs / portable SSDs", "💾", ("ssd", "nvme", "portable ssd", "external ssd"), ("ssd", "nvme", "solid state drive"), excluded=("enclosure", "case only", "adapter")),
    _profile("microsd_cards", "microSD memory cards", "💾", ("microsd", "micro sd", "memory card"), ("microsd", "micro sd"), excluded=("adapter only", "reader only")),
    _profile("robot_vacuums", "Robot vacuums", "🤖", ("robot vacuum", "robotic vacuum", "roomba"), ("robot vacuum", "robotic vacuum", "roomba")),
    _profile("air_fryers", "Air fryers", "🍳", ("air fryer", "air fryers"), ("air fryer",)),
    _profile("countertop_ice_makers", "Countertop ice makers", "🧊", ("ice maker", "countertop ice maker"), ("ice maker",), excluded=("refrigerator replacement", "ice maker kit")),
    _profile("coffee_makers", "Coffee makers", "☕", ("coffee maker", "keurig", "espresso machine"), ("coffee maker", "keurig", "espresso machine", "coffee machine")),
    _profile("power_tools", "Power tools", "🛠️", ("power tools", "drill", "impact driver", "circular saw"), ("drill", "impact driver", "impact wrench", "circular saw", "reciprocating saw", "power tool", "tool combo kit")),
    _profile("tool_batteries", "Tool batteries / charger kits", "🔋", ("tool battery", "dewalt battery", "milwaukee battery", "ryobi battery"), ("battery", "battery kit", "battery charger kit"), ("dewalt", "milwaukee", "ryobi", "makita", "hart", "craftsman", "power tool")),
    _profile("diapers", "Diapers", "👶", ("diapers", "diaper"), ("diaper", "diapers"), excluded=("diaper bag", "diaper pail")),
    _profile("strollers", "Baby strollers", "👶", ("stroller", "baby stroller"), ("stroller",), excluded=("stroller accessory", "stroller organizer")),
    _profile("car_seats", "Car seats / boosters", "👶", ("car seat", "booster seat", "infant car seat"), ("car seat", "booster seat"), excluded=("seat cover", "car seat protector")),
    _profile("energy_drinks", "Energy drinks", "🥤", ("energy drink", "energy drinks"), ("energy drink",)),
    _profile("sneakers", "Sneakers", "👟", ("sneakers", "sneaker", "athletic shoes"), ("sneaker", "sneakers", "athletic shoe", "running shoe", "basketball shoe")),
    _profile("smartwatches", "Smartwatches", "⌚", ("smartwatch", "smart watch", "apple watch", "galaxy watch"), ("smartwatch", "smart watch", "apple watch", "galaxy watch", "pixel watch"), excluded=("watch band", "watch strap", "screen protector")),
    _profile("tablets", "Tablets", "📱", ("tablet", "ipad", "galaxy tab"), ("tablet", "ipad", "galaxy tab", "fire hd"), excluded=("tablet case", "screen protector", "keyboard case")),
    _profile("laptops", "Laptops", "💻", ("laptop", "notebook computer", "macbook"), ("laptop", "notebook computer", "macbook", "chromebook"), excluded=("laptop bag", "laptop sleeve", "laptop stand")),
    _profile("monitors", "Computer monitors", "🖥️", ("monitor", "gaming monitor", "computer monitor"), ("monitor",), excluded=("monitor arm", "monitor stand", "baby monitor")),
)

_PROFILE_BY_KEY = {profile.key: profile for profile in PRODUCT_INTEREST_PROFILES}
_ALIAS_TO_KEY: dict[str, str] = {}
for _interest_profile in PRODUCT_INTEREST_PROFILES:
    _ALIAS_TO_KEY[normalize_slug(_interest_profile.key)] = _interest_profile.key
    _ALIAS_TO_KEY[normalize_slug(_interest_profile.label)] = _interest_profile.key
    for _interest_alias in _interest_profile.aliases:
        _ALIAS_TO_KEY[normalize_slug(_interest_alias)] = _interest_profile.key

_BRAND_ALIASES: dict[str, tuple[str, ...]] = {
    "ysl": ("ysl", "yves saint laurent"),
    "yves_saint_laurent": ("yves saint laurent", "ysl"),
    "rabanne": ("rabanne", "paco rabanne"),
    "paco_rabanne": ("paco rabanne", "rabanne"),
    "calvin_klein": ("calvin klein", "ck"),
    "dolce_gabbana": ("dolce gabbana", "dolce & gabbana", "d&g"),
    "giorgio_armani": ("giorgio armani", "armani"),
    "anker": ("anker", "powercore"),
}

_CATEGORY_EMOJIS: dict[str, str] = {
    "gpus": "💻", "brand_direct_electronics": "📺", "sneakers": "👟", "cpus": "💻",
    "apple": "🍎", "ram": "💾", "ssds": "💾", "premium_apparel": "👕",
    "gold_jewelry": "💍", "watches": "⌚", "fragrance_beauty": "🧴", "motor_oil": "🚗",
    "tools": "🛠️", "appliances": "🏠", "mobile_accessories": "📱", "viral_gadgets": "✨",
    "smart_home": "🏠", "office_school": "📎", "household_essentials": "🧼", "baby_kids": "👶",
    "pet_supplies": "🐾", "outdoor_sports": "🏕️", "shoes_apparel": "👟", "seasonal_holiday": "🎁",
    "walmart_cash": "💵", "home_kitchen": "🍳", "grocery_pantry": "🛒", "toys_collectibles": "🧸",
    "open_box_restored": "♻️", "health_wellness": "🩹", "business_bulk": "📦",
}


@dataclass(frozen=True)
class ProductPreferenceMatch:
    matched: bool
    reason: str = ""
    interest_key: str = ""
    interest_label: str = ""
    brand_slug: str = ""
    brand_label: str = ""


def product_interest_catalog() -> tuple[ProductInterestProfile, ...]:
    return PRODUCT_INTEREST_PROFILES


def product_interest_label(key: str) -> str:
    profile = _PROFILE_BY_KEY.get(str(key or "").strip().lower())
    return profile.label if profile else str(key or "").replace("_", " ").title()


def parse_product_interest_input(values: Iterable[str] | str | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    keys: list[str] = []
    unknown: list[str] = []
    for value in split_values(values):
        key = _ALIAS_TO_KEY.get(normalize_slug(value))
        (keys if key else unknown).append(key or value)
    return dedupe(keys)[:MAX_PRODUCT_INTERESTS], dedupe(unknown)


def normalize_product_interests(values: Iterable[str] | str | None) -> tuple[str, ...]:
    return parse_product_interest_input(values)[0]


def normalize_brands(values: Iterable[str] | str | None) -> tuple[str, ...]:
    return dedupe(normalize_slug(value) for value in split_values(values))[:MAX_BRANDS]


def brand_label(slug: str) -> str:
    key = normalize_slug(slug)
    special = {
        "ysl": "YSL",
        "yves_saint_laurent": "Yves Saint Laurent",
        "paco_rabanne": "Paco Rabanne",
        "calvin_klein": "Calvin Klein",
        "dolce_gabbana": "Dolce & Gabbana",
        "giorgio_armani": "Giorgio Armani",
    }
    return special.get(key, key.replace("_", " ").title())


def split_product_preferences(categories: Iterable[str] | str | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    interests: list[str] = []
    brands: list[str] = []
    for token in split_tokens(categories):
        if token.startswith(PRODUCT_INTEREST_PREFIX):
            key = _ALIAS_TO_KEY.get(normalize_slug(token[len(PRODUCT_INTEREST_PREFIX) :]))
            if key:
                interests.append(key)
        elif token.startswith(BRAND_PREFIX):
            slug = normalize_slug(token[len(BRAND_PREFIX) :])
            if slug:
                brands.append(slug)
    return dedupe(interests)[:MAX_PRODUCT_INTERESTS], dedupe(brands)[:MAX_BRANDS]


def product_preference_tokens(categories: Iterable[str] | str | None) -> tuple[str, ...]:
    interests, brands = split_product_preferences(categories)
    return compose_product_preference_tokens(interests, brands)


def compose_product_preference_tokens(
    interests: Iterable[str] | str | None,
    brands: Iterable[str] | str | None,
) -> tuple[str, ...]:
    return (
        *(f"{PRODUCT_INTEREST_PREFIX}{key}" for key in normalize_product_interests(interests)),
        *(f"{BRAND_PREFIX}{slug}" for slug in normalize_brands(brands)),
    )


def update_product_preferences(
    categories: Iterable[str] | str | None,
    *,
    add_interests: Iterable[str] | str | None = None,
    remove_interests: Iterable[str] | str | None = None,
    replace_interests: Iterable[str] | str | None = None,
    add_brands: Iterable[str] | str | None = None,
    remove_brands: Iterable[str] | str | None = None,
    replace_brands: Iterable[str] | str | None = None,
) -> tuple[str, ...]:
    tokens = list(split_tokens(categories))
    non_product = [
        token for token in tokens
        if not token.startswith(PRODUCT_INTEREST_PREFIX) and not token.startswith(BRAND_PREFIX)
    ]
    current_interests, current_brands = split_product_preferences(tokens)

    if replace_interests is not None:
        interests = list(normalize_product_interests(replace_interests))
    else:
        interests = list(current_interests)
        interests.extend(normalize_product_interests(add_interests))
        remove_set = set(normalize_product_interests(remove_interests))
        interests = [key for key in dedupe(interests) if key not in remove_set]

    if replace_brands is not None:
        brands = list(normalize_brands(replace_brands))
    else:
        brands = list(current_brands)
        brands.extend(normalize_brands(add_brands))
        remove_set = set(normalize_brands(remove_brands))
        brands = [slug for slug in dedupe(brands) if slug not in remove_set]

    return dedupe((*non_product, *compose_product_preference_tokens(interests, brands)))


def match_product_preferences(
    search_text: str,
    *,
    interests: Iterable[str] | str | None = None,
    brands: Iterable[str] | str | None = None,
) -> ProductPreferenceMatch:
    interest_keys = normalize_product_interests(interests)
    brand_slugs = normalize_brands(brands)
    normalized = normalize_match_text(search_text)

    matched_profile: ProductInterestProfile | None = None
    if interest_keys:
        matched_profile = next(
            (
                _PROFILE_BY_KEY[key]
                for key in interest_keys
                if key in _PROFILE_BY_KEY and _PROFILE_BY_KEY[key].matches(normalized)
            ),
            None,
        )
        if matched_profile is None:
            return ProductPreferenceMatch(False, "none of your specific product interests matched")

    matched_brand = ""
    if brand_slugs:
        for slug in brand_slugs:
            phrases = _BRAND_ALIASES.get(slug, (slug.replace("_", " "),))
            if any(phrase_in_text(normalized, phrase) for phrase in phrases):
                matched_brand = slug
                break
        if not matched_brand:
            return ProductPreferenceMatch(
                False,
                "none of your selected brands matched",
                interest_key=matched_profile.key if matched_profile else "",
                interest_label=matched_profile.label if matched_profile else "",
            )

    pieces: list[str] = []
    if matched_profile:
        pieces.append(f"interest {matched_profile.label}")
    if matched_brand:
        pieces.append(f"brand {brand_label(matched_brand)}")
    return ProductPreferenceMatch(
        True,
        " • ".join(pieces),
        interest_key=matched_profile.key if matched_profile else "",
        interest_label=matched_profile.label if matched_profile else "",
        brand_slug=matched_brand,
        brand_label=brand_label(matched_brand) if matched_brand else "",
    )


def product_emoji_for_text(search_text: str) -> str:
    normalized = normalize_match_text(search_text)
    for profile in PRODUCT_INTEREST_PROFILES:
        if profile.matches(normalized):
            return profile.emoji
    category = category_for_title(normalized)
    return _CATEGORY_EMOJIS.get(category.key, "📦") if category else "📦"


def product_badge_text(search_text: str, *, heat_emoji: str) -> str:
    badges: list[str] = []
    for emoji in (str(heat_emoji or "").strip(), product_emoji_for_text(search_text)):
        if emoji and emoji not in badges:
            badges.append(emoji)
    return " ".join(badges[:2])


def product_interest_catalog_lines(limit: int | None = None) -> tuple[str, ...]:
    profiles = (
        PRODUCT_INTEREST_PROFILES
        if limit is None
        else PRODUCT_INTEREST_PROFILES[: max(0, int(limit))]
    )
    return tuple(
        f"{profile.emoji} **{profile.label}** — `{profile.key}`"
        for profile in profiles
    )
