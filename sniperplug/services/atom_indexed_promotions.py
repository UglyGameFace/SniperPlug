from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
import re
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
ATOM_DIRECT_BLOCK_MARKER = ";direct-blocked:"
ATOM_INDEX_MAX_RESPONSE_BYTES = 512_000
# Bing's documented `site:` operator searches a directory tree, unlike `url:`
# which checks one exact URL. Production proved the exact-URL query can omit the
# hub while legitimate official Atom /promotions/<slug> pages remain indexed.
ATOM_INDEX_QUERY = "site:www.atomtickets.com/promotions Atom Tickets promotion offer"
ATOM_INDEX_RECHECK_SECONDS = 30 * 60
ATOM_DIRECT_REPROBE_SECONDS = 6 * 60 * 60
_ATOM_PROMOTION_PATH_RE = re.compile(
    r"^/promotions(?:/([A-Za-z0-9][A-Za-z0-9-]{0,199}))?/?$"
)
_INDEX_IDENTITY_TERMS = frozenset({"promotion", "promo", "offer", "ticket"})


@dataclass(frozen=True, slots=True)
class AtomIndexedConfirmation:
    # Compatibility name retained from #263. This can now be either the
    # canonical hub or one strictly allowlisted official promotion detail URL.
    canonical_url: str
    title: str
    snippet: str
    observed_at: str

    @property
    def state_marker(self) -> str:
        return build_atom_indexed_fallback_marker(
            indexed_at=self.observed_at,
            direct_blocked_at=self.observed_at,
        )

    @property
    def synthetic_html(self) -> str:
        """Return a zero-public-code Atom shell for the existing fail-closed parser.

        Indexed confirmation proves only that a current Bing result points to an
        official Atom promotions URL. It is deliberately insufficient proof for
        a public reusable promo code, so this document contains Partner
        Promotions only. The current Atom parser therefore clears stale public
        codes instead of resurrecting them from search snippets or old history.
        """

        snippet = escape(self.snippet[:1200])
        official_url = escape(self.canonical_url)
        return (
            "<!doctype html><html><head><title>Atom Tickets Promotions</title></head>"
            "<body><h1>Atom Tickets Promotions</h1><h2>Partner Promotions</h2>"
            f"<p>Official Atom promotions URL confirmed in a public search index at {escape(self.observed_at)}.</p>"
            f"<p>Indexed URL: {official_url}</p>"
            f"<p>{snippet}</p></body></html>"
        )


@dataclass(frozen=True, slots=True)
class AtomIndexedFallbackState:
    indexed_at: str
    direct_blocked_at: str


def build_atom_indexed_fallback_marker(*, indexed_at: str, direct_blocked_at: str) -> str:
    return (
        f"{ATOM_INDEXED_FALLBACK_PREFIX}{clean_text(indexed_at)}"
        f"{ATOM_DIRECT_BLOCK_MARKER}{clean_text(direct_blocked_at)}"
    )


def parse_atom_indexed_fallback_marker(value: str | None) -> AtomIndexedFallbackState | None:
    text = str(value or "")
    if not text.startswith(ATOM_INDEXED_FALLBACK_PREFIX):
        return None
    payload = text[len(ATOM_INDEXED_FALLBACK_PREFIX) :]
    if ATOM_DIRECT_BLOCK_MARKER in payload:
        indexed_at, direct_blocked_at = payload.split(ATOM_DIRECT_BLOCK_MARKER, 1)
    else:
        # Backward compatibility with the first marker shape used while #263 was
        # being developed. Treat that one timestamp as both observations.
        indexed_at = payload
        direct_blocked_at = payload
    if _parse_iso(indexed_at) is None or _parse_iso(direct_blocked_at) is None:
        return None
    return AtomIndexedFallbackState(
        indexed_at=clean_text(indexed_at),
        direct_blocked_at=clean_text(direct_blocked_at),
    )


def is_atom_indexed_fallback_marker(value: str | None) -> bool:
    return parse_atom_indexed_fallback_marker(value) is not None


def atom_indexed_fallback_age_seconds(value: str | None, *, now: datetime | None = None) -> tuple[float, float] | None:
    parsed = parse_atom_indexed_fallback_marker(value)
    if parsed is None:
        return None
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    current = current.astimezone(UTC)
    indexed = _parse_iso(parsed.indexed_at)
    blocked = _parse_iso(parsed.direct_blocked_at)
    if indexed is None or blocked is None:
        return None
    return (
        max(0.0, (current - indexed).total_seconds()),
        max(0.0, (current - blocked).total_seconds()),
    )


def normalize_atom_canonical_url(value: str | None) -> str:
    """Normalize only the canonical promotions hub (legacy/public contract)."""

    normalized = normalize_atom_indexed_promotion_url(value)
    return ATOM_PROMOTIONS_URL if normalized == ATOM_PROMOTIONS_URL else ""


def normalize_atom_indexed_promotion_url(value: str | None) -> str:
    """Allow only Atom's hub or one-level official promotion detail pages.

    Search-index fallback is intentionally narrower than the general Atom URL
    allowlist. Query strings/fragments are discarded, deeper paths are rejected,
    and no redirect/off-domain URL can become issuer evidence.
    """

    text = clean_text(value)
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme != "https":
        return ""
    if (parsed.hostname or "").lower() not in {"atomtickets.com", "www.atomtickets.com"}:
        return ""
    match = _ATOM_PROMOTION_PATH_RE.fullmatch(parsed.path or "/")
    if match is None:
        return ""
    slug = match.group(1)
    if not slug:
        return ATOM_PROMOTIONS_URL
    return f"{ATOM_PROMOTIONS_URL}/{slug}"


def parse_bing_atom_confirmation_rss(
    xml_text: str,
    *,
    observed_at: str | None = None,
) -> AtomIndexedConfirmation | None:
    """Confirm an official Atom promotions URL from Bing RSS.

    Prefer the canonical hub when Bing returns it. If Bing omits the hub, accept
    a strictly allowlisted one-level `/promotions/<slug>` page. Search snippets
    are identity hints only and can never supply a reusable promo code.
    """

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    timestamp = observed_at or datetime.now(UTC).isoformat()
    detail_candidate: AtomIndexedConfirmation | None = None
    for item in root.findall(".//item"):
        title = clean_text(item.findtext("title"))
        link = normalize_atom_indexed_promotion_url(item.findtext("link"))
        description = clean_text(item.findtext("description"))
        if not link or not _indexed_identity_is_atom_promotion(title, description):
            continue
        confirmation = AtomIndexedConfirmation(
            canonical_url=link,
            title=title or "Atom Tickets Promotions",
            snippet=description,
            observed_at=timestamp,
        )
        if link == ATOM_PROMOTIONS_URL:
            return confirmation
        if detail_candidate is None:
            detail_candidate = confirmation
    return detail_candidate


class AtomIndexedPromotionsClient:
    """Bounded public-index fallback used only when direct Atom access is blocked."""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def confirm_canonical_page(self) -> AtomIndexedConfirmation:
        # Method name is retained for compatibility with #263 callers/tests. The
        # returned confirmation can now safely point at an official detail page.
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
                "Bing did not return an allowlisted official Atom promotions URL, so indexed fallback could not confirm it."
            )
        return confirmation


def _indexed_identity_is_atom_promotion(title: str, description: str) -> bool:
    identity = normalize_text(f"{title} {description}")
    if "atom" not in identity:
        return False
    return any(term in identity for term in _INDEX_IDENTITY_TERMS)


def _parse_iso(value: str | None) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
