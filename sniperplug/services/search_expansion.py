from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SearchPlan:
    original_query: str
    queries: tuple[str, ...]
    notes: tuple[str, ...] = ()


TECH_TERMS = (
    "phone",
    "iphone",
    "galaxy",
    "samsung",
    "tablet",
    "monitor",
    "tv",
    "laptop",
    "ssd",
    "headset",
    "earbuds",
    "keyboard",
    "mouse",
)

PREPAID_TERMS = ("straight talk", "tracfone", "total wireless", "prepaid", "locked")
HOUSEHOLD_TERMS = ("detergent", "paper", "toilet", "cleaner", "soap", "diaper", "wipes", "razor")
TOY_TERMS = ("lego", "toy", "pokemon", "barbie", "board game", "collectible")
TOOL_TERMS = ("tool", "drill", "dewalt", "milwaukee", "hart", "hyper tough", "socket", "pressure washer")
HOME_TERMS = ("air fryer", "vacuum", "coffee", "patio", "furniture", "mattress", "appliance")
BEAUTY_FRAGRANCE_TERMS = (
    "fragrance",
    "cologne",
    "perfume",
    "parfum",
    "eau de parfum",
    "eau de toilette",
    "edt",
    "edp",
    "spray",
    "dolce",
    "gabbana",
    "gucci",
    "versace",
    "armani",
    "yves saint laurent",
    "ysl",
    "prada",
    "burberry",
    "calvin klein",
)
FRAGRANCE_CATEGORY_TERMS = ("cologne", "fragrance", "perfume")
GENERIC_DEAL_SURFACES = ("rollback", "clearance")
SECONDARY_DEAL_SURFACES = ("reduced price", "special buy", "online clearance")
SEARCH_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "clearance",
    "rollback",
    "sale",
    "deals",
    "deal",
    "walmart",
    "online",
    "price",
    "reduced",
    "special",
    "buy",
}
GENDER_WORDS = {"men", "mens", "man", "male", "women", "womens", "woman", "female"}


def expand_walmart_query(query: str, *, max_queries: int = 14, boosted_queries: tuple[str, ...] = ()) -> SearchPlan:
    """Expand a user query into recall-first Walmart search routes.

    The goal is not just to search the exact sentence. Walmart search is brittle:
    long queries, gender words, missing category nouns, and brand/model wording can
    hide products. We keep the exact route first, then add relaxed identity routes
    before deal-surface routes.
    """
    cleaned = normalize_query(query)
    if not cleaned:
        return SearchPlan(original_query=query, queries=(), notes=("empty search query",))

    expansions: list[str] = [cleaned]
    notes: list[str] = ["kept exact user search as the first route"]
    lowered = cleaned.lower()

    for variant in relaxed_identity_queries(cleaned):
        add_unique(expansions, variant)
    if len(expansions) > 1:
        notes.append("added relaxed brand/model routes so exact product names are not required")

    compact = compact_product_query(cleaned)
    if compact and compact.lower() != lowered:
        add_unique(expansions, compact)
        notes.append("added compact product-keyword route")

    for surface in GENERIC_DEAL_SURFACES:
        add_unique(expansions, f"{cleaned} {surface}")

    if any(term in lowered for term in TECH_TERMS):
        notes.append("expanded with tech sale surfaces")
        add_unique(expansions, f"{cleaned} electronics clearance")
        add_unique(expansions, f"{cleaned} restored")
        add_unique(expansions, f"{cleaned} open box")
    if any(term in lowered for term in PREPAID_TERMS) or "phone" in lowered or "galaxy" in lowered:
        notes.append("expanded with prepaid/mobile surfaces")
        add_unique(expansions, f"{cleaned} prepaid")
        add_unique(expansions, f"straight talk {cleaned}")
    if any(term in lowered for term in BEAUTY_FRAGRANCE_TERMS):
        notes.append("expanded with beauty/fragrance deal surfaces")
        # Clearance/value routes must come before plain category variants so
        # tight max_queries callers still include deal-finding surfaces.
        for variant in fragrance_category_clearance_queries(cleaned):
            add_unique(expansions, variant)
        for variant in fragrance_category_queries(cleaned):
            add_unique(expansions, variant)
        add_unique(expansions, f"designer fragrance {cleaned}")
    if any(term in lowered for term in HOUSEHOLD_TERMS):
        notes.append("expanded with household rollback surfaces")
        add_unique(expansions, f"{cleaned} household rollback")
        add_unique(expansions, f"{cleaned} value pack")
    if any(term in lowered for term in TOY_TERMS):
        notes.append("expanded with toy clearance surfaces")
        add_unique(expansions, f"{cleaned} toy clearance")
        add_unique(expansions, f"{cleaned} collector")
    if any(term in lowered for term in TOOL_TERMS):
        notes.append("expanded with tool/auto clearance surfaces")
        add_unique(expansions, f"{cleaned} tool clearance")
        add_unique(expansions, f"{cleaned} hardware clearance")
    if any(term in lowered for term in HOME_TERMS):
        notes.append("expanded with home clearance surfaces")
        add_unique(expansions, f"{cleaned} home clearance")
        add_unique(expansions, f"{cleaned} kitchen clearance")

    for surface in SECONDARY_DEAL_SURFACES:
        add_unique(expansions, f"{cleaned} {surface}")

    for boosted in boosted_queries:
        if len(expansions) >= max_queries:
            break
        boosted_cleaned = normalize_query(boosted)
        if not boosted_cleaned:
            continue
        if query_overlap(cleaned, boosted_cleaned):
            add_unique(expansions, boosted_cleaned)

    if boosted_queries:
        notes.append("checked server-learned productive routes")

    return SearchPlan(original_query=query, queries=tuple(expansions[:max_queries]), notes=tuple(dedupe(notes)))


