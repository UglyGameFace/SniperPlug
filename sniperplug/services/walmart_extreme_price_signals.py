from __future__ import annotations

"""Source-informed Walmart bargain triage without a closed SKU/model whitelist.

This is *not* proof of a markdown: it only decides which unusually cheap items
merit timely exact-offer verification and opt-in lead delivery. Proof and seller
checks remain at the authoritative exact-detail/posting boundary.
"""

import re
from typing import Any

# Product value must not be inferred from a case or replacement part bearing
# the name of an expensive device. Apply these exclusions before any positive
# Walmart category, product type, or trusted reference signal.
ACCESSORY = re.compile(
    r"\b(?:case|cover|protector|charger|cable|adapter|replacement|parts? only"
    r"|box only|mount|holder|remote control|display dummy|sticker|decal|"
    r"screen protector|refill|repair part|protective sleeve)\b",
    re.IGNORECASE,
)

# Walmart categoryPath is a retailer-origin product taxonomy, not a model-name
# allowlist. That permits new brands and product generations automatically.
VALUABLE_DEPARTMENTS = (
    "electronics", "computers", "tablet", "cell phones", "video games",
    "televisions", "home theater", "appliances", "furniture", "power tools",
    "jewelry", "cameras", "sporting goods", "fitness equipment",
    "smart home", "audio equipment",
)
ACCESSORY_DEPARTMENTS = (
    "accessories", "cases", "chargers", "cables", "replacement parts",
    "screen protectors", "mounts", "components",
)
# General product nouns allow new brands and models without curated SKU names.
DURABLE_PRODUCT_TYPE = re.compile(
    r"\b(?:tablet|smartphone|cell\s*phone|mobile\s*phone|laptop|notebook|"
    r"desktop\s*(?:computer|pc)|computer|television|oled\s*tv|qled\s*tv|"
    r"monitor|gaming\s*console|game\s*console|handheld\s*console|"
    r"graphics\s*card|gpu|camera|projector|drone|robot\s*vacuum|"
    r"refrigerator|dishwasher|washing\s*machine|dryer|power\s*tool|"
    r"electric\s*bike|treadmill|smartwatch|headphones|earbuds|"
    r"coffee\s*maker|printer|office\s*chair|mattress|"
    r"controllers?|gamepads?|joy[-\s]?cons?)\b",
    re.IGNORECASE,
)
# Compatibility fallback for earlier exact snapshots lacking source category;
# no fixed model numbers, and new category/type matches do not need this.
DEVICE_FAMILY = re.compile(
    r"\b(?:galaxy\s+(?:tab|s\d+|z\s*(?:fold|flip)|a\d+)|"
    r"iphone|ipad|pixel\s*\d+|macbook|playstation\s*\d+|"
    r"xbox\s+series|nintendo\s+switch)\b",
    re.IGNORECASE,
)


def primary_product_evidence(candidate: Any, *, trusted_reference: float | None = None) -> str | None:
    """Return why a low-priced item may be substantial, or None for weak signals.

    This does not claim an MSRP or independently establish retail value. For
    unrecognized products with no retailer category or trustworthy baseline,
    keep the offer private rather than guessing a public windfall.
    """
    if candidate is None:
        return None
    title = " ".join(
        str(value or "") for value in (
            getattr(candidate, "title", ""),
            getattr(candidate, "parent_title", ""),
        )
    ).strip()
    if not title or ACCESSORY.search(title):
        return None
    attrs = getattr(candidate, "variant_attributes", None) or {}
    category = str(attrs.get("category") or attrs.get("categoryPath") or "").lower()
    # A parent such as "Cell Phones & Accessories" may contain the real
    # "Cell Phones" product leaf. Reject accessory *leaves*, not the entire
    # ancestry, or we silently discard new phone brands again.
    category_leaf = re.split(r"[/|>]", category)[-1].strip()
    if category and any(dept in category for dept in VALUABLE_DEPARTMENTS):
        if not any(bad in category_leaf for bad in ACCESSORY_DEPARTMENTS):
            return "walmart_category"
    if DURABLE_PRODUCT_TYPE.search(title):
        return "product_type"
    if DEVICE_FAMILY.search(title):
        return "device_family"
    if trusted_reference is not None:
        try:
            if float(trusted_reference) >= 50:
                return "trusted_reference"
        except (TypeError, ValueError, OverflowError):
            pass
    return None


