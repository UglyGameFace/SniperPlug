from __future__ import annotations

from typing import Any

import discord


PUBLIC_DEAL_LANE_FIELD = "✅ Public deal lane"
PUBLIC_SCOUT_LANE_FIELD = "🧪 Private scout/review lane"

# Regression guard copy: Public Scout Lane is intentionally disabled.
# Review/scout/value leads can be shown privately, but they must not public-post
# unless they pass the verified public deal gate below.
PUBLIC_SCOUT_LANE_DISABLED_REASON = "Public Scout Lane is intentionally disabled"

LANE_VERIFIED_MARKDOWN = "verified_markdown"
LANE_PRICE_MEMORY_DROP = "price_memory_drop"
LANE_OPEN_BOX_LIKE_NEW = "open_box_like_new"
LANE_RESTORED_REFURBISHED = "restored_refurbished"
LANE_WALMART_CASH = "walmart_cash"
LANE_CART_PROMO = "cart_promo"
LANE_ONEPAY = "onepay"
LANE_CLEARANCE = "clearance"
LANE_ROLLBACK = "rollback"

PUBLIC_PRICE_LANES = {
    LANE_VERIFIED_MARKDOWN,
    LANE_PRICE_MEMORY_DROP,
    LANE_OPEN_BOX_LIKE_NEW,
    LANE_RESTORED_REFURBISHED,
}

PRIVATE_PROMO_LANES = {
    LANE_WALMART_CASH,
    LANE_CART_PROMO,
    LANE_ONEPAY,
}

OPEN_BOX_CONDITION_TERMS = ("open box", "open-box", "like new", "like-new", "new other")
RESTORED_CONDITION_TERMS = ("restored", "refurbished", "pre-owned", "pre owned", "preowned", "renewed")
DISPLAY_REFERENCE_TERMS = ("msrp", "original price", "reference price", "list price")
LOW_TRUST_REFERENCE_TERMS = (
    "ignored reference",
    "ignored suspicious",
    "ignored low-confidence",
    "weak reference",
    "weak/ignored reference",
    "low-trust/suspicious",
    "blocked as low-trust",
    "reference match: blocked",
)


def card_text(card: Any, *, source_label: str = "") -> str:
    parts: list[str] = [str(source_label or ""), str(getattr(card, "label", "") or ""), str(getattr(card, "url", "") or "")]
    embed = getattr(card, "embed", None)
    if embed is not None:
        parts.append(str(getattr(embed, "title", "") or ""))
        parts.append(str(getattr(embed, "description", "") or ""))
        for field in getattr(embed, "fields", []) or []:
            parts.append(str(getattr(field, "name", "") or ""))
            parts.append(str(getattr(field, "value", "") or ""))
    return " ".join(parts)


def float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        value = value.replace("$", "").replace(",", "").strip().rstrip("%")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_review_or_watchlist(card: Any, *, source_label: str = "") -> bool:
    text = card_text(card, source_label=source_label).lower()
    blocked_terms = (
        "watchlist",
        "review-only",
        "review only",
        "private only",
        "staff review",
        "scout lead",
        "public scout lane",
        "private scout",
        "not verified",
        "not a verified",
        "not blind-buy",
        "not deal proof",
        "shown even without walmart markdown proof",
    )
    return any(term in text for term in blocked_terms)


def variant_attrs(card: Any) -> dict[str, Any]:
    attrs = getattr(card, "variant_attributes", None)
    if isinstance(attrs, dict):
        return attrs
    candidate = getattr(card, "candidate", None)
    attrs = getattr(candidate, "variant_attributes", None)
    if isinstance(attrs, dict):
        return attrs
    deal = getattr(card, "deal", None)
    attrs = getattr(deal, "variant_attributes", None)
    if isinstance(attrs, dict):
        return attrs
    return {}


def attr_value(card: Any, *names: str) -> Any:
    for name in names:
        value = getattr(card, name, None)
        if value not in (None, ""):
            return value
    attrs = variant_attrs(card)
    for name in names:
        for key in (name, camel_name(name)):
            value = attrs.get(key)
            if value not in (None, ""):
                return value
    return None