def relaxed_identity_queries(query: str) -> tuple[str, ...]:
    """Create high-priority identity routes that still mean the same product.

    Example: "dolce gabbana the one for men" should also search
    "dolce gabbana the one", "dolce gabbana the one cologne", and
    "the one cologne". This is product-name recall, not a cologne-only hack.
    """
    normalized = normalize_query(query)
    lowered = normalized.lower()
    variants: list[str] = []

    without_gender = strip_gender_suffix(normalized)
    if without_gender and without_gender.lower() != lowered:
        add_unique(variants, without_gender)

    tokens = token_list(normalized)
    meaningful = [token for token in tokens if token not in SEARCH_STOPWORDS]
    no_gender = [token for token in meaningful if token not in GENDER_WORDS]
    gender_tokens = [token for token in meaningful if token in GENDER_WORDS]
    has_fragrance_category = any(term in no_gender for term in FRAGRANCE_CATEGORY_TERMS)

    if len(no_gender) >= 2:
        add_unique(variants, " ".join(no_gender))
    if gender_tokens and len(no_gender) >= 3:
        add_unique(variants, " ".join(no_gender[:3]))
        add_unique(variants, " ".join(no_gender[-3:]))
    if len(no_gender) >= 2 and any(term in lowered for term in BEAUTY_FRAGRANCE_TERMS):
        base = " ".join(no_gender)
        if not has_fragrance_category:
            add_unique(variants, f"{base} cologne")
            add_unique(variants, f"{base} fragrance")
            add_unique(variants, f"{' '.join(no_gender[-2:])} cologne")
        if gender_tokens and len(no_gender) >= 2:
            add_unique(variants, f"{' '.join(no_gender[-2:])} {gender_tokens[0]} cologne")

    return tuple(variants)


def fragrance_category_clearance_queries(query: str) -> tuple[str, ...]:
    variants = []
    for category in FRAGRANCE_CATEGORY_TERMS:
        add_unique(variants, f"{query} {category} clearance")
    return tuple(variants)


def fragrance_category_queries(query: str) -> tuple[str, ...]:
    variants = []
    for category in FRAGRANCE_CATEGORY_TERMS:
        add_unique(variants, f"{query} {category}")
    return tuple(variants)


def strip_gender_suffix(query: str) -> str:
    text = re.sub(r"\b(for|by)\s+(men|mens|man|male|women|womens|woman|female)\b", "", query, flags=re.I)
    text = re.sub(r"\b(men|mens|man|male|women|womens|woman|female)'?s?\b", "", text, flags=re.I)
    return normalize_query(text)


def normalize_query(query: str) -> str:
    cleaned = re.sub(r"\s+", " ", (query or "").strip())
    return cleaned[:120]


def compact_product_query(query: str) -> str:
    tokens = [token for token in token_list(query) if len(token) >= 3 and token not in SEARCH_STOPWORDS]
    if len(tokens) <= 2:
        return ""
    return " ".join(tokens[:8])


def query_overlap(query: str, boosted_query: str) -> bool:
    query_tokens = meaningful_tokens(query)
    boosted_tokens = meaningful_tokens(boosted_query)
    return bool(query_tokens & boosted_tokens)


def token_list(query: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", query.lower())


def meaningful_tokens(query: str) -> set[str]:
    return {token for token in token_list(query) if len(token) >= 3 and token not in SEARCH_STOPWORDS}


def add_unique(values: list[str], value: str) -> None:
    normalized = normalize_query(value).lower()
    if not normalized:
        return
    if normalized not in {item.lower() for item in values}:
        values.append(normalize_query(value))


def dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        marker = value.lower().strip()
        if marker in seen:
            continue
        seen.add(marker)
        result.append(value)
    return result
