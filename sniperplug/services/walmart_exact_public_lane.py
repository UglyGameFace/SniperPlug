from __future__ import annotations

from typing import Any, Iterable

import discord

from sniperplug.services import public_deal_quality as quality


WALMART_CASH_FIELD = "💵 Walmart Cash"

_STALE_REVIEW_MARKERS = (
    "review/scout-only",
    "review / scout only",
    "review-only",
    "review only",
    "scout-only",
    "scout only",
    "staff review",
    "scout lead",
    "public scout lane",
    "private scout",
    "not verified",
    "not a verified",
    "not deal proof",
    "shown even without walmart markdown proof",
)


def normalize_exact_verified_walmart_cards(
    cards: Iterable[Any],
    *,
    min_discount: int,
) -> int:
    """Refresh exact Walmart cards before every public-quality boundary.

    This operation is intentionally idempotent. Public proof fields added by an
    earlier gate must not poison a later gate merely because their explanation
    mentions rejected scout/review signals. Exact Walmart Cash proof is also
    rendered on the final card with its numeric amount.
    """

    normalized = 0
    for card in cards:
        normalized += int(
            normalize_exact_verified_walmart_card(
                card,
                min_discount=min_discount,
            )
        )
    return normalized


def normalize_exact_verified_walmart_card(
    card: Any,
    *,
    min_discount: int,
) -> bool:
    """Promote a fail-closed exact markdown while preserving auxiliary promos."""

    attrs = quality.variant_attrs(card)
    if not attrs:
        return False

    if _normalized(attrs.get("exactDetailPriceProof")) != "yes":
        return False
    if _normalized(attrs.get("referencePriceTrusted")) != "yes":
        return False
    if _normalized(attrs.get("exactDetailOfferIdentityStatus")) in {
        "blocked",
        "missing",
        "mismatch",
        "failed",
    }:
        return False

    item_id = _digits(attrs.get("exactDetailItemId"))
    if not item_id:
        return False
    if quality.walmart_item_id_from_url(quality.direct_product_url(card)) != item_id:
        return False

    selected_offer_id = str(getattr(card, "selected_offer_id", "") or "").strip()
    if not selected_offer_id:
        return False

    current = quality.current_price(card)
    reference = quality.reference_price(card)
    if current is None or reference is None or current <= 0 or reference <= current:
        return False

    trusted_reference = quality.float_or_none(attrs.get("trustedReferencePrice"))
    if trusted_reference is None or abs(trusted_reference - reference) > 0.001:
        return False

    reference_source = str(
        quality.attr_value(
            card,
            "api_reference_path",
            "apiReferencePath",
            "trustedReferenceSource",
        )
        or ""
    ).strip()
    if not reference_source:
        return False

    discount = (reference - current) / reference * 100
    if discount < max(1, int(min_discount)):
        return False

    existing_lane = quality.normalized_lane(card)
    if existing_lane == quality.LANE_PRICE_MEMORY_DROP:
        return False
    if existing_lane in {
        quality.LANE_OPEN_BOX_LIKE_NEW,
        quality.LANE_RESTORED_REFURBISHED,
    }:
        lane = existing_lane
    elif _normalized(attrs.get("clearance")) == "yes":
        lane = quality.LANE_CLEARANCE
    elif _normalized(attrs.get("rollback")) == "yes":
        lane = quality.LANE_ROLLBACK
    else:
        lane = quality.LANE_VERIFIED_MARKDOWN

    had_auxiliary_promo = existing_lane in quality.PRIVATE_PROMO_LANES or any(
        attrs.get(key)
        for key in (
            "cartPromo",
            "apiPromotionText",
            "apiPromotionSavingsCap",
            "walmartCashSavings",
            "walmartCashAmount",
            "walmartCashReward",
            "onePayCashback",
            "onepayCashback",
        )
    )

    setattr(card, "deal_lane", lane)
    setattr(card, "api_current_price", current)
    setattr(card, "api_reference_price", reference)
    setattr(card, "api_discount_percent", discount)
    setattr(card, "discount", discount)
    setattr(card, "should_alert", True)

    attrs["dealLane"] = lane
    attrs["publicMarkdownIndependentOfPromo"] = "yes"
    if had_auxiliary_promo:
        attrs["auxiliaryPromoPresent"] = "yes"
    card.variant_attributes = attrs

    # A card passes several public gates. Remove stale review/scout wording and
    # the earlier public-proof explanation before the next gate re-evaluates it.
    _remove_stale_review_markers(card)
    _ensure_walmart_cash_field(card, attrs=attrs, current_price=current)
    return True


def _ensure_walmart_cash_field(
    card: Any,
    *,
    attrs: dict[str, Any],
    current_price: float,
) -> None:
    """Show only strict API-proven Walmart Cash and always include its amount."""

    if _normalized(attrs.get("walmartCashApiProof")) != "yes":
        return

    amount = quality.float_or_none(
        attrs.get("walmartCashAmount")
        or attrs.get("walmartCashSavings")
        or attrs.get("walmartCashReward")
    )
    if amount is None or amount <= 0:
        return
    if amount > max(float(current_price) * 1.10, float(current_price) + 5.00):
        return

    embed = getattr(card, "embed", None)
    if not isinstance(embed, discord.Embed):
        return

    proof_path = " ".join(str(attrs.get("walmartCashProofPath") or "").split())
    proof_label = " ".join(str(attrs.get("walmartCashProofLabel") or "").split())
    source = proof_label or proof_path or "Walmart API"
    value = (
        f"**Earn ${amount:,.2f} Walmart Cash**\n"
        f"Verified from **{source}**. This reward is shown separately and is "
        "not included in the markdown percentage."
    )

    for index, field in enumerate(embed.fields):
        if str(field.name or "") == WALMART_CASH_FIELD:
            embed.set_field_at(index, name=WALMART_CASH_FIELD, value=value, inline=False)
            attrs["walmartCashDisplayed"] = "yes"
            return

    embed.add_field(name=WALMART_CASH_FIELD, value=value, inline=False)
    attrs["walmartCashDisplayed"] = "yes"


def _remove_stale_review_markers(card: Any) -> None:
    label = str(getattr(card, "label", "") or "")
    if _contains_stale_marker(label):
        setattr(card, "label", "Verified Walmart deal")

    embed = getattr(card, "embed", None)
    if not isinstance(embed, discord.Embed):
        return

    title = str(embed.title or "")
    if _contains_stale_marker(title):
        embed.title = "Exact-verified Walmart deal"

    description = str(embed.description or "")
    if description:
        clean_lines = [
            line
            for line in description.splitlines()
            if not _contains_stale_marker(line)
        ]
        embed.description = "\n".join(clean_lines) or None

    remove_indexes: list[int] = []
    for index, field in enumerate(embed.fields):
        text = f"{field.name or ''} {field.value or ''}"
        if _contains_stale_marker(text):
            remove_indexes.append(index)
    for index in reversed(remove_indexes):
        embed.remove_field(index)


def _contains_stale_marker(value: Any) -> bool:
    text = " ".join(str(value or "").lower().split())
    return any(marker in text for marker in _STALE_REVIEW_MARKERS)


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _digits(value: Any) -> str:
    text = str(value or "").strip()
    return text if text.isdigit() else ""
