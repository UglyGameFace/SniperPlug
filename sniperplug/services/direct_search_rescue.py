from __future__ import annotations

import re

SEARCH_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "walmart",
    "online",
    "clearance",
    "rollback",
    "reduced",
    "price",
    "deal",
    "deals",
}


def direct_match_score(query: str, title: str, *, sku: str | None = None, upc: str | None = None, product_id: str | None = None) -> float:
    query_text = normalize_text(query)
    title_text = normalize_text(title)
    if not query_text or not title_text:
        return 0.0

    identifiers = {normalize_text(value) for value in (sku, upc, product_id) if value}
    if query_text in identifiers:
        return 1.0
    if query_text in title_text:
        return 1.0

    query_tokens = meaningful_tokens(query_text)
    if not query_tokens:
        return 0.0
    title_tokens = meaningful_tokens(title_text)
    if not title_tokens:
        return 0.0

    matched = query_tokens & title_tokens
    token_score = len(matched) / len(query_tokens)

    query_phrases = phrase_tokens(query_text)
    phrase_hits = sum(1 for phrase in query_phrases if phrase in title_text)
    phrase_score = min(1.0, phrase_hits / max(len(query_phrases), 1)) if query_phrases else 0.0

    return round(max(token_score, (token_score * 0.75) + (phrase_score * 0.25)), 3)


def is_direct_match(query: str, title: str, *, sku: str | None = None, upc: str | None = None, product_id: str | None = None, threshold: float = 0.45) -> bool:
    return direct_match_score(query, title, sku=sku, upc=upc, product_id=product_id) >= threshold


def normalize_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", str(value or "").lower())).strip()


def meaningful_tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) >= 3 and token not in SEARCH_STOPWORDS}


def phrase_tokens(value: str) -> tuple[str, ...]:
    tokens = [token for token in re.findall(r"[a-z0-9]+", value.lower()) if len(token) >= 3 and token not in SEARCH_STOPWORDS]
    return tuple(" ".join(tokens[i : i + 2]) for i in range(0, max(len(tokens) - 1, 0)))
