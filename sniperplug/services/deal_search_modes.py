from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from sniperplug.cogs.deal_scanner import DealCard


MODE_BEST = "best"
MODE_POPULAR = "popular"
MODE_CHEAPEST = "cheapest"
MODE_HIDDEN = "hidden"
MODE_MARKDOWN = "markdown"


@dataclass(frozen=True)
class DealSearchModeInfo:
    key: str
    label: str
    emoji: str
    description: str
    public_safe: bool = True

    @property
    def display_name(self) -> str:
        return f"{self.emoji} {self.label}"


@dataclass(frozen=True)
class ModeRankedCards:
    mode: DealSearchModeInfo
    verified: list[DealCard]
    review: list[DealCard]
    note: str

    @property
    def has_verified(self) -> bool:
        return bool(self.verified)

    @property
    def has_review(self) -> bool:
        return bool(self.review)


DEAL_SEARCH_MODES: dict[str, DealSearchModeInfo] = {
    MODE_BEST: DealSearchModeInfo(MODE_BEST, "Best Picks", "🔥", "Balances deal proof, brand quality, seller trust, reviews, and price."),
    MODE_POPULAR: DealSearchModeInfo(MODE_POPULAR, "Popular Brands", "🏷️", "Boosts recognizable brands and trusted sellers first."),
    MODE_CHEAPEST: DealSearchModeInfo(MODE_CHEAPEST, "Cheapest", "💸", "Shows the lowest current prices first while still keeping proof visible."),
    MODE_HIDDEN: DealSearchModeInfo(MODE_HIDDEN, "Hidden Gems", "🧪", "Allows offbrand, weird clearance, scout, flip, and review-only leads.", public_safe=False),
    MODE_MARKDOWN: DealSearchModeInfo(MODE_MARKDOWN, "Biggest Markdown", "📉", "Prioritizes the largest verified Walmart markdowns only."),
}

MODE_ORDER = (MODE_BEST, MODE_POPULAR, MODE_HIDDEN, MODE_CHEAPEST, MODE_MARKDOWN)

POPULAR_BRAND_TERMS = {
    "apple",
    "samsung",
    "lg",
    "sony",
    "dell",
    "hp",
    "lenovo",
    "asus",
    "acer",
    "msi",
    "nvidia",
    "amd",
    "intel",
    "logitech",
    "razer",
    "hyperx",
    "corsair",
    "steelseries",
    "sandisk",
    "western digital",
    "wd",
    "seagate",
    "crucial",
    "kingston",
    "anker",
    "belkin",
    "jbl",
    "bose",
    "beats",
    "roku",
    "onn",
    "tcl",
    "hisense",
    "vizio",
    "shark",
    "dyson",
    "bissell",
    "hoover",
    "blackstone",
    "ninja",
    "keurig",
    "instant pot",
    "kitchenaid",
    "hamilton beach",
    "de'longhi",
    "delonghi",
    "milwaukee",
    "dewalt",
    "hart",
    "ryobi",
    "craftsman",
    "stanley",
    "lego",
    "pokemon",
    "barbie",
    "fisher-price",
    "hot wheels",
    "nike",
    "adidas",
    "puma",
    "reebok",
    "under armour",
    "levi",
    "calvin klein",
    "tommy hilfiger",
    "dolce",
    "gabbana",
    "versace",
    "gucci",
    "ysl",
    "armani",
}

LOW_TRUST_MARKERS = (
    "third-party seller",
    "marketplace seller",
    "not alertable",
    "private only",
    "review-only",
    "verify before",
    "staff review",
    "weak reference",
)

HIDDEN_GEM_MARKERS = (
    "review candidate",
    "flip/value lead",
    "flip estimate",
    "marketplace comp",
    "low-price scout",
    "scout",
    "offbrand",
    "private only",
    "exact product match",
)


