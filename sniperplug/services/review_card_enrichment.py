from __future__ import annotations

import re
from typing import Any

import discord


DISCOVERY_TAG_FIELD = "🏷️ Item tags"
LISTING_DETAILS_FIELD = "🧾 Walmart listing details"
PRICE_HISTORY_FIELD = "📉 Price history"
GLOBAL_OBSERVED_REFERENCE_SOURCE = "sniperplug.global_exact_offer_memory.stable_price"


def enrich_review_card(card: Any) -> Any:
    """Add structured retailer tags, listing facts, and honest price context."""
    embed = getattr(card, "embed", None)
    if not isinstance(embed, discord.Embed):
        return card

    attrs = _attrs(card)
    tags = _discovery_tags(card, attrs, embed)
    if tags and not _has_field(embed, DISCOVERY_TAG_FIELD):
        embed.add_field(
            name=DISCOVERY_TAG_FIELD,
            value=" • ".join(f"`{tag}`" for tag in tags),
            inline=False,
        )

    listing_details = _listing_details_text(card, attrs)
    if listing_details and not _has_field(embed, LISTING_DETAILS_FIELD):
        embed.add_field(
            name=LISTING_DETAILS_FIELD,
            value=listing_details,
            inline=False,
        )

    if not _has_field(embed, PRICE_HISTORY_FIELD):
        embed.add_field(
            name=PRICE_HISTORY_FIELD,
            value=_price_history_text(card, attrs, embed),
            inline=False,
        )
    return card


def _attrs(card: Any) -> dict[str, Any]:
    attrs = getattr(card, "variant_attributes", None)
    if isinstance(attrs, dict):
        return attrs
    candidate = getattr(card, "candidate", None)
    attrs = getattr(candidate, "variant_attributes", None)
    return attrs if isinstance(attrs, dict) else {}


def _embed_text(embed: discord.Embed) -> str:
    parts = [str(embed.title or ""), str(embed.description or "")]
    for field in embed.fields:
        parts.extend((str(field.name or ""), str(field.value or "")))
    return " ".join(parts)


def _discovery_tags(card: Any, attrs: dict[str, Any], embed: discord.Embed) -> list[str]:
    explicit_tags = [
        " ".join(tag.split())
        for tag in str(attrs.get("retailerTags") or "").split("|")
        if " ".join(tag.split())
    ]
    text = " ".join(
        [
            _embed_text(embed),
            *(
                str(value or "")
                for value in (
                    attrs.get("finderSourceQuery"),
                    attrs.get("finderSourceQueries"),
                    attrs.get("dealLane"),
                    attrs.get("deal_lane"),
                    attrs.get("condition"),
                    getattr(card, "deal_lane", None),
                    getattr(card, "label", None),
                )
            ),
        ]
    ).lower()
    tags: list[str] = list(dict.fromkeys(explicit_tags))
    checks = (
        ("clearance", "Clearance"),
        ("rollback", "Rollback"),
        ("special buy", "Special Buy"),
        ("overall pick", "Overall Pick"),
        ("best seller", "Best Seller"),
        ("open box", "Open Box"),
        ("like new", "Like New"),
        ("restored", "Restored"),
        ("refurb", "Refurbished"),
        ("walmart cash", "Walmart Cash"),
        ("coupon", "Coupon"),
        ("price_memory", "Observed Price Drop"),
        ("observed price", "Observed Price Drop"),
    )
    for needle, label in checks:
        if needle in text and label not in tags:
            tags.append(label)
    if (
        str(attrs.get("clearance") or "").lower() in {"yes", "true", "1"}
        and "Clearance" not in tags
    ):
        tags.append("Clearance")
    if (
        str(attrs.get("rollback") or "").lower() in {"yes", "true", "1"}
        and "Rollback" not in tags
    ):
        tags.append("Rollback")
    return tags or ["Private Review"]


