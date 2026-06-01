from __future__ import annotations

import re
from dataclasses import dataclass

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


@dataclass(frozen=True)
class DemandProfile:
    key: str
    label: str
    category_terms: tuple[str, ...]
    tier1_brands: tuple[str, ...]
    tier2_brands: tuple[str, ...]
    hot_terms: tuple[str, ...]
    low_priority_terms: tuple[str, ...] = ()
    min_good_rating: float = 4.0
    min_good_reviews: int = 75


DEAL_SEARCH_MODES: dict[str, DealSearchModeInfo] = {
    MODE_BEST: DealSearchModeInfo(MODE_BEST, "Best Picks", "🔥", "Balances category demand, brand quality, seller trust, reviews, proof, and price."),
    MODE_POPULAR: DealSearchModeInfo(MODE_POPULAR, "Popular Brands", "🏷️", "Boosts recognizable brands and trusted sellers first."),
    MODE_CHEAPEST: DealSearchModeInfo(MODE_CHEAPEST, "Cheapest", "💸", "Shows the lowest current prices first while still keeping proof visible."),
    MODE_HIDDEN: DealSearchModeInfo(MODE_HIDDEN, "Hidden Gems", "🧪", "Allows offbrand, weird clearance, scout, flip, and review-only leads.", public_safe=False),
    MODE_MARKDOWN: DealSearchModeInfo(MODE_MARKDOWN, "Biggest Markdown", "📉", "Prioritizes the largest verified Walmart markdowns only."),
}

MODE_ORDER = (MODE_BEST, MODE_POPULAR, MODE_HIDDEN, MODE_CHEAPEST, MODE_MARKDOWN)

POPULAR_BRAND_TERMS = {
    "apple", "samsung", "lg", "sony", "dell", "hp", "lenovo", "asus", "acer", "msi", "nvidia", "amd", "intel",
    "logitech", "razer", "hyperx", "corsair", "steelseries", "sandisk", "western digital", "wd", "seagate", "crucial", "kingston", "anker", "belkin",
    "jbl", "bose", "beats", "roku", "onn", "tcl", "hisense", "vizio",
    "shark", "dyson", "bissell", "hoover", "blackstone", "ninja", "keurig", "instant pot", "kitchenaid", "hamilton beach", "de'longhi", "delonghi",
    "milwaukee", "dewalt", "hart", "ryobi", "craftsman", "stanley", "black+decker", "black & decker", "husky", "kobalt",
    "lego", "pokemon", "barbie", "fisher-price", "fisher price", "hot wheels", "nerf", "hasbro", "mattel", "nintendo", "xbox", "playstation",
    "nike", "adidas", "puma", "reebok", "under armour", "levi", "calvin klein", "tommy hilfiger",
    "dolce", "gabbana", "versace", "gucci", "ysl", "armani", "dior", "burberry", "coach", "nautica", "polo", "ralph lauren",
    "cerave", "cetaphil", "neutrogena", "olay", "dove", "gillette", "tide", "gain", "persil", "bounty", "charmin", "scott", "huggies", "pampers",
}