def normalize_deal_search_mode(mode: str | None) -> DealSearchModeInfo:
    key = str(mode or MODE_BEST).strip().lower().replace("_", "-")
    aliases = {
        "default": MODE_BEST,
        "best-picks": MODE_BEST,
        "brand": MODE_POPULAR,
        "brands": MODE_POPULAR,
        "popular-brands": MODE_POPULAR,
        "cheap": MODE_CHEAPEST,
        "low": MODE_CHEAPEST,
        "lowest": MODE_CHEAPEST,
        "offbrand": MODE_HIDDEN,
        "off-brand": MODE_HIDDEN,
        "hidden-gems": MODE_HIDDEN,
        "gems": MODE_HIDDEN,
        "biggest": MODE_MARKDOWN,
        "discount": MODE_MARKDOWN,
        "markdowns": MODE_MARKDOWN,
        "biggest-markdown": MODE_MARKDOWN,
    }
    key = aliases.get(key, key)
    return DEAL_SEARCH_MODES.get(key, DEAL_SEARCH_MODES[MODE_BEST])


def rank_for_search_mode(verified_cards: list[DealCard], review_cards: list[DealCard], mode: str | None, *, limit: int = 5) -> ModeRankedCards:
    mode_info = normalize_deal_search_mode(mode)
    verified = list(verified_cards or [])
    review = list(review_cards or [])

    if mode_info.key == MODE_POPULAR:
        ranked_verified = sorted(verified, key=popular_card_score, reverse=True)
        ranked_review = sorted(review, key=popular_card_score, reverse=True)
        return ModeRankedCards(mode_info, ranked_verified[:limit], ranked_review[:limit], "Popular Brand Mode boosts recognizable brands, trusted sellers, reviews, and ratings.")

    if mode_info.key == MODE_CHEAPEST:
        ranked_verified = sorted(verified, key=cheapest_card_score, reverse=True)
        ranked_review = sorted(review, key=cheapest_card_score, reverse=True)
        return ModeRankedCards(mode_info, ranked_verified[:limit], ranked_review[:limit], "Cheapest Mode sorts by lowest current API price first; verify variant and seller before buying.")

    if mode_info.key == MODE_HIDDEN:
        hidden_review = sorted(review, key=hidden_gem_card_score, reverse=True)
        hidden_verified = sorted([card for card in verified if not has_popular_brand(card)], key=hidden_gem_card_score, reverse=True)
        if not hidden_verified:
            hidden_verified = sorted(verified, key=hidden_gem_card_score, reverse=True)
        return ModeRankedCards(mode_info, hidden_verified[:limit], hidden_review[:limit], "Hidden Gem Mode allows offbrand/unpopular/private review leads. These stay private unless staff manually shares one.")

    if mode_info.key == MODE_MARKDOWN:
        ranked_verified = sorted(verified, key=markdown_card_score, reverse=True)
        ranked_review = sorted(review, key=markdown_card_score, reverse=True)
        return ModeRankedCards(mode_info, ranked_verified[:limit], ranked_review[:limit], "Biggest Markdown Mode uses verified Walmart markdown cards first. Marketplace comps are not markdown proof.")

    ranked_verified = sorted(verified, key=best_pick_card_score, reverse=True)
    ranked_review = sorted(review, key=best_pick_card_score, reverse=True)
    return ModeRankedCards(mode_info, ranked_verified[:limit], ranked_review[:limit], "Best Picks Mode balances useful brands, trusted sellers, review signals, clean proof, and markdown strength.")


def mode_select_options() -> list[tuple[str, str, str, str]]:
    return [(info.key, info.display_name, info.description, info.emoji) for key, info in DEAL_SEARCH_MODES.items() if key in MODE_ORDER]


def best_pick_card_score(card: DealCard) -> tuple[float, float, float, float]:
    quality = product_quality_score(card)
    trust = seller_trust_score(card)
    discount = float(getattr(card, "discount", 0.0) or 0.0)
    score = float(getattr(card, "score", 0.0) or 0.0)
    price = current_price(card)
    return (quality + trust + discount * 0.9 + min(score, 250.0) * 0.08, discount, trust, -price)