def _listing_details_text(card: Any, attrs: dict[str, Any]) -> str:
    lines: list[str] = []

    source = str(attrs.get("retailerMetadataSource") or "").strip().lower()
    exact = str(attrs.get("exactDetailPriceProof") or "").strip().lower() == "yes"
    if exact or source == "exact_detail":
        lines.append("• Metadata source: **Exact Walmart item detail**")
    elif source == "search":
        lines.append("• Metadata source: **Walmart search response; exact detail pending**")

    savings = _number(attrs.get("officialSavingsAmount"))
    if savings is not None:
        lines.append(f"• You save: **${savings:,.2f}** from trusted current/was prices")

    seller = str(
        getattr(card, "seller_name", None)
        or attrs.get("seller")
        or ""
    ).strip()
    condition = str(
        getattr(card, "condition", None)
        or attrs.get("condition")
        or ""
    ).strip()
    offer_parts = [part for part in (seller, condition) if part]
    if offer_parts:
        labels = []
        if seller:
            labels.append(f"Seller: **{seller}**")
        if condition:
            labels.append(f"Condition: **{condition}**")
        lines.append("• " + " • ".join(labels))

    condition_options = str(attrs.get("conditionOptions") or "").strip()
    if condition_options:
        lines.append(f"• Condition choices: **{condition_options[:260]}**")

    for label, method in (("Shipping", "shipping"), ("Pickup", "pickup"), ("Delivery", "delivery")):
        method_text = _method_text(attrs, method)
        if method_text:
            lines.append(f"• {label}: **{method_text}**")

    rating = str(attrs.get("rating") or "").strip()
    reviews = str(attrs.get("reviews") or "").strip()
    if rating or reviews:
        value = f"{rating}/5" if rating else "Rating not returned"
        if reviews:
            value += f" from {reviews} review(s)"
        lines.append(f"• Rating: **{value}**")

    purchase_context = str(attrs.get("purchaseContext") or "").strip()
    if purchase_context:
        lines.append(f"• Price context: **{purchase_context[:180]}**")

    return_policy = str(attrs.get("returnPolicy") or "").strip()
    if return_policy:
        lines.append(f"• Returns: **{return_policy[:180]}**")

    location = str(attrs.get("fulfillmentLocation") or "").strip()
    if location:
        lines.append(f"• Location returned by API: **{location[:180]}**")

    return "\n".join(lines[:10])[:1024]


def _method_text(attrs: dict[str, Any], method: str) -> str | None:
    status = str(attrs.get(f"{method}Status") or "").strip()
    text = str(attrs.get(f"{method}Text") or "").strip()
    parts: list[str] = []
    for value in (status, text):
        if value and value not in parts:
            parts.append(value)
    return " — ".join(parts) or None


def _price_history_text(card: Any, attrs: dict[str, Any], embed: discord.Embed) -> str:
    current = _number(
        getattr(card, "api_current_price", None)
        or getattr(card, "current_price", None)
    )
    exact_detail_verified = (
        str(attrs.get("exactDetailPriceProof") or "").strip().lower() == "yes"
    )
    reference_source = str(
        attrs.get("trustedReferenceSource")
        or attrs.get("exactDetailReferenceSource")
        or getattr(card, "api_reference_path", None)
        or ""
    ).strip()
    is_observed_reference = reference_source == GLOBAL_OBSERVED_REFERENCE_SOURCE
    exact_reference_trusted = (
        exact_detail_verified
        and not is_observed_reference
        and str(attrs.get("exactDetailReferenceStatus") or "").strip().lower()
        == "trusted"
        and str(attrs.get("referencePriceTrusted") or "").strip().lower() == "yes"
    )

    walmart_previous = None
    if exact_reference_trusted:
        walmart_previous = _first_number(
            getattr(card, "api_reference_price", None),
            getattr(card, "typical_price", None),
            attrs.get("apiReferencePrice"),
            attrs.get("trustedReferencePrice"),
            _extract_money(
                _embed_text(embed),
                r"(?:walmart was price|walmart was/reference)",
            ),
        )

    observed_previous = _first_number(
        attrs.get("priceMemoryPreviousPrice"),
        attrs.get("observedPreviousPrice"),
        attrs.get("previousObservedPrice"),
        attrs.get("priceMemoryReferencePrice"),
        getattr(card, "api_reference_price", None) if is_observed_reference else None,
        getattr(card, "typical_price", None) if is_observed_reference else None,
        attrs.get("trustedReferencePrice") if is_observed_reference else None,
        _extract_money(
            _embed_text(embed),
            r"(?:previously observed by sniperplug|observed previous price)",
        ),
    )

    lines: list[str] = []
    if walmart_previous and current and walmart_previous > current:
        source = reference_source or "official exact detail"
        lines.append(f"**Walmart was price:** ${walmart_previous:,.2f}")
        lines.append(f"Official exact-item detail source: `{source}`")
    elif observed_previous and current and observed_previous > current:
        lines.append("**Walmart was price:** Not returned")
        lines.append(f"**Previously observed by SniperPlug:** ${observed_previous:,.2f}")
        lines.append(
            "This is exact-offer history collected by SniperPlug, not Walmart's official original or was price."
        )
    else:
        lines.append("**Walmart was price:** Not returned")
        if exact_detail_verified:
            lines.append("**SniperPlug observed history:** Learning — no trusted higher baseline yet")
            lines.append(
                "Walmart's exact detail confirmed the current offer, but did not provide a numeric was price. "
                "The current deal price is never assumed to be the original price."
            )
        else:
            lines.append("**Exact Walmart detail:** Not verified; this result must not be surfaced")
    if current:
        lines.append(f"**Current price:** ${current:,.2f}")
    return "\n".join(lines)


def _extract_money(text: str, label_pattern: str) -> float | None:
    match = re.search(
        label_pattern + r"[^$]{0,40}\$([0-9][0-9,]*(?:\.[0-9]{1,2})?)",
        text,
        flags=re.IGNORECASE,
    )
    return _number(match.group(1)) if match else None


def _has_field(embed: discord.Embed, name: str) -> bool:
    return any(str(field.name or "") == name for field in embed.fields)


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return None
