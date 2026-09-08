from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape, unescape
from html.parser import HTMLParser
import re
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, urlparse

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
DUCKDUCKGO_SEARCH_URL = "https://html.duckduckgo.com/html/"
DUCKDUCKGO_ALLOWED_HOSTS = frozenset(
    {"html.duckduckgo.com", "duckduckgo.com", "www.duckduckgo.com"}
)
ATOM_INDEXED_FALLBACK_PREFIX = "indexed-fallback:"
ATOM_DIRECT_BLOCK_MARKER = ";direct-blocked:"
ATOM_INDEX_MAX_RESPONSE_BYTES = 512_000
# Bing's documented `site:` operator searches a directory tree, unlike `url:`
# which checks one exact URL. Production then proved Bing RSS itself can omit
# Atom entirely even while public web indexes expose live official promo pages,
# so the fallback now checks a second independent public index as well.
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
    index_provider: str = "public-index"

    @property
    def state_marker(self) -> str:
        return build_atom_indexed_fallback_marker(
            indexed_at=self.observed_at,
            direct_blocked_at=self.observed_at,
        )

    @property
    def synthetic_html(self) -> str:
        """Return a zero-public-code Atom shell for the existing fail-closed parser.

        Indexed confirmation proves only that a current public search index points
        to an official Atom promotions URL. It is deliberately insufficient proof
        for a public reusable promo code, so this document contains Partner
        Promotions only. The current Atom parser therefore clears stale public
        codes instead of resurrecting them from search snippets or old history.
        """

        snippet = escape(self.snippet[:1200])
        official_url = escape(self.canonical_url)
        provider = escape(clean_text(self.index_provider) or "public index")
        return (
            "<!doctype html><html><head><title>Atom Tickets Promotions</title></head>"
            "<body><h1>Atom Tickets Promotions</h1><h2>Partner Promotions</h2>"
            f"<p>Official Atom promotions URL confirmed in {provider} at {escape(self.observed_at)}.</p>"
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


def atom_indexed_fallback_age_seconds(
    value: str | None,
    *,
    now: datetime | None = None,
) -> tuple[float, float] | None:
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
    """Confirm an official Atom promotions URL from Bing RSS."""

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
            index_provider="Bing public index",
        )
        if link == ATOM_PROMOTIONS_URL:
            return confirmation
        if detail_candidate is None:
            detail_candidate = confirmation
    return detail_candidate


