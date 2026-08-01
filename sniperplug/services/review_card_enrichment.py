from __future__ import annotations

import re
from typing import Any

import discord


DISCOVERY_TAG_FIELD = "🏷️ Item tags"
PRICE_HISTORY_FIELD = "📉 Price history"
GLOBAL_OBSERVED_REFERENCE_SOURCE = "sniperplug.global_exact_offer_memory.stable_price"


def enrich_review_card(card: Any) -> Any:
    """Add structured discovery tags and honest price-history context to review cards."""
    embed = getattr(card, "embed", None)
    if not isinstance(embed, discord.Embed):
        return card

    attrs = _attrs(card)
    tags = _discovery_tags(card, attrs, embed)
    if tags and not _has_field(embed, DISCOVERY_TAG_FIELD):
        embed.add_field(name=DISCOVERY_TAG_FIELD, value=" • ".join(f"`{tag}`" for tag in tags), inline=False)

    if not _has_field(embed, PRICE_HISTORY_FIELD):
        embed.add_field(name=PRICE_HISTORY_FIELD, value=_price_history_text(card, attrs, embed), inline=False)
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


def _price_history_text(card: Any, attrs: dict[str, Any], embed: discord.Embed) -> str:
    current = _number(getattr(card, "api_current_price", None) or getattr(card, "current_price", None))
    reference = _first_number(
        getattr(card, "api_reference_price", None),
        getattr(card, "typical_price", None),
        attrs.get("apiReferencePrice"),
        attrs.get("trustedReferencePrice"),
    )
    reference_source = str(
        getattr(card, "api_reference_path", None)
        or attrs.get("trustedReferenceSource")
        or attrs.get("apiReferencePath")
        or ""
    ).strip()
    identity = str(attrs.get("priceMemoryIdentity") or "").strip()
    is_observed_reference = (
        reference_source == GLOBAL_OBSERVED_REFERENCE_SOURCE
        or identity.startswith("walmart-offer:v1:")
    )
    exact_detail_verified = str(attrs.get("exactDetailPriceProof") or "").strip().lower() == "yes"
    exact_reference_trusted = str(attrs.get("exactDetailReferenceStatus") or "").strip().lower() == "trusted"

    embedded_walmart_reference = _extract_money(
        _embed_text(embed),
        r"(?:trusted was/typical|walmart was/reference|walmart was price)",
    )
    embedded_observed_reference = _extract_money(
        _embed_text(embed),
        r"(?:previously observed by sniperplug|observed previous price|stable observed price)",
    )

    walmart_previous = None
    observed_previous = None
    if is_observed_reference:
        observed_previous = _first_number(reference, embedded_observed_reference)
    elif exact_reference_trusted or reference_source:
        walmart_previous = _first_number(reference, embedded_walmart_reference)
    else:
        walmart_previous = embedded_walmart_reference
        observed_previous = embedded_observed_reference

    lines: list[str] = []
    if walmart_previous and current and walmart_previous > current:
        lines.append(f"**Walmart was price:** ${walmart_previous:,.2f}")
        lines.append("This reference came from Walmart's exact item-detail response.")
    elif observed_previous and current and observed_previous > current:
        lines.append("**Walmart was price:** Not returned")
        lines.append(f"**Previously observed by SniperPlug:** ${observed_previous:,.2f}")
        lines.append(
            "This is a repeatedly confirmed price for the same exact offer. "
            "It is not Walmart's official original or was price."
        )
    else:
        lines.append("**Walmart was price:** Not returned")
        if exact_detail_verified:
            lines.append("**SniperPlug observed history:** Learning — no trusted higher baseline yet")
            lines.append(
                "The current deal price is only recorded as today's exact-offer price. "
                "It is never assumed to be the original price. A later higher price can establish history, "
                "and a future lower price can then be compared with that repeatedly confirmed baseline."
            )
        else:
            lines.append("**SniperPlug observed history:** Not recorded from this result")
            lines.append("The exact item/offer proof was incomplete, so this result cannot train trusted price history.")
    if current:
        lines.append(f"**Current price:** ${current:,.2f}")
    return "\n".join(lines)


def _extract_money(text: str, label_pattern: str) -> float | None:
    match = re.search(label_pattern + r"[^$]{0,40}\$([0-9][0-9,]*(?:\.[0-9]{1,2})?)", text, flags=re.IGNORECASE)
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
