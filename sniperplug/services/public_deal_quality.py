from __future__ import annotations

import hashlib
import json
import re
from typing import Any

import discord


PUBLIC_DEAL_LANE_FIELD = "✅ Public deal lane"
PUBLIC_SCOUT_LANE_FIELD = "🧪 Public scout lane"

PUBLIC_SCOUT_LANE_ENABLED_REASON = "Public Scout Lane is enabled for high-confidence review leads"

LANE_VERIFIED_MARKDOWN = "verified_markdown"
LANE_PRICE_MEMORY_DROP = "price_memory_drop"
LANE_OPEN_BOX_LIKE_NEW = "open_box_like_new"
LANE_RESTORED_REFURBISHED = "restored_refurbished"
LANE_WALMART_CASH = "walmart_cash"
LANE_CART_PROMO = "cart_promo"
LANE_ONEPAY = "onepay"
LANE_CLEARANCE = "clearance"
LANE_ROLLBACK = "rollback"
LANE_PUBLIC_SCOUT = "public_scout"

GLOBAL_EXACT_OFFER_REFERENCE_SOURCE = "sniperplug.global_exact_offer_memory.stable_price"
GLOBAL_EXACT_OFFER_IDENTITY_PREFIX = "walmart-offer:v1:"
GLOBAL_EXACT_OFFER_IDENTITY_VERSION = "v1"
MIN_GLOBAL_EXACT_OFFER_CONFIRMATIONS = 2

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
PUBLIC_SCOUT_VALUE_TERMS = (
    "walmart cash",
    "coupon from api",
    "walmart api savings",
    "walmart api promo",
    "api promo cap",
    "rough spread",
    "flip/value lead",
    "profit",
    "margin",
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


def has_global_exact_offer_memory_proof(card: Any) -> bool:
    """Require a complete, internally consistent exact-offer memory proof.

    A legacy identity string or trusted marker alone is deliberately rejected.
    The public gate recomputes the fingerprint from the stored proof components,
    checks the exact-detail item against the Walmart URL, and binds the selected
    offer and stable reference source before allowing an observed-price post.
    """

    attrs = variant_attrs(card)
    identity = str(attrs.get("priceMemoryIdentity") or "").strip()
    item_id = str(attrs.get("priceMemoryItemId") or "").strip()
    offer_id = str(attrs.get("priceMemoryOfferId") or "").strip()
    seller_key = str(attrs.get("priceMemorySellerKey") or "").strip()
    variant_key = str(attrs.get("priceMemoryVariantKey") or "").strip()
    condition_key = str(attrs.get("priceMemoryConditionKey") or "").strip()
    fulfillment_key = str(attrs.get("priceMemoryFulfillmentKey") or "").strip()
    exact_item_id = str(attrs.get("exactDetailItemId") or "").strip()
    exact_detail_verified = str(attrs.get("exactDetailPriceProof") or "").strip().lower() == "yes"
    trusted = str(attrs.get("referencePriceTrusted") or "").strip().lower() == "yes"
    trusted_source = str(attrs.get("trustedReferenceSource") or "").strip()
    api_source = str(attr_value(card, "api_reference_path", "apiReferencePath") or "").strip()

    try:
        confirmations = int(str(attrs.get("priceMemoryStableConfirmations") or "0").strip())
    except (TypeError, ValueError):
        confirmations = 0

    if not identity.startswith(GLOBAL_EXACT_OFFER_IDENTITY_PREFIX):
        return False
    digest = identity[len(GLOBAL_EXACT_OFFER_IDENTITY_PREFIX) :]
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        return False
    if not item_id.isdigit() or exact_item_id != item_id or not exact_detail_verified:
        return False
    if not offer_id or not seller_key or not condition_key or not fulfillment_key:
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", variant_key):
        return False
    if confirmations < MIN_GLOBAL_EXACT_OFFER_CONFIRMATIONS:
        return False
    if not trusted or trusted_source != GLOBAL_EXACT_OFFER_REFERENCE_SOURCE:
        return False
    if api_source != GLOBAL_EXACT_OFFER_REFERENCE_SOURCE:
        return False

    selected_offer = str(attr_value(card, "selected_offer_id") or "").strip()
    if not selected_offer or selected_offer != offer_id:
        return False

    url_item_id = walmart_item_id_from_url(direct_product_url(card))
    if url_item_id != item_id:
        return False

    trusted_reference = float_or_none(attrs.get("trustedReferencePrice"))
    current = current_price(card)
    reference = reference_price(card)
    if current is None or reference is None or reference <= current:
        return False
    if trusted_reference is None or abs(trusted_reference - reference) > 0.001:
        return False

    payload = {
        "version": GLOBAL_EXACT_OFFER_IDENTITY_VERSION,
        "item_id": item_id,
        "offer_id": offer_id,
        "seller_key": seller_key,
        "variant_key": variant_key,
        "condition_key": condition_key,
        "fulfillment_key": fulfillment_key,
    }
    expected_digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return digest == expected_digest


def walmart_item_id_from_url(url: Any) -> str:
    match = re.search(r"/ip/(?:[^/?#]+/)?(\d+)", str(url or ""))
    return match.group(1) if match else ""


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
        return has_global_exact_offer_memory_proof(card)
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
    labels = {LANE_VERIFIED_MARKDOWN: "Verified Markdown", LANE_PRICE_MEMORY_DROP: "Observed Price Drop", LANE_OPEN_BOX_LIKE_NEW: "Open Box / Like New", LANE_RESTORED_REFURBISHED: "Restored / Refurbished", LANE_CLEARANCE: "Clearance Markdown", LANE_ROLLBACK: "Rollback Markdown", LANE_PUBLIC_SCOUT: "Public Scout"}
    return labels.get(lane, lane.replace("_", " ").title())


def existing_score(card: Any) -> int:
    try:
        return int(float(getattr(card, "score", 0) or 0))
    except (TypeError, ValueError):
        return 0


def public_scout_signal_score(card: Any, *, source_label: str = "") -> int:
    """Score review cards using their embedded hard-value proof.

    Review candidate cards are usually created with score=0, so the public scout
    gate must derive a score from API promo/coupon/cash or comp-profit fields.
    Search-route match text alone deliberately earns only a small bonus.
    """

    text = card_text(card, source_label=source_label).lower()
    score = existing_score(card)
    if has_real_price(card):
        score += 10
    if direct_product_url(card):
        score += 10
    discount = structured_discount(card) or 0.0
    if discount >= 40:
        score += 35
    elif discount >= 25:
        score += 20

    if "coupon from api" in text:
        score += 80
    if "walmart cash" in text:
        score += 80
    if "walmart api savings" in text or "walmart api promo" in text or "api promo cap" in text:
        score += 80
    if "rough spread" in text or "flip/value lead" in text or "profit" in text or "margin" in text:
        score += 85

    if "search route match" in text or "direct search match" in text or "exact product match" in text:
        score += 8
    if "stock: **available" in text or "available online" in text:
        score += 5
    if "rollback" in text:
        score += 5
    if "clearance" in text:
        score += 5

    if has_low_trust_reference(card, source_label=source_label) and not any(term in text for term in ("coupon from api", "walmart cash", "walmart api savings", "walmart api promo", "api promo cap", "rough spread", "profit", "margin")):
        score -= 50
    if any(term in text for term in ("out of stock", "sold out", "not available online")):
        score -= 60
    return max(0, min(150, int(score)))


def has_public_scout_value_signal(card: Any, *, source_label: str = "") -> bool:
    text = card_text(card, source_label=source_label).lower()
    return any(term in text for term in PUBLIC_SCOUT_VALUE_TERMS)


def is_public_scout_candidate(card: Any, *, source_label: str = "", min_score: int = 95) -> bool:
    text = card_text(card, source_label=source_label).lower()
    if not has_real_price(card):
        return False
    if not direct_product_url(card):
        return False
    if any(term in text for term in ("out of stock", "sold out", "not available online")):
        return False
    if not has_public_scout_value_signal(card, source_label=source_label):
        return False
    if has_low_trust_reference(card, source_label=source_label) and not any(term in text for term in ("coupon from api", "walmart cash", "walmart api savings", "walmart api promo", "api promo cap", "rough spread", "profit", "margin")):
        return False
    return public_scout_signal_score(card, source_label=source_label) >= max(1, int(min_score))


def prepare_public_scout_candidate(card: Any, *, source_label: str = "", min_score: int = 95) -> bool:
    if not is_public_scout_candidate(card, source_label=source_label, min_score=min_score):
        return False
    score = public_scout_signal_score(card, source_label=source_label)
    setattr(card, "score", max(existing_score(card), score))
    setattr(card, "should_alert", True)
    setattr(card, "deal_lane", LANE_PUBLIC_SCOUT)
    setattr(card, "direct_product_url", direct_product_url(card))
    embed = getattr(card, "embed", None)
    if isinstance(embed, discord.Embed) and not any(str(field.name or "") == PUBLIC_SCOUT_LANE_FIELD for field in embed.fields):
        embed.add_field(
            name=PUBLIC_SCOUT_LANE_FIELD,
            value=(
                f"Posted as **Public Scout**, not Verified Markdown. Scout score: **{score}/150**. SniperPlug found a hard value signal, but this did **not** pass the trusted Walmart markdown gate. Recheck price, seller, selected option, stock, and comps before buying."
            ),
            inline=False,
        )
    return True


def select_public_deal_candidates(cards: list[Any], *, source_label: str = "", min_discount: int = 50, limit: int = 5) -> list[Any]:
    selected: list[Any] = []
    for card in cards:
        if prepare_public_deal_candidate(card, source_label=source_label, min_discount=min_discount):
            selected.append(card)
        if len(selected) >= max(1, int(limit)):
            break
    return selected
