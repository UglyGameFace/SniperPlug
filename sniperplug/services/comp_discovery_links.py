from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class CompLink:
    label: str
    url: str


@dataclass(frozen=True)
class ProductCompIdentity:
    query: str
    exact_query: str
    identifiers: tuple[str, ...]
    category_hint: str | None = None


STOP_WORDS = {
    "for",
    "with",
    "and",
    "the",
    "new",
    "brand",
    "walmart",
    "free",
    "shipping",
    "online",
    "only",
}

FRAGRANCE_TERMS = {
    "fragrance",
    "cologne",
    "perfume",
    "parfum",
    "edp",
    "edt",
    "toilette",
    "homme",
    "spray",
}

TECH_TERMS = {
    "tv",
    "monitor",
    "laptop",
    "phone",
    "tablet",
    "ssd",
    "gpu",
    "graphics",
    "headset",
    "keyboard",
    "mouse",
    "smartphone",
}

HOME_TERMS = {
    "vanity",
    "faucet",
    "appliance",
    "vacuum",
    "air",
    "fryer",
    "coffee",
    "patio",
    "furniture",
    "mattress",
}

APPAREL_TERMS = {
    "shoe",
    "shoes",
    "sneaker",
    "shirt",
    "hoodie",
    "jacket",
    "jeans",
    "nike",
    "adidas",
    "puma",
}


def build_comp_identity(
    *,
    title: str,
    brand: str | None = None,
    upc: str | None = None,
    model: str | None = None,
    sku: str | None = None,
    category: str | None = None,
) -> ProductCompIdentity:
    clean_title = normalize_title(title)
    brand_text = normalize_title(brand or "")
    model_text = normalize_identifier(model)
    upc_text = normalize_identifier(upc)
    sku_text = normalize_identifier(sku)

    tokens = compact_tokens(clean_title)
    if brand_text and brand_text.lower() not in clean_title.lower():
        tokens = compact_tokens(f"{brand_text} {' '.join(tokens)}")
    query = " ".join(tokens[:12]).strip() or clean_title or brand_text or upc_text or model_text or sku_text

    identifiers = tuple(value for value in dedupe([upc_text, model_text]) if value)
    exact_parts = [query]
    if identifiers:
        exact_parts.append(identifiers[0])
    exact_query = " ".join(part for part in exact_parts if part).strip()

    hint = category_hint(clean_title, category=category)
    return ProductCompIdentity(query=query, exact_query=exact_query or query, identifiers=identifiers, category_hint=hint)


def build_free_comp_links(
    *,
    title: str,
    brand: str | None = None,
    upc: str | None = None,
    model: str | None = None,
    sku: str | None = None,
    category: str | None = None,
    max_links: int = 7,
) -> tuple[CompLink, ...]:
    identity = build_comp_identity(title=title, brand=brand, upc=upc, model=model, sku=sku, category=category)
    query = identity.exact_query or identity.query
    links: list[CompLink] = [
        CompLink("Google Shopping", google_shopping_url(query)),
        CompLink("Google Web", google_web_url(query)),
        CompLink("eBay Sold", ebay_sold_url(query)),
    ]

    if identity.identifiers:
        links.append(CompLink("UPC/Model Search", google_web_url(identity.identifiers[0])))

    links.extend(category_specific_links(identity))
    return tuple(dedupe_links(links)[:max_links])


def comp_link_block(links: Iterable[CompLink], *, max_links: int = 7) -> str:
    rendered = [f"[{link.label}]({link.url})" for link in list(links)[:max_links] if link.url]
    return " • ".join(rendered)


def google_shopping_url(query: str) -> str:
    return "https://www.google.com/search?tbm=shop&q=" + quote(query)


def google_web_url(query: str) -> str:
    return "https://www.google.com/search?q=" + quote(query)


def ebay_sold_url(query: str) -> str:
    return "https://www.ebay.com/sch/i.html?_nkw=" + quote(query) + "&LH_Sold=1&LH_Complete=1"


def retailer_search_url(retailer_host: str, query: str) -> str:
    return google_web_url(f"site:{retailer_host} {query}")


def category_specific_links(identity: ProductCompIdentity) -> list[CompLink]:
    query = identity.exact_query or identity.query
    hint = identity.category_hint
    if hint == "fragrance":
        return [
            CompLink("Macy's", retailer_search_url("macys.com", query)),
            CompLink("Ulta", retailer_search_url("ulta.com", query)),
            CompLink("Sephora", retailer_search_url("sephora.com", query)),
            CompLink("FragranceNet", retailer_search_url("fragrancenet.com", query)),
            CompLink("Jomashop", retailer_search_url("jomashop.com", query)),
        ]
    if hint == "tech":
        return [
            CompLink("Amazon", retailer_search_url("amazon.com", query)),
            CompLink("Best Buy", retailer_search_url("bestbuy.com", query)),
            CompLink("Target", retailer_search_url("target.com", query)),
            CompLink("B&H", retailer_search_url("bhphotovideo.com", query)),
        ]
    if hint == "home":
        return [
            CompLink("Home Depot", retailer_search_url("homedepot.com", query)),
            CompLink("Lowe's", retailer_search_url("lowes.com", query)),
            CompLink("Target", retailer_search_url("target.com", query)),
            CompLink("Amazon", retailer_search_url("amazon.com", query)),
        ]
    if hint == "apparel":
        return [
            CompLink("Nike/adidas", google_web_url(f"Nike Adidas Puma {query}")),
            CompLink("StockX", retailer_search_url("stockx.com", query)),
            CompLink("GOAT", retailer_search_url("goat.com", query)),
            CompLink("eBay Active", "https://www.ebay.com/sch/i.html?_nkw=" + quote(query)),
        ]
    return [
        CompLink("Amazon", retailer_search_url("amazon.com", query)),
        CompLink("Target", retailer_search_url("target.com", query)),
    ]


def category_hint(title: str, *, category: str | None = None) -> str | None:
    text = f"{title} {category or ''}".lower()
    words = set(re.findall(r"[a-z0-9]+", text))
    if words & FRAGRANCE_TERMS:
        return "fragrance"
    if words & TECH_TERMS:
        return "tech"
    if words & HOME_TERMS:
        return "home"
    if words & APPAREL_TERMS:
        return "apparel"
    return None


def normalize_title(value: str | None) -> str:
    text = str(value or "")
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"[^A-Za-z0-9&.+\- /]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_identifier(value: str | None) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "none":
        return ""
    return re.sub(r"[^A-Za-z0-9\-]", "", text)


def compact_tokens(title: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-z0-9&.+\-]+", title)
    tokens: list[str] = []
    for token in raw_tokens:
        clean = token.strip("-_. ")
        if not clean:
            continue
        if clean.lower() in STOP_WORDS:
            continue
        tokens.append(clean)
    return tokens


def dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = str(value or "").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(str(value))
    return result


def dedupe_links(links: Iterable[CompLink]) -> list[CompLink]:
    seen: set[str] = set()
    result: list[CompLink] = []
    for link in links:
        key = link.url
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(link)
    return result


def quote(query: str) -> str:
    return urllib.parse.quote_plus(" ".join(str(query or "").split()))
