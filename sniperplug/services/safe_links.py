from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

from sniperplug.services.comp_discovery_links import build_free_comp_links


@dataclass(frozen=True)
class SafeLinkResult:
    url: str
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class LinkChoice:
    label: str
    url: str


INTERNAL_OR_API_HOSTS = {
    "apionline.homedepot.com",
    "www.apionline.homedepot.com",
    "developer.api.walmart.com",
    "api.walmart.com",
    "serpapi.com",
    "www.serpapi.com",
}

SAFE_QUERY_KEYS = {
    "tag",
    "ascsubtag",
    "irclickid",
    "veh",
    "wmlspartner",
    "selectedSellerId",
}


def normalize_product_url(
    *,
    retailer: str,
    url: str | None,
    product_id: str | None = None,
    sku: str | None = None,
    asin: str | None = None,
) -> SafeLinkResult:
    """Normalize provider URLs into browser-friendly public product pages."""
    retailer_key = retailer.strip().lower()
    raw_url = (url or "").strip()
    notes: list[str] = []

    if "home depot" in retailer_key or "homedepot" in retailer_key:
        return _normalize_home_depot(raw_url, product_id=product_id or sku, notes=notes)
    if "walmart" in retailer_key:
        return _normalize_walmart(raw_url, product_id=product_id or sku, notes=notes)
    if "amazon" in retailer_key:
        return _normalize_amazon(raw_url, asin=asin or product_id, notes=notes)
    if "best buy" in retailer_key or "bestbuy" in retailer_key:
        return _normalize_best_buy(raw_url, sku=sku or product_id, notes=notes)
    if "target" in retailer_key:
        return _normalize_target(raw_url, tcin=sku or product_id, notes=notes)

    return _normalize_generic(raw_url, notes=notes)


def product_link_choices(
    *,
    retailer: str,
    product_url: str,
    title: str = "",
    product_id: str | None = None,
    sku: str | None = None,
    asin: str | None = None,
    upc: str | None = None,
    brand: str | None = None,
    model: str | None = None,
    category: str | None = None,
    include_comp_links: bool = True,
) -> tuple[LinkChoice, ...]:
    """Return user-facing app/web and comp choices for Discord cards.

    Product links stay direct. Comp links are free discovery links only; they do
    not scrape Google Shopping or pretend scraped prices are verified proof.
    """
    safe = normalize_product_url(retailer=retailer, url=product_url, product_id=product_id, sku=sku, asin=asin).url
    search = retailer_browser_search_url(retailer=retailer, title=title, product_id=product_id, sku=sku, asin=asin)
    choices = [LinkChoice("Open App/Web", safe)]
    if search and search != safe:
        choices.append(LinkChoice("Browser Search", search))
    if include_comp_links:
        comp_links = build_free_comp_links(title=title, brand=brand, upc=upc, model=model, sku=sku or product_id, category=category, max_links=4)
        choices.extend(LinkChoice(link.label, link.url) for link in comp_links)
    return tuple(dedupe_link_choices(choices))


def retailer_browser_search_url(*, retailer: str, title: str = "", product_id: str | None = None, sku: str | None = None, asin: str | None = None) -> str | None:
    retailer_key = retailer.strip().lower()
    identifier = asin or sku or product_id
    if "amazon" in retailer_key and asin:
        return f"https://www.google.com/search?q={urllib.parse.quote_plus(f'Amazon {asin}') }"
    if "home depot" in retailer_key or "homedepot" in retailer_key:
        query = f"Home Depot {identifier or title}".strip()
        return f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"
    if "walmart" in retailer_key:
        query = f"Walmart {identifier or title}".strip()
        return f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"
    if "best buy" in retailer_key or "bestbuy" in retailer_key:
        query = f"Best Buy {identifier or title}".strip()
        return f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"
    if "target" in retailer_key:
        query = f"Target {identifier or title}".strip()
        return f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"
    if title or identifier:
        return f"https://www.google.com/search?q={urllib.parse.quote_plus((retailer + ' ' + (identifier or title)).strip())}"
    return None


def _normalize_home_depot(raw_url: str, *, product_id: str | None, notes: list[str]) -> SafeLinkResult:
    parsed = _parse_url(raw_url)
    if raw_url.startswith("/p/"):
        notes.append("product link normalized to Home Depot public URL")
        return SafeLinkResult(f"https://www.homedepot.com{raw_url}", tuple(notes))
    if parsed and parsed.path.startswith("/p/"):
        host = parsed.netloc.lower()
        if host != "www.homedepot.com" or parsed.scheme != "https":
            notes.append("product link normalized to Home Depot public URL")
        return SafeLinkResult(_public_url("www.homedepot.com", parsed.path, _safe_query(parsed.query)), tuple(notes))
    if product_id:
        notes.append("product link rebuilt from Home Depot product ID")
        return SafeLinkResult(f"https://www.homedepot.com/p/{product_id}", tuple(notes))
    return _normalize_generic(raw_url, notes=notes)


