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


def expand_walmart_query(query: str, *, max_queries: int = 5, boosted_queries: tuple[str, ...] = ()) -> SearchPlan:
    """Expand a user query into a small set of Walmart sale-surface searches.

    Keep this deterministic and conservative. The goal is to improve recall without
    adding noisy preset terms that wreck result quality. Boosted queries come from
    per-server route memory and are appended after the direct/safe expansions.
    """
    cleaned = normalize_query(query)
    if not cleaned:
        return SearchPlan(original_query=query, queries=(), notes=("empty search query",))

    expansions: list[str] = [cleaned]
    notes: list[str] = []
    lowered = cleaned.lower()

    add_unique(expansions, f"{cleaned} rollback")
    add_unique(expansions, f"{cleaned} clearance")

    if any(term in lowered for term in TECH_TERMS):
        notes.append("expanded with tech sale surfaces")
        add_unique(expansions, f"{cleaned} electronics clearance")
        add_unique(expansions, f"{cleaned} restored")
    if any(term in lowered for term in PREPAID_TERMS) or "phone" in lowered or "galaxy" in lowered:
        notes.append("expanded with prepaid/mobile surfaces")
        add_unique(expansions, f"{cleaned} prepaid")
        add_unique(expansions, f"straight talk {cleaned}")
    if any(term in lowered for term in HOUSEHOLD_TERMS):
        notes.append("expanded with household rollback surfaces")
        add_unique(expansions, f"{cleaned} household rollback")
    if any(term in lowered for term in TOY_TERMS):
        notes.append("expanded with toy clearance surfaces")
        add_unique(expansions, f"{cleaned} toy clearance")
    if any(term in lowered for term in TOOL_TERMS):
        notes.append("expanded with tool/auto clearance surfaces")
        add_unique(expansions, f"{cleaned} tool clearance")
    if any(term in lowered for term in HOME_TERMS):
        notes.append("expanded with home clearance surfaces")
        add_unique(expansions, f"{cleaned} home clearance")

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


def query_overlap(query: str, boosted_query: str) -> bool:
    query_tokens = meaningful_tokens(query)
    boosted_tokens = meaningful_tokens(boosted_query)
    return bool(query_tokens & boosted_tokens)


def meaningful_tokens(query: str) -> set[str]:
    stopwords = {"the", "and", "for", "with", "clearance", "rollback", "sale", "deals", "deal", "walmart"}
    return {token for token in re.findall(r"[a-z0-9]+", query.lower()) if len(token) >= 3 and token not in stopwords}


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