CATEGORY_PROFILES: tuple[DemandProfile, ...] = (
    DemandProfile(
        key="tech",
        label="Tech & Gaming",
        category_terms=("ipad", "tablet", "laptop", "monitor", "tv", "ssd", "hard drive", "keyboard", "mouse", "headset", "earbuds", "speaker", "console", "gaming", "router", "webcam", "graphics card", "gpu"),
        tier1_brands=("apple", "samsung", "sony", "lg", "dell", "lenovo", "asus", "msi", "nvidia", "amd", "intel", "logitech", "razer", "corsair", "jbl", "bose", "nintendo", "xbox", "playstation"),
        tier2_brands=("acer", "hp", "hyperx", "steelseries", "sandisk", "western digital", "wd", "seagate", "crucial", "kingston", "anker", "belkin", "roku", "onn", "tcl", "hisense", "vizio"),
        hot_terms=("ipad", "macbook", "airpods", "oled", "qled", "4k", "gaming monitor", "ssd", "nvme", "mechanical keyboard", "wireless earbuds", "portable monitor", "graphics card", "gpu"),
        low_priority_terms=("case only", "screen protector", "charger cable only", "refurbished", "used"),
    ),
    DemandProfile(
        key="home",
        label="Home & Kitchen",
        category_terms=("air fryer", "coffee", "espresso", "vacuum", "blender", "mixer", "griddle", "microwave", "cookware", "patio", "mattress", "furniture", "storage", "humidifier", "dehumidifier", "heater", "fan"),
        tier1_brands=("dyson", "shark", "ninja", "keurig", "kitchenaid", "blackstone", "bissell", "de'longhi", "delonghi"),
        tier2_brands=("hoover", "instant pot", "hamilton beach", "oster", "rubbermaid", "sterilite", "mainstays", "better homes", "beautiful"),
        hot_terms=("air fryer", "espresso", "coffee maker", "robot vacuum", "stick vacuum", "griddle", "stand mixer", "patio set", "storage cabinet", "mattress"),
        low_priority_terms=("replacement part", "filter only", "cover only", "accessory only"),
    ),
    DemandProfile(
        key="toys",
        label="Toys & Gifts",
        category_terms=("lego", "pokemon", "toy", "toys", "barbie", "hot wheels", "nerf", "board game", "doll", "scooter", "bike", "collectible", "plush", "nintendo"),
        tier1_brands=("lego", "pokemon", "nintendo", "hot wheels", "barbie", "nerf", "hasbro", "mattel", "fisher-price", "fisher price"),
        tier2_brands=("play-doh", "play doh", "little tikes", "monster high", "lol surprise", "squishmallows", "bluey", "disney", "marvel", "star wars"),
        hot_terms=("lego", "pokemon", "switch", "hot wheels", "barbie", "nerf", "squishmallows", "bluey", "marvel", "star wars"),
        low_priority_terms=("party favor", "sticker", "single card", "replacement", "accessory only"),
        min_good_reviews=40,
    ),
    DemandProfile(
        key="auto_tools",
        label="Auto & Tools",
        category_terms=("tool", "drill", "impact", "socket", "wrench", "battery", "charger", "motor oil", "tire", "inflator", "car wash", "detailing", "garage", "jack", "compressor"),
        tier1_brands=("milwaukee", "dewalt", "ryobi", "craftsman", "stanley", "husky", "kobalt", "mobil 1", "castrol", "valvoline", "pennzoil"),
        tier2_brands=("hart", "black+decker", "black & decker", "hyper tough", "armor all", "chemical guys", "meguiar", "rain-x", "rain x"),
        hot_terms=("impact driver", "drill", "socket set", "battery kit", "motor oil", "tire inflator", "pressure washer", "tool set", "floor jack"),
        low_priority_terms=("bit only", "single bit", "manual", "replacement part", "case only"),
    ),
    DemandProfile(
        key="beauty",
        label="Beauty & Fragrance",
        category_terms=("cologne", "perfume", "fragrance", "makeup", "skincare", "skin care", "razor", "beard", "shampoo", "conditioner", "hair dryer", "curling", "flat iron", "toothbrush"),
        tier1_brands=("dior", "ysl", "yves saint laurent", "armani", "versace", "dolce", "gabbana", "gucci", "burberry", "coach", "polo", "ralph lauren", "calvin klein"),
        tier2_brands=("nautica", "cerave", "cetaphil", "neutrogena", "olay", "dove", "gillette", "remington", "conair", "revlon", "maybelline", "l'oreal", "loreal"),
        hot_terms=("eau de parfum", "eau de toilette", "cologne", "perfume", "fragrance", "gift set", "electric razor", "sonic toothbrush", "hair dryer", "flat iron"),
        low_priority_terms=("sample", "travel size", "tester", "mini", "empty bottle"),
        min_good_reviews=30,
    ),
    DemandProfile(
        key="essentials",
        label="Daily Essentials",
        category_terms=("detergent", "paper towel", "toilet paper", "cleaning", "trash bag", "diaper", "wipe", "toothpaste", "soap", "body wash", "shampoo", "snack", "coffee", "pet food", "razor"),
        tier1_brands=("tide", "gain", "persil", "bounty", "charmin", "scott", "huggies", "pampers", "gillette", "dove", "crest", "oral-b", "oral b", "purina", "pedigree", "keurig"),
        tier2_brands=("arm & hammer", "all", "lysol", "clorox", "febreze", "swiffer", "finish", "cascade", "colgate", "degree", "old spice"),
        hot_terms=("laundry detergent", "paper towels", "toilet paper", "diapers", "trash bags", "razor blades", "k-cup", "k cups", "pet food"),
        low_priority_terms=("single count", "trial size", "sample", "travel size"),
        min_good_reviews=100,
    ),
)

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