def _normalize_walmart(raw_url: str, *, product_id: str | None, notes: list[str]) -> SafeLinkResult:
    parsed = _parse_url(raw_url)
    if product_id and (not parsed or "|PUBID|" in raw_url or _is_internal_or_api_host(parsed.netloc)):
        notes.append("product link rebuilt as direct Walmart public URL")
        return SafeLinkResult(f"https://www.walmart.com/ip/{product_id}", tuple(notes))
    if parsed and parsed.netloc.lower().endswith("walmart.com") and parsed.path.startswith("/ip/"):
        if parsed.netloc.lower() != "www.walmart.com" or parsed.scheme != "https" or parsed.query:
            notes.append("product link normalized to Walmart public URL")
        return SafeLinkResult(_public_url("www.walmart.com", parsed.path, _safe_query(parsed.query)), tuple(notes))
    if product_id:
        notes.append("product link rebuilt as direct Walmart public URL")
        return SafeLinkResult(f"https://www.walmart.com/ip/{product_id}", tuple(notes))
    return _normalize_generic(raw_url, notes=notes)


def _normalize_amazon(raw_url: str, *, asin: str | None, notes: list[str]) -> SafeLinkResult:
    parsed = _parse_url(raw_url)
    found_asin = asin or _asin_from_url(parsed.path if parsed else raw_url)
    tag = _query_value(parsed.query if parsed else "", "tag")
    if found_asin:
        query = urllib.parse.urlencode({"tag": tag}) if tag else ""
        notes.append("product link normalized to Amazon public DP URL")
        return SafeLinkResult(_public_url("www.amazon.com", f"/dp/{found_asin}", query), tuple(notes))
    return _normalize_generic(raw_url, notes=notes)


def _normalize_best_buy(raw_url: str, *, sku: str | None, notes: list[str]) -> SafeLinkResult:
    parsed = _parse_url(raw_url)
    if parsed and parsed.netloc.lower().endswith("bestbuy.com") and parsed.path.startswith("/site/"):
        if parsed.netloc.lower() != "www.bestbuy.com" or parsed.scheme != "https":
            notes.append("product link normalized to Best Buy public URL")
        return SafeLinkResult(_public_url("www.bestbuy.com", parsed.path, _safe_query(parsed.query)), tuple(notes))
    if sku:
        notes.append("product link rebuilt from Best Buy SKU")
        return SafeLinkResult(f"https://www.bestbuy.com/site/searchpage.jsp?st={urllib.parse.quote(str(sku))}", tuple(notes))
    return _normalize_generic(raw_url, notes=notes)


def _normalize_target(raw_url: str, *, tcin: str | None, notes: list[str]) -> SafeLinkResult:
    parsed = _parse_url(raw_url)
    found_tcin = tcin or _target_tcin_from_url(parsed.path if parsed else raw_url)
    if found_tcin:
        notes.append("product link normalized to Target public URL")
        return SafeLinkResult(f"https://www.target.com/p/-/A-{found_tcin}", tuple(notes))
    if parsed and parsed.netloc.lower().endswith("target.com"):
        return SafeLinkResult(_public_url("www.target.com", parsed.path, _safe_query(parsed.query)), tuple(notes))
    return _normalize_generic(raw_url, notes=notes)


def _normalize_generic(raw_url: str, *, notes: list[str]) -> SafeLinkResult:
    parsed = _parse_url(raw_url)
    if not parsed:
        return SafeLinkResult(raw_url, tuple(notes))
    if parsed.scheme != "https" and parsed.netloc:
        notes.append("product link upgraded to HTTPS")
        return SafeLinkResult(_public_url(parsed.netloc, parsed.path, _safe_query(parsed.query)), tuple(notes))
    if parsed.query:
        safe_query = _safe_query(parsed.query)
        if safe_query != parsed.query:
            notes.append("tracking-heavy product link cleaned")
            return SafeLinkResult(_public_url(parsed.netloc, parsed.path, safe_query), tuple(notes))
    return SafeLinkResult(raw_url, tuple(notes))


def _parse_url(url: str | None) -> urllib.parse.ParseResult | None:
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    if not parsed.netloc:
        return None
    return parsed


def _public_url(host: str, path: str, query: str = "") -> str:
    return urllib.parse.urlunparse(("https", host, path or "/", "", query, ""))


def _safe_query(query: str) -> str:
    if not query:
        return ""
    pairs = urllib.parse.parse_qsl(query, keep_blank_values=False)
    safe_pairs = [(key, value) for key, value in pairs if key in SAFE_QUERY_KEYS]
    return urllib.parse.urlencode(safe_pairs)


def _query_value(query: str, key: str) -> str | None:
    if not query:
        return None
    for parsed_key, value in urllib.parse.parse_qsl(query, keep_blank_values=False):
        if parsed_key == key and value:
            return value
    return None


def _is_internal_or_api_host(host: str) -> bool:
    lowered = host.lower()
    return lowered in INTERNAL_OR_API_HOSTS or lowered.startswith("api.") or ".api." in lowered


def _asin_from_url(path: str) -> str | None:
    patterns = (
        r"/(?:dp|gp/product)/([A-Z0-9]{10})(?:[/?]|$)",
        r"/([A-Z0-9]{10})(?:[/?]|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, path)
        if match:
            return match.group(1)
    return None


def _target_tcin_from_url(path: str) -> str | None:
    match = re.search(r"/A-([0-9]+)(?:[/?]|$)", path)
    if match:
        return match.group(1)
    return None


def dedupe_link_choices(choices: list[LinkChoice]) -> list[LinkChoice]:
    seen: set[str] = set()
    result: list[LinkChoice] = []
    for choice in choices:
        if not choice.url or choice.url in seen:
            continue
        seen.add(choice.url)
        result.append(choice)
    return result