# Price errors are relative, not absolute: a $600 TV at $49 is a glitch even
# though it is far above $10. A price at or below this fraction of a trusted
# reference counts as a price-error-level anomaly at any price point.
PRICE_ERROR_MAX_RATIO = 0.20
PRICE_ERROR_MIN_REFERENCE = 50.0
ABSOLUTE_EXTREME_MAX_PRICE = 10.0


def is_price_error_ratio(
    current_price: Any, reference_price: Any
) -> bool:
    """True when price is <= PRICE_ERROR_MAX_RATIO of a substantial reference."""
    try:
        price = float(current_price)
        reference = float(reference_price)
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        price > 0
        and reference >= PRICE_ERROR_MIN_REFERENCE
        and price <= reference * PRICE_ERROR_MAX_RATIO
    )


def is_trusted_price_error(
    candidate: Any, *, current_price: Any, trusted_reference: Any
) -> bool:
    """Ratio anomaly backed by an exact-detail validated reference price."""
    attrs = getattr(candidate, "variant_attributes", None) or {}
    trusted_flag = str(attrs.get("referencePriceTrusted") or "").strip().lower()
    return trusted_flag in {"yes", "true", "1"} and is_price_error_ratio(
        current_price, trusted_reference
    )


def is_priority_extreme_discovery(
    candidate: Any, *, current_price: float | None, trusted_reference: float | None
) -> bool:
    """Boost potentially valuable extreme leads in the shared queue.

    Two shapes qualify: a recognized primary product at <=$10, or any
    non-accessory item at <=20% of a substantial reference price (a likely
    price error at any price point). A search result is never treated as a
    verified offer: this only changes ordering inside existing bounded
    queues; exact detail still must confirm it.
    """
    try:
        price = float(current_price) if current_price is not None else None
    except (TypeError, ValueError, OverflowError):
        return False
    if price is None or price <= 0:
        return False
    if price <= ABSOLUTE_EXTREME_MAX_PRICE:
        return primary_product_evidence(
            candidate, trusted_reference=trusted_reference
        ) is not None
    if not is_price_error_ratio(price, trusted_reference):
        return False
    title = " ".join(
        str(getattr(candidate, key, "") or "") for key in ("title", "parent_title")
    )
    return bool(title.strip()) and ACCESSORY.search(title) is None