ALPHABET_SOUP_BRAND_RE = re.compile(r"\bbrand\s*[:•-]?\s*([a-z0-9]{6,})\b", flags=re.IGNORECASE)


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
        return ModeRankedCards(mode_info, ranked_verified[:limit], ranked_review[:limit], "Popular Brand Mode boosts recognizable brands, trusted sellers, reviews, ratings, and category brand fit.")

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
    return ModeRankedCards(mode_info, ranked_verified[:limit], ranked_review[:limit], "Best Picks Mode now includes category-aware demand, brand tiers, hot product keywords, reviews, seller trust, clean proof, and markdown strength.")


def mode_select_options() -> list[tuple[str, str, str, str]]:
    return [(info.key, info.display_name, info.description, info.emoji) for key, info in DEAL_SEARCH_MODES.items() if key in MODE_ORDER]


def best_pick_card_score(card: DealCard) -> tuple[float, float, float, float, float]:
    quality = product_quality_score(card)
    trust = seller_trust_score(card)
    demand = category_demand_score(card)
    discount = float(getattr(card, "discount", 0.0) or 0.0)
    score = float(getattr(card, "score", 0.0) or 0.0)
    price = current_price(card)
    final = demand + quality + trust + discount * 0.75 + min(score, 250.0) * 0.08
    return (final, demand, discount, trust, -price)


def popular_card_score(card: DealCard) -> tuple[float, float, float]:
    brand = brand_score(card) + category_brand_score(card)
    trust = seller_trust_score(card)
    discount = float(getattr(card, "discount", 0.0) or 0.0)
    return (brand * 2.4 + trust + product_quality_score(card), discount, -current_price(card))


def cheapest_card_score(card: DealCard) -> tuple[float, float, float]:
    price = current_price(card)
    discount = float(getattr(card, "discount", 0.0) or 0.0)
    trust = seller_trust_score(card)
    return (-price, trust + discount * 0.25 + category_demand_score(card) * 0.15, product_quality_score(card))


def hidden_gem_card_score(card: DealCard) -> tuple[float, float, float]:
    text = card_text(card)
    hidden_bonus = 45.0 if any(marker in text for marker in HIDDEN_GEM_MARKERS) else 0.0
    offbrand_bonus = 25.0 if not has_popular_brand(card) else 0.0
    value_bonus = 20.0 if "coupon" in text or "walmart cash" in text or "flip estimate" in text else 0.0
    price = current_price(card)
    discount = float(getattr(card, "discount", 0.0) or 0.0)
    return (hidden_bonus + offbrand_bonus + value_bonus + discount * 0.45 + category_demand_score(card) * 0.15, -price, product_quality_score(card))


