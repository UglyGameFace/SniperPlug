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


def expand_walmart_query(query: str, *, max_queries: int = 10, boosted_queries: tuple[str, ...] = ()) -> SearchPlan:
    """Expand a user query into recall-first Walmart search routes.

    Direct product searches should not be a tiny markdown-only search. Keep the
    exact user query first, then add compact/product-keyword, sale-surface, and
    category-aware routes while still respecting the caller's max query budget.
    """
    cleaned = normalize_query(query)
    if not cleaned:
        return SearchPlan(original_query=query, queries=(), notes=("empty search query",))

    expansions: list[str] = [cleaned]
    notes: list[str] = ["kept exact user search as the first route"]
    lowered = cleaned.lower()

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
        add_unique(expansions, f"{cleaned} fragrance clearance")
        add_unique(expansions, f"{cleaned} perfume clearance")
        add_unique(expansions, f"{cleaned} cologne clearance")
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
        # Only use memory routes that still overlap the user's current intent.
        if query_overlap(cleaned, boosted_cleaned):
            add_unique(expansions, boosted_cleaned)

    if boosted_queries:
        notes.append("checked server-learned productive routes")

    return SearchPlan(original_query=query, queries=tuple(expansions[:max_queries]), notes=tuple(dedupe(notes)))


def normalize_query(query: str) -> str:
    cleaned = re.sub(r"\s+", " ", (query or "").strip())
    return cleaned[:120]


def compact_product_query(query: str) -> str:
    tokens = [token for token in re.findall(r"[a-z0-9]+", query.lower()) if len(token) >= 3 and token not in SEARCH_STOPWORDS]
    if len(tokens) <= 2:
        return ""
    return " ".join(tokens[:8])


def query_overlap(query: str, boosted_query: str) -> bool:
    query_tokens = meaningful_tokens(query)
    boosted_tokens = meaningful_tokens(boosted_query)
    return bool(query_tokens & boosted_tokens)


def meaningful_tokens(query: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", query.lower()) if len(token) >= 3 and token not in SEARCH_STOPWORDS}


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