# Discovery deliberately stays broad, but a public "extreme" alert needs stronger
# value evidence than a $10 item belonging to Electronics, Jewelry or Video Games.
# These are product *types*, not an allowlist of brands or model/SKU numbers.
PUBLIC_HIGH_VALUE_HARDWARE = re.compile(
    r"\b(?:tablet(?:\s+computer)?|smartphone|cell\s*phone|mobile\s*phone|"
    r"laptop|notebook\s*(?:computer|pc)|desktop\s*(?:computer|pc)|"
    r"all[-\s]?in[-\s]?one\s*(?:computer|pc)|television|oled\s*tv|qled\s*tv|"
    r"monitor|(?:video\s*game\s*|game\s*|gaming\s*|handheld\s*)?consoles?|"
    r"controllers?|gamepads?|joy[-\s]?cons?|graphics\s*card|gpu|projector|drone|"
    r"robot\s*vacuum|refrigerator|"
    r"dishwasher|washing\s*machine|tumble\s*dryer|power\s*tool|"
    r"electric\s*bike|treadmill)\b",
    re.IGNORECASE,
)
PUBLIC_HIGH_VALUE_CATEGORY_LEAF = re.compile(
    r"^(?:tablets?|tablet\s*computers?|cell\s*phones?|smartphones?|"
    r"mobile\s*phones?|laptops?|laptop\s*computers?|notebooks?|"
    r"desktop\s*(?:computers?|pcs?)|computer\s*monitors?|monitors?|"
    r"televisions?|tvs?|consoles?|game\s*consoles?|gaming\s*consoles?|"
    r"video\s*game\s*consoles?|game\s*controllers?|gaming\s*controllers?|"
    r"gamepads?|joy[-\s]?cons?|graphics\s*cards?|projectors?|"
    r"robot\s*vacuums?|refrigerators?|dishwashers?|washing\s*machines?|"
    r"treadmills?|electric\s*bikes?)$",
    re.IGNORECASE,
)
# Product-family names in software and accessories do not make those items
# consoles. These exclusions apply to title, parent title and source type.
NON_HARDWARE_PUBLIC_LEADS = re.compile(
    r"\b(?:gift\s*cards?|gift\s*certificates?|e[-\s]?gift|"
    r"v\s*g\s*c|subscriptions?|memberships?|prepaid|"
    r"digital\s*(?:code|credit|delivery|download|game|gift|item)|"
    r"download\s*code|code[-\s]?in[-\s]?box|game\s*code|"
    r"in[-\s]?game\s*(?:currency|credit|coins?|points?)|"
    r"virtual\s*(?:currency|credit|items?)|"
    r"software\s*(?:license|key)|online\s*(?:service|code)|"
    r"(?:video\s*)?games?\s*(?:for|on)\s*(?:nintendo|playstation|xbox)|"
    r"video\s*game(?!\s*(?:console|controller|gamepad))|"
    r"game\s*cartridge|game\s*disc|game\s*software|"
    r"charging\s*(?:dock|stand)|controller\s*(?:stand|grip|skin|shell)|"
    r"(?:thumbstick|joystick)\s*(?:caps?|covers?|grips?))\b",
    re.IGNORECASE,
)

def public_extreme_value_evidence(
    candidate: Any, *, trusted_reference: float | None = None
) -> str | None:
    """Require plausible exceptional value before an exact-priced public post.

    A generic retailer department is not an item-level price anomaly. Unverified
    reference prices must not be used to rescue ordinary $10 goods. Discovery
    may still inspect these items privately using primary_product_evidence.
    """
    if candidate is None:
        return None
    title = " ".join(
        str(value or "") for value in (
            getattr(candidate, "title", ""),
            getattr(candidate, "parent_title", ""),
        )
    ).strip()
    attrs = getattr(candidate, "variant_attributes", None) or {}
    source_type = " ".join(
        str(attrs.get(k) or "") for k in ("productType", "product_type")
    )
    fulfillment = str(getattr(candidate, "fulfillment_type", "") or "")
    if (
        not title
        or ACCESSORY.search(title)
        or NON_HARDWARE_PUBLIC_LEADS.search(" ".join((title, source_type, fulfillment)))
    ):
        return None

    # Search metadata can be a whole department ("Video Games" or "Jewelry").
    # Only a specific, substantial hardware leaf can establish value.
    category = str(attrs.get("categoryPath") or attrs.get("category") or "").strip()
    leaf = re.split(r"[/|>]", category)[-1].strip()
    if (
        PUBLIC_HIGH_VALUE_HARDWARE.search(title)
        or PUBLIC_HIGH_VALUE_HARDWARE.search(source_type)
        or PUBLIC_HIGH_VALUE_CATEGORY_LEAF.fullmatch(leaf)
    ):
        return "high_value_hardware"

    # Mentions of "Nintendo Switch" or "PlayStation 5" in software titles
    # do not establish that the item is the actual console. Console listings
    # must have an explicit hardware type or precise retailer category.
    console_family = re.search(
        r"\b(?:nintendo\s*switch|playstation\s*\d+|xbox\s+series)\b",
        title,
        re.IGNORECASE,
    )
    if DEVICE_FAMILY.search(title) and console_family is None:
        return "high_value_hardware"

    # Exact-detail validated reference evidence is a separate path for
    # genuinely unusual items outside hardware, without inventing an MSRP.
    if is_trusted_price_error(
        candidate,
        current_price=getattr(candidate, "api_current_price", None),
        trusted_reference=trusted_reference,
    ):
        return "trusted_reference_price_anomaly"
    return None