def markdown_card_score(card: DealCard) -> tuple[float, float, float]:
    discount = float(getattr(card, "discount", 0.0) or 0.0)
    score = float(getattr(card, "score", 0.0) or 0.0)
    return (discount, score + category_demand_score(card) * 0.2, -current_price(card))


def product_quality_score(card: DealCard) -> float:
    text = card_text(card)
    score = brand_score(card) + category_brand_score(card) * 0.65
    rating = first_float_match(text, r"rating[^0-9]*(\d+(?:\.\d+)?)")
    reviews = first_float_match(text, r"reviews[^0-9]*(\d+(?:,\d{3})*|\d+)")
    profile = infer_category_profile(card)
    if rating:
        min_rating = profile.min_good_rating if profile else 4.0
        score += min(30.0, max(-8.0, (rating - min_rating) * 14.0 + 10.0))
    if reviews:
        min_reviews = profile.min_good_reviews if profile else 75
        score += min(30.0, reviews / max(20.0, min_reviews / 2.0))
    if "selected option" in text or "exact product match" in text:
        score += 8.0
    if "product links" in text:
        score += 4.0
    if any(marker in text for marker in LOW_TRUST_MARKERS):
        score -= 22.0
    if profile and any(term in text for term in profile.low_priority_terms):
        score -= 22.0
    if alphabet_soup_brand_penalty(text):
        score -= 12.0
    return score


def category_demand_score(card: DealCard) -> float:
    text = card_text(card)
    profile = infer_category_profile(card)
    if profile is None:
        return global_demand_score(card)
    score = 14.0
    score += category_brand_score(card)
    for term in profile.hot_terms:
        if term in text:
            score += 18.0
            break
    hot_hits = sum(1 for term in profile.hot_terms if term in text)
    score += min(24.0, max(0, hot_hits - 1) * 6.0)
    if any(term in text for term in profile.low_priority_terms):
        score -= 25.0
    score += global_demand_score(card) * 0.35
    return score


def global_demand_score(card: DealCard) -> float:
    text = card_text(card)
    score = 0.0
    global_hot_terms = (
        "clearance", "rollback", "special buy", "limited", "bundle", "gift set", "starter kit", "pro", "max", "ultra", "oled", "4k", "lego", "pokemon", "dyson", "shark", "ninja", "milwaukee", "dewalt", "apple", "samsung",
    )
    for term in global_hot_terms:
        if term in text:
            score += 7.0
    return min(score, 45.0)


def category_brand_score(card: DealCard) -> float:
    text = card_text(card)
    profile = infer_category_profile(card)
    if profile is None:
        return 0.0
    score = 0.0
    for brand in profile.tier1_brands:
        if brand in text:
            score = max(score, 65.0)
    for brand in profile.tier2_brands:
        if brand in text:
            score = max(score, 42.0)
    return score


def infer_category_profile(card: DealCard) -> DemandProfile | None:
    text = card_text(card)
    best_profile: DemandProfile | None = None
    best_hits = 0
    for profile in CATEGORY_PROFILES:
        hits = sum(1 for term in profile.category_terms if term in text)
        if hits > best_hits:
            best_profile = profile
            best_hits = hits
    return best_profile if best_hits else None


def brand_score(card: DealCard) -> float:
    text = card_text(card)
    score = 0.0
    for brand in POPULAR_BRAND_TERMS:
        if brand in text:
            score = max(score, 45.0 if len(brand) > 3 else 25.0)
    return score


def has_popular_brand(card: DealCard) -> bool:
    return brand_score(card) > 0 or category_brand_score(card) > 0


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


def alphabet_soup_brand_penalty(text: str) -> bool:
    match = ALPHABET_SOUP_BRAND_RE.search(text)
    if not match:
        return False
    brand = match.group(1).lower()
    if brand in POPULAR_BRAND_TERMS:
        return False
    vowels = sum(1 for char in brand if char in "aeiou")
    return len(brand) >= 7 and vowels <= 2


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
