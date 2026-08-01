from __future__ import annotations

from typing import Any

import discord


DISCOVERY_TAG_FIELD = "🏷️ Item tags"
PRICE_HISTORY_FIELD = "📉 Price history"


def enrich_review_card(card: Any) -> Any:
    """Add structured discovery tags and honest price-history context to review cards."""
    embed = getattr(card, "embed", None)
    if not isinstance(embed, discord.Embed):
        return card

    attrs = _attrs(card)
    tags = _discovery_tags(card, attrs)
    if tags and not _has_field(embed, DISCOVERY_TAG_FIELD):
        embed.add_field(name=DISCOVERY_TAG_FIELD, value=" • ".join(f"`{tag}`" for tag in tags), inline=False)

    if not _has_field(embed, PRICE_HISTORY_FIELD):
        embed.add_field(name=PRICE_HISTORY_FIELD, value=_price_history_text(card, attrs), inline=False)
    return card


def _attrs(card: Any) -> dict[str, Any]:
    attrs = getattr(card, "variant_attributes", None)
    if isinstance(attrs, dict):
        return attrs
    candidate = getattr(card, "candidate", None)
    attrs = getattr(candidate, "variant_attributes", None)
    return attrs if isinstance(attrs, dict) else {}


def _discovery_tags(card: Any, attrs: dict[str, Any]) -> list[str]:
    text = " ".join(
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
    ).lower()
    tags: list[str] = []
    checks = (
        ("clearance", "Clearance"),
        ("rollback", "Rollback"),
        ("special buy", "Special Buy"),
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
    if str(attrs.get("clearance") or "").lower() in {"yes", "true", "1"} and "Clearance" not in tags:
        tags.append("Clearance")
    if str(attrs.get("rollback") or "").lower() in {"yes", "true", "1"} and "Rollback" not in tags:
        tags.append("Rollback")
    return tags or ["Private Review"]


def _price_history_text(card: Any, attrs: dict[str, Any]) -> str:
    current = _number(getattr(card, "api_current_price", None) or getattr(card, "current_price", None))
    walmart_previous = _first_number(
        getattr(card, "api_reference_price", None),
        getattr(card, "typical_price", None),
        attrs.get("apiReferencePrice"),
        attrs.get("trustedReferencePrice"),
    )
    observed_previous = _first_number(
        attrs.get("priceMemoryPreviousPrice"),
        attrs.get("observedPreviousPrice"),
        attrs.get("previousObservedPrice"),
        attrs.get("priceMemoryReferencePrice"),
    )
    lines: list[str] = []
    if walmart_previous and current and walmart_previous > current:
        lines.append(f"**Walmart was/reference:** ${walmart_previous:,.2f}")
    elif observed_previous and current and observed_previous > current:
        lines.append(f"**Previously observed by SniperPlug:** ${observed_previous:,.2f}")
        lines.append("This is exact-item history collected by SniperPlug, not a marketplace comparison.")
    else:
        lines.append("**Previous trustworthy price:** Not available yet")
        lines.append("Walmart did not return a trusted was price, and SniperPlug has not yet observed a higher exact-item price.")
    if current:
        lines.append(f"**Current price:** ${current:,.2f}")
    return "\n".join(lines)


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