class _IndexAnchorParser(HTMLParser):
    """Collect candidate search-result anchors without trusting search snippets."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[tuple[str, str]] = []
        self._href = ""
        self._parts: list[str] = []
        self._capture = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        values = {str(key).lower(): str(value or "") for key, value in attrs}
        href = clean_text(values.get("href"))
        if not href:
            return
        classes = set(values.get("class", "").split())
        # DuckDuckGo HTML marks actual result titles with result__a. Keep the
        # parser narrowly bound to that result surface rather than every link.
        if "result__a" not in classes:
            return
        self._href = href
        self._parts = []
        self._capture = True

    def handle_data(self, data: str) -> None:
        if self._capture:
            text = clean_text(data)
            if text:
                self._parts.append(text)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._capture:
            return
        self.results.append((self._href, clean_text(unescape(" ".join(self._parts)))))
        self._href = ""
        self._parts = []
        self._capture = False


def parse_duckduckgo_atom_confirmation_html(
    html_text: str,
    *,
    observed_at: str | None = None,
) -> AtomIndexedConfirmation | None:
    """Confirm an allowlisted official Atom promo URL from DuckDuckGo HTML."""

    parser = _IndexAnchorParser()
    try:
        parser.feed(html_text)
        parser.close()
    except (TypeError, ValueError):
        return None

    timestamp = observed_at or datetime.now(UTC).isoformat()
    detail_candidate: AtomIndexedConfirmation | None = None
    for raw_href, title in parser.results:
        target = _decode_duckduckgo_result_url(raw_href)
        link = normalize_atom_indexed_promotion_url(target)
        if not link or not _indexed_identity_is_atom_promotion(title, ""):
            continue
        confirmation = AtomIndexedConfirmation(
            canonical_url=link,
            title=title or "Atom Tickets Promotions",
            snippet="",
            observed_at=timestamp,
            index_provider="DuckDuckGo public index",
        )
        if link == ATOM_PROMOTIONS_URL:
            return confirmation
        if detail_candidate is None:
            detail_candidate = confirmation
    return detail_candidate


class AtomIndexedPromotionsClient:
    """Bounded multi-index fallback used only when direct Atom access is blocked."""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session

    async def confirm_canonical_page(self) -> AtomIndexedConfirmation:
        # Compatibility method name retained from #263. Production has now shown
        # that Bing RSS can omit Atom entirely, so no single index is authoritative.
        errors: list[str] = []
        try:
            return await self._confirm_bing_rss()
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError) as error:
            errors.append(f"Bing: {clean_text(str(error)) or type(error).__name__}")

        try:
            return await self._confirm_duckduckgo_html()
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError) as error:
            errors.append(f"DuckDuckGo: {clean_text(str(error)) or type(error).__name__}")

        detail = "; ".join(errors)[:1200]
        raise RuntimeError(
            "No public index returned an allowlisted official Atom promotions URL"
            + (f" ({detail})." if detail else ".")
        )

    async def _confirm_bing_rss(self) -> AtomIndexedConfirmation:
        headers = {
            "Accept": "application/rss+xml,application/xml,text/xml;q=0.9,*/*;q=0.1",
            "User-Agent": ATOM_USER_AGENT,
        }
        params = {
            "q": ATOM_INDEX_QUERY,
            "format": "rss",
            "setlang": "en-us",
        }
        xml_text = await self._fetch_text(
            BING_SEARCH_URL,
            params=params,
            headers=headers,
            allowed_hosts=BING_ALLOWED_HOSTS,
            provider_name="Bing",
        )
        confirmation = parse_bing_atom_confirmation_rss(xml_text)
        if confirmation is None:
            raise RuntimeError("no allowlisted Atom promotions result")
        return confirmation

    async def _confirm_duckduckgo_html(self) -> AtomIndexedConfirmation:
        headers = {
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
            "User-Agent": ATOM_USER_AGENT,
        }
        params = {
            "q": ATOM_INDEX_QUERY,
            "kl": "us-en",
        }
        html_text = await self._fetch_text(
            DUCKDUCKGO_SEARCH_URL,
            params=params,
            headers=headers,
            allowed_hosts=DUCKDUCKGO_ALLOWED_HOSTS,
            provider_name="DuckDuckGo",
        )
        confirmation = parse_duckduckgo_atom_confirmation_html(html_text)
        if confirmation is None:
            raise RuntimeError("no allowlisted Atom promotions result")
        return confirmation

    async def _fetch_text(
        self,
        url: str,
        *,
        params: dict[str, str],
        headers: dict[str, str],
        allowed_hosts: frozenset[str],
        provider_name: str,
    ) -> str:
        timeout = aiohttp.ClientTimeout(total=ATOM_REQUEST_TIMEOUT_SECONDS, connect=5)
        async with self.session.get(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
            allow_redirects=True,
        ) as response:
            final = urlparse(str(response.url))
            if final.scheme != "https" or (final.hostname or "").lower() not in allowed_hosts:
                raise RuntimeError(f"{provider_name} redirected outside its allowlist")
            if response.status != 200:
                raise RuntimeError(f"{provider_name} search returned HTTP {response.status}")
            payload = await response.read()
            if len(payload) > ATOM_INDEX_MAX_RESPONSE_BYTES:
                raise RuntimeError(f"{provider_name} search exceeded the safe response-size limit")
            charset = response.charset or "utf-8"
            try:
                return payload.decode(charset, errors="replace")
            except LookupError:
                return payload.decode("utf-8", errors="replace")


def _decode_duckduckgo_result_url(value: str | None) -> str:
    text = clean_text(value)
    if not text:
        return ""
    if text.startswith("//"):
        text = f"https:{text}"
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower()
    if host in {"duckduckgo.com", "www.duckduckgo.com", "html.duckduckgo.com"} and parsed.path.startswith("/l/"):
        values = parse_qs(parsed.query)
        target = values.get("uddg", [""])[0]
        return clean_text(target)
    return text


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
