from __future__ import annotations

from typing import Any

from sniperplug.services.dm_product_interests import product_emoji_for_text


_DEAL_BADGES = frozenset({"🚨", "🔥", "💸", "💎", "⚡", "✅", "🔎"})
_PRODUCT_BADGES = frozenset(
    {
        "🃏",
        "🧸",
        "🔋",
        "🧴",
        "🧱",
        "🏎️",
        "🎁",
        "🎮",
        "🎧",
        "🔊",
        "💾",
        "🤖",
        "🍳",
        "🧊",
        "☕",
        "🛠️",
        "👶",
        "🥤",
        "👟",
        "⌚",
        "📱",
        "💻",
        "🖥️",
        "📺",
        "🍎",
        "👕",
        "💍",
        "🚗",
        "🏠",
        "✨",
        "📎",
        "🧼",
        "🐾",
        "🏕️",
        "💵",
        "🛒",
        "♻️",
        "🩹",
        "📦",
    }
)


def personalize_dm_card_title(card: Any) -> str:
    """Give a personal DM one deal-strength badge plus one product badge.

    The Walmart bulk fanout clones the source card *after* personal matching.
    Decorating the source title at the successful-match boundary keeps the DM
    notification recognizable without changing price/proof data or adding a
    second rendering path. The operation is idempotent for cards evaluated for
    multiple subscribers.
    """

    embed = getattr(card, "embed", None)
    if embed is None:
        return ""
    title = " ".join(str(getattr(embed, "title", "") or "").split())
    if not title:
        return ""

    product_text = _card_product_text(card)
    product_badge = _specific_product_badge(product_text)
    if not product_badge:
        product_badge = product_emoji_for_text(product_text)
    deal_badge = _deal_strength_badge(card)

    words = title.split()
    while words and words[0] in (_DEAL_BADGES | _PRODUCT_BADGES):
        words.pop(0)
    body = " ".join(words).strip()

    badges: list[str] = []
    for badge in (deal_badge, product_badge):
        if badge and badge not in badges:
            badges.append(badge)
    decorated = " ".join((*badges[:2], body)).strip()
    embed.title = decorated[:256]
    return embed.title


def _deal_strength_badge(card: Any) -> str:
    current = _as_float(
        getattr(card, "api_current_price", None)
        or getattr(card, "current_price", None)
    )
    discount = _as_float(
        getattr(card, "api_discount_percent", None)
        or getattr(card, "discount", None)
    )
    if current is not None and current <= 1:
        return "🚨"
    if discount is not None and discount >= 90:
        return "🚨"
    if discount is not None and discount >= 80:
        return "🔥"
    if discount is not None and discount >= 65:
        return "💸"
    if discount is not None and discount >= 50:
        return "💎"
    if discount is not None and discount >= 35:
        return "⚡"
    if discount is not None and discount >= 20:
        return "✅"
    return "🔎"


def _specific_product_badge(search_text: str) -> str:
    text = f" {_normalized(search_text)} "
    if any(term in text for term in (" dog ", " cat ", " pet ")):
        return "🐾"
    if any(term in text for term in (" baby ", " newborn ", " infant ", " toddler ")):
        return "👶"
    if any(
        term in text
        for term in (
            " motor oil ",
            " engine oil ",
            " tire ",
            " automotive ",
            " vehicle ",
        )
    ):
        return "🚗"
    if any(
        term in text
        for term in (
            " deodorant ",
            " body soap ",
            " bar soap ",
            " body wash ",
            " toothpaste ",
            " laundry detergent ",
        )
    ):
        return "🧼"
    if any(
        term in text
        for term in (
            " grocery ",
            " chili ",
            " snack ",
            " cereal ",
            " candy ",
            " food ",
        )
    ):
        return "🛒"
    return ""


def _card_product_text(card: Any) -> str:
    parts = [str(getattr(card, "label", "") or "")]
    attrs = getattr(card, "variant_attributes", None)
    if isinstance(attrs, dict):
        for key in (
            "brand",
            "manufacturer",
            "category",
            "productCategory",
            "productType",
            "department",
            "taxonomy",
            "breadcrumb",
            "productClass",
            "shelfName",
        ):
            value = attrs.get(key)
            if value not in (None, ""):
                parts.append(str(value))
    embed = getattr(card, "embed", None)
    if embed is not None:
        parts.append(str(getattr(embed, "title", "") or ""))
        parts.append(str(getattr(embed, "description", "") or ""))
        for field in getattr(embed, "fields", []) or []:
            parts.append(str(getattr(field, "name", "") or ""))
            parts.append(str(getattr(field, "value", "") or ""))
    return " ".join(parts)


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").lower().replace("/", " ").split())


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