def popular_card_score(card: DealCard) -> tuple[float, float, float]:
    brand = brand_score(card)
    trust = seller_trust_score(card)
    discount = float(getattr(card, "discount", 0.0) or 0.0)
    return (brand * 3.0 + trust + product_quality_score(card), discount, -current_price(card))


def cheapest_card_score(card: DealCard) -> tuple[float, float, float]:
    price = current_price(card)
    discount = float(getattr(card, "discount", 0.0) or 0.0)
    trust = seller_trust_score(card)
    return (-price, trust + discount * 0.25, product_quality_score(card))


def hidden_gem_card_score(card: DealCard) -> tuple[float, float, float]:
    text = card_text(card)
    hidden_bonus = 45.0 if any(marker in text for marker in HIDDEN_GEM_MARKERS) else 0.0
    offbrand_bonus = 25.0 if not has_popular_brand(card) else 0.0
    value_bonus = 20.0 if "coupon" in text or "walmart cash" in text or "flip estimate" in text else 0.0
    price = current_price(card)
    discount = float(getattr(card, "discount", 0.0) or 0.0)
    return (hidden_bonus + offbrand_bonus + value_bonus + discount * 0.45, -price, product_quality_score(card))


def markdown_card_score(card: DealCard) -> tuple[float, float, float]:
    discount = float(getattr(card, "discount", 0.0) or 0.0)
    score = float(getattr(card, "score", 0.0) or 0.0)
    return (discount, score, -current_price(card))


def product_quality_score(card: DealCard) -> float:
    text = card_text(card)
    score = brand_score(card)
    rating = first_float_match(text, r"rating[^0-9]*(\d+(?:\.\d+)?)")
    reviews = first_float_match(text, r"reviews[^0-9]*(\d+(?:,\d{3})*|\d+)")
    if rating:
        score += min(25.0, max(0.0, (rating - 3.0) * 12.0))
    if reviews:
        score += min(25.0, reviews / 100.0)
    if "selected option" in text or "exact product match" in text:
        score += 8.0
    if "product links" in text:
        score += 4.0
    if any(marker in text for marker in LOW_TRUST_MARKERS):
        score -= 20.0
    return score


def brand_score(card: DealCard) -> float:
    text = card_text(card)
    score = 0.0
    for brand in POPULAR_BRAND_TERMS:
        if brand in text:
            score = max(score, 45.0 if len(brand) > 3 else 25.0)
    return score


def has_popular_brand(card: DealCard) -> bool:
    return brand_score(card) > 0


def seller_trust_score(card: DealCard) -> float:
    text = card_text(card)
    score = 0.0
    if "seller: **walmart" in text or "walmartseller" in text or "walmart seller" in text:
        score += 35.0
    if "would alert: **yes" in text:
        score += 25.0
    if "available online" in text or "add to cart" in text or "in stock" in text:
        score += 8.0
    if "third-party" in text or "marketplace seller" in text:
        score -= 18.0
    return score


def current_price(card: DealCard) -> float:
    value = getattr(card, "current_price", None)
    if value is not None:
        try:
            return max(0.0, float(value))
        except (TypeError, ValueError):
            pass
    text = card_text(card)
    parsed = first_float_match(text, r"(?:current product price|now|price)[^$]*\$\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)")
    return parsed or 999999.0


def card_text(card: DealCard) -> str:
    embed = card.embed
    pieces: list[str] = [str(getattr(card, "label", "") or ""), str(embed.title or ""), str(embed.description or "")]
    for field in embed.fields:
        pieces.append(str(field.name or ""))
        pieces.append(str(field.value or ""))
    return " ".join(pieces).lower()


def first_float_match(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except (TypeError, ValueError):
        return None
