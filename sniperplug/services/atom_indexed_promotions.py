from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import aiohttp

from sniperplug.services.movie_ticket_drops import (
    ATOM_PROMOTIONS_URL,
    ATOM_REQUEST_TIMEOUT_SECONDS,
    ATOM_USER_AGENT,
    clean_text,
    normalize_text,
)


BING_SEARCH_URL = "https://www.bing.com/search"
BING_ALLOWED_HOSTS = frozenset({"bing.com", "www.bing.com"})
ATOM_INDEXED_FALLBACK_PREFIX = "indexed-fallback:"
ATOM_INDEX_MAX_RESPONSE_BYTES = 512_000
ATOM_INDEX_QUERY = "url:www.atomtickets.com/promotions"


@dataclass(frozen=True, slots=True)
class AtomIndexedConfirmation:
    canonical_url: str
    title: str
    snippet: str
    observed_at: str

    @property
    def state_marker(self) -> str:
        return f"{ATOM_INDEXED_FALLBACK_PREFIX}{self.observed_at}"

    @property
    def synthetic_html(self) -> str:
        """Return a zero-public-code Atom shell for the existing fail-closed parser.

        Indexed confirmation proves only that Bing currently exposes the exact
        canonical Atom promotions page. It is deliberately insufficient proof for
        a public reusable promo code, so this document contains Partner Promotions
        only. The current Atom parser therefore clears stale public codes instead
        of resurrecting them from month-old durable history.
        """

        snippet = escape(self.snippet[:1200])
        return (
            "<!doctype html><html><head><title>Atom Tickets Promotions</title></head>"
            "<body><h1>Atom Tickets Promotions</h1><h2>Partner Promotions</h2>"
            f"<p>Canonical Atom promotions page confirmed in a public search index at {escape(self.observed_at)}.</p>"
            f"<p>{snippet}</p></body></html>"
        )


def is_atom_indexed_fallback_marker(value: str | None) -> bool:
    return str(value or "").startswith(ATOM_INDEXED_FALLBACK_PREFIX)


def normalize_atom_canonical_url(value: str | None) -> str:
    text = clean_text(value)
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme != "https":
        return ""
    if (parsed.hostname or "").lower() not in {"atomtickets.com", "www.atomtickets.com"}:
        return ""
    path = (parsed.path or "/").rstrip("/") or "/"
    if path != "/promotions":
        return ""
    return ATOM_PROMOTIONS_URL


def parse_bing_atom_confirmation_rss(
    xml_text: str,
    *,
    observed_at: str | None = None,
) -> AtomIndexedConfirmation | None:
    """Accept only the exact canonical Atom promotions result from Bing RSS."""

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    timestamp = observed_at or datetime.now(UTC).isoformat()
    for item in root.findall(".//item"):
        title = clean_text(item.findtext("title"))
        link = normalize_atom_canonical_url(item.findtext("link"))
        description = clean_text(item.findtext("description"))
        identity = normalize_text(f"{title} {description}")
        if not link:
            continue
        if "atom" not in identity or "promotion" not in identity:
            continue
        return AtomIndexedConfirmation(
            canonical_url=link,
            title=title or "Atom Tickets Promotions",
            snippet=description,
            observed_at=timestamp,
        )
    return None


class AtomIndexedPromotionsClient:
    """Bounded public-index fallback used only when direct Atom access is blocked."""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def confirm_canonical_page(self) -> AtomIndexedConfirmation:
        headers = {
            "Accept": "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.1",
            "User-Agent": ATOM_USER_AGENT,
        }
        params = {
            "q": ATOM_INDEX_QUERY,
            "format": "rss",
            "setlang": "en-us",
        }
        timeout = aiohttp.ClientTimeout(total=ATOM_REQUEST_TIMEOUT_SECONDS, connect=5)
        async with self.session.get(
            BING_SEARCH_URL,
            params=params,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        ) as response:
            final = urlparse(str(response.url))
            if final.scheme != "https" or (final.hostname or "").lower() not in BING_ALLOWED_HOSTS:
                raise RuntimeError("Atom indexed fallback redirected outside the Bing allowlist.")
            if response.status != 200:
                raise RuntimeError(f"Atom indexed fallback search returned HTTP {response.status}.")
            payload = await response.read()
            if len(payload) > ATOM_INDEX_MAX_RESPONSE_BYTES:
                raise RuntimeError("Atom indexed fallback exceeded the safe response-size limit.")
            charset = response.charset or "utf-8"
            try:
                xml_text = payload.decode(charset, errors="replace")
            except LookupError:
                xml_text = payload.decode("utf-8", errors="replace")

        confirmation = parse_bing_atom_confirmation_rss(xml_text)
        if confirmation is None:
            raise RuntimeError(
                "Bing did not return the exact canonical Atom promotions page, so indexed fallback could not confirm it."
            )
        return confirmation