def camel_name(name: str) -> str:
    parts = name.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def normalized_lane(card: Any) -> str:
    explicit = str(attr_value(card, "deal_lane", "dealLane") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if explicit:
        return explicit
    condition = normalized_condition(attr_value(card, "api_condition", "condition"))
    if is_open_box_condition(condition):
        return LANE_OPEN_BOX_LIKE_NEW
    if is_restored_condition(condition):
        return LANE_RESTORED_REFURBISHED
    attrs = variant_attrs(card)
    if attrs.get("priceMemoryIdentity") or attrs.get("priceMemoryReason"):
        return LANE_PRICE_MEMORY_DROP
    if attrs.get("walmartCashSavings") or attrs.get("walmartCashAmount") or attrs.get("walmartCashReward"):
        return LANE_WALMART_CASH
    if attrs.get("onePayCashback") or attrs.get("onepayCashback"):
        return LANE_ONEPAY
    if attrs.get("cartPromo") or attrs.get("apiPromotionText") or attrs.get("apiPromotionSavingsCap"):
        return LANE_CART_PROMO
    if str(attrs.get("clearance") or "").lower() == "yes":
        return LANE_CLEARANCE
    if str(attrs.get("rollback") or "").lower() == "yes":
        return LANE_ROLLBACK
    return LANE_VERIFIED_MARKDOWN


def normalized_condition(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").replace("-", " ").lower().split())


def is_open_box_condition(condition: str) -> bool:
    text = normalized_condition(condition)
    return any(term.replace("-", " ") in text for term in OPEN_BOX_CONDITION_TERMS)


def is_restored_condition(condition: str) -> bool:
    text = normalized_condition(condition)
    return any(term in text for term in RESTORED_CONDITION_TERMS)


def has_low_trust_reference(card: Any, *, source_label: str = "") -> bool:
    if normalized_lane(card) == LANE_PRICE_MEMORY_DROP:
        return False
    attrs = variant_attrs(card)
    trusted = str(attrs.get("referencePriceTrusted") or "").strip().lower()
    if trusted == "no":
        return True
    text = card_text(card, source_label=source_label).lower()
    return any(term in text for term in LOW_TRUST_REFERENCE_TERMS)


def has_real_price(card: Any) -> bool:
    return current_price(card) is not None


def current_price(card: Any) -> float | None:
    return float_or_none(attr_value(card, "api_current_price", "current_price", "apiCurrentPrice"))


def reference_price(card: Any) -> float | None:
    return float_or_none(attr_value(card, "api_reference_price", "typical_price", "apiReferencePrice", "trustedReferencePrice"))


def structured_discount(card: Any) -> float | None:
    explicit = float_or_none(attr_value(card, "api_discount_percent", "apiDiscountPercent"))
    if explicit is not None:
        return explicit
    current = current_price(card)
    reference = reference_price(card)
    if current is not None and reference is not None and reference > 0 and reference > current:
        return (reference - current) / reference * 100
    return float_or_none(getattr(card, "discount", None))


def direct_product_url(card: Any) -> str:
    url = str(attr_value(card, "direct_product_url", "directProductUrl") or getattr(card, "url", "") or "").strip()
    if not url:
        return ""
    lowered = url.lower()
    if "walmart.com/ip/" not in lowered and "walmart.com/" not in lowered:
        return ""
    return url


def has_structured_reference_proof(card: Any) -> bool:
    if reference_price(card) is not None:
        return True
    reference_path = attr_value(card, "api_reference_path", "apiReferencePath", "trustedReferenceSource")
    return bool(reference_path)


def display_reference_without_proof(card: Any, *, source_label: str = "") -> bool:
    if has_structured_reference_proof(card):
        return False
    text = card_text(card, source_label=source_label).lower()
    return any(term in text for term in DISPLAY_REFERENCE_TERMS)


def has_verified_api_threshold_discount(card: Any, *, source_label: str = "", min_discount: int = 50) -> bool:
    if is_review_or_watchlist(card, source_label=source_label):
        return False
    if has_low_trust_reference(card, source_label=source_label):
        return False
    if display_reference_without_proof(card, source_label=source_label):
        return False
    if not has_real_price(card):
        return False
    if not direct_product_url(card):
        return False
    lane = normalized_lane(card)
    if lane in PRIVATE_PROMO_LANES:
        return False
    discount = structured_discount(card) or 0.0
    if discount < max(1, int(min_discount)):
        return False
    if lane == LANE_PRICE_MEMORY_DROP:
        attrs = variant_attrs(card)
        return bool(attrs.get("priceMemoryIdentity")) and str(attrs.get("referencePriceTrusted") or "").lower() == "yes"
    if lane in {LANE_OPEN_BOX_LIKE_NEW, LANE_RESTORED_REFURBISHED}:
        condition = normalized_condition(attr_value(card, "api_condition", "condition"))
        if not condition or not has_structured_reference_proof(card):
            return False
        if lane == LANE_OPEN_BOX_LIKE_NEW and not is_open_box_condition(condition):
            return False
        if lane == LANE_RESTORED_REFURBISHED and not is_restored_condition(condition):
            return False
    return lane in PUBLIC_PRICE_LANES or lane in {LANE_CLEARANCE, LANE_ROLLBACK}


def is_public_deal_candidate(card: Any, *, source_label: str = "", min_discount: int = 50) -> bool:
    return has_verified_api_threshold_discount(card, source_label=source_label, min_discount=min_discount)


def prepare_public_deal_candidate(card: Any, *, source_label: str = "", min_discount: int = 50) -> bool:
    if not is_public_deal_candidate(card, source_label=source_label, min_discount=min_discount):
        return False
    discount = structured_discount(card) or 0.0
    lane = normalized_lane(card)
    setattr(card, "should_alert", True)
    setattr(card, "deal_lane", lane)
    setattr(card, "api_current_price", current_price(card))
    setattr(card, "api_reference_price", reference_price(card))
    setattr(card, "api_discount_percent", discount)
    setattr(card, "direct_product_url", direct_product_url(card))
    embed = getattr(card, "embed", None)
    if isinstance(embed, discord.Embed) and not any(str(field.name or "") == PUBLIC_DEAL_LANE_FIELD for field in embed.fields):
        lane_label = public_lane_label(lane)
        proof_copy = "SniperPlug observed price memory" if lane == LANE_PRICE_MEMORY_DROP else "Walmart/API structured math"
        embed.add_field(name=PUBLIC_DEAL_LANE_FIELD, value=(f"Posted as **{lane_label}** because {proof_copy} is **{discount:.0f}%**, meeting this server's **{int(min_discount)}%+** public deal threshold. Walmart Cash, OnePay, cart promos, scout leads, marketplace comps, and display-only MSRP text did not bypass this gate."), inline=False)
    return True


def public_lane_label(lane: str) -> str:
    labels = {LANE_VERIFIED_MARKDOWN: "Verified Markdown", LANE_PRICE_MEMORY_DROP: "Observed Price Drop", LANE_OPEN_BOX_LIKE_NEW: "Open Box / Like New", LANE_RESTORED_REFURBISHED: "Restored / Refurbished", LANE_CLEARANCE: "Clearance Markdown", LANE_ROLLBACK: "Rollback Markdown"}
    return labels.get(lane, lane.replace("_", " ").title())


def is_public_scout_candidate(card: Any, *, source_label: str = "", min_score: int = 95) -> bool:
    # Public Scout Lane is intentionally disabled.
    return False


def prepare_public_scout_candidate(card: Any, *, source_label: str = "", min_score: int = 95) -> bool:
    # Public Scout Lane is intentionally disabled.
    return False


def select_public_deal_candidates(cards: list[Any], *, source_label: str = "", min_discount: int = 50, limit: int = 5) -> list[Any]:
    selected: list[Any] = []
    for card in cards:
        if prepare_public_deal_candidate(card, source_label=source_label, min_discount=min_discount):
            selected.append(card)
        if len(selected) >= max(1, int(limit)):
            break
    return selected
